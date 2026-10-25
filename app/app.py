"""DengueRadar app: the Monday screen, served from Lakebase.

GET  /            the page (static/index.html)
GET  /api/data    everything the page shows, as one JSON document
POST /api/draft   draft an alert for an area or a team site with the LLM (alerts.py)
GET  /api/alerts  the alert log; POST /api/alerts saves a draft or marks one sent
GET  /api/me      who is signed in (Databricks Apps sign-in; no passwords of our own)
POST /api/ask     "Ask DengueRadar", answered by the Genie space (genie_chat.py)

Data lives in Lakebase Postgres, schema `dengueradar`, written by notebooks/05_publish_app_data.py.
The alert log is the app's own table, dengueradar_app.alerts, so officers see what was already sent.

On Databricks Apps the database resource sets PGHOST / PGDATABASE / PGUSER / PGPORT, and the app's
service principal signs in with a short-lived OAuth token (pg8000, a pure-Python driver).

Local preview without Lakebase: DENGUERADAR_SAMPLE=/path/to/data.json python app.py
"""

import json
import os
import time
from datetime import date, datetime

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import alerts
import genie_chat

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = ["meta", "areas", "wolbachia_zones", "high_aedes", "clusters", "blocks", "hdb_blocks", "team_plan",
          "track_record", "retro_check", "test_headline", "test_by_year", "weekly_cases"]
GEOMETRY = {"areas", "wolbachia_zones"}
CACHE_SECONDS = 300

app = FastAPI(title="DengueRadar")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
app.include_router(genie_chat.router)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # /api/data is ~3.5 MB of JSON
_cache = {"at": 0.0, "data": None}


def _connect():
    import ssl

    from databricks.sdk import WorkspaceClient
    from pg8000.native import Connection  # pure Python, no libpq to clash with

    w = WorkspaceClient()
    host, user = os.environ.get("PGHOST"), os.environ.get("PGUSER")
    if not host:  # resource variables missing: look the instance up by name
        host = w.database.get_database_instance(name=os.environ.get("LAKEBASE_INSTANCE", "dengueradar")).read_write_dns
    if not user:
        user = w.config.client_id or w.current_user.me().user_name
    return Connection(user=user, host=host, port=int(os.environ.get("PGPORT", "5432")),
                      database=os.environ.get("PGDATABASE", "databricks_postgres"),
                      password=w.config.oauth_token().access_token, ssl_context=ssl.create_default_context())


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _load():
    sample = os.environ.get("DENGUERADAR_SAMPLE")
    if sample:
        with open(sample) as f:
            return json.load(f)
    out = {}
    conn = _connect()
    try:
        for t in TABLES:
            result = conn.run(f"SELECT * FROM dengueradar.{t}")
            cols = [c["name"] for c in conn.columns]
            rows = [dict(zip(cols, map(_plain, r))) for r in result]
            if t in GEOMETRY:
                for r in rows:
                    r["geometry"] = json.loads(r["geometry"]) if r.get("geometry") else None
            out[t] = rows
    finally:
        conn.close()
    out["meta"] = out["meta"][0] if out["meta"] else {}
    return out


def _data():
    if _cache["data"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
        _cache["data"], _cache["at"] = _load(), time.time()
    return _cache["data"]


@app.get("/api/data")
def data():
    return JSONResponse(_data())


# ------------------------------------------------------------------ alerts

# the log shows names, never email addresses
ALERT_COLUMNS = ["id", "created_at", "created_by_name", "area", "scope", "audience", "channel", "language",
                 "status", "homes", "blocks", "message"]
# accounts the whole team signs in with: the app can't tell people apart, so it asks for a name
SHARED_ACCOUNTS = {e.strip().lower() for e in os.environ.get("SHARED_ACCOUNTS", "upsidedevsdaisi@gmail.com").split(",")}
_sample_alerts = []  # local preview only


def _alerts_conn():
    conn = _connect()
    conn.run("CREATE SCHEMA IF NOT EXISTS dengueradar_app")
    conn.run("""CREATE TABLE IF NOT EXISTS dengueradar_app.alerts (
                  id bigserial PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), created_by text,
                  area text NOT NULL, scope text, audience text, channel text, language text,
                  status text NOT NULL, homes integer, blocks integer, message text NOT NULL)""")
    conn.run("ALTER TABLE dengueradar_app.alerts ADD COLUMN IF NOT EXISTS created_by_name text")
    return conn


def _identity(request):
    email = (request.headers.get("x-forwarded-email") or "").strip()
    user = (request.headers.get("x-forwarded-preferred-username") or "").strip()
    return email, user


@app.get("/api/me")
def me(request: Request):
    """Databricks Apps signs everyone in with their Databricks account and passes it in these headers."""
    email, user = _identity(request)
    handle = (email or user).split("@")[0]
    guess = " ".join(w.capitalize() for w in handle.replace(".", " ").replace("_", " ").split() if not w.isdigit())
    return {"signed_in": bool(email or user), "account": email or user,
            "shared": (email or user).lower() in SHARED_ACCOUNTS, "suggested_name": guess}


@app.post("/api/draft")
def draft(body: dict = Body(...)):  # plain def: FastAPI runs it in a thread, so the LLM call doesn't block others
    audience, channel, language = body.get("audience"), body.get("channel"), body.get("language")
    if audience not in alerts.AUDIENCES or channel not in alerts.CHANNELS or language not in alerts.LANGUAGES:
        raise HTTPException(400, "unknown audience, channel or language")
    try:
        f = alerts.facts(_data(), body.get("area"), body.get("site"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from databricks.sdk import WorkspaceClient
    text, unverified = alerts.draft(WorkspaceClient(), f, audience, channel, language)
    return {"message": text, "unverified_numbers": unverified, "facts": f}


@app.get("/api/alerts")
def list_alerts():
    if os.environ.get("DENGUERADAR_SAMPLE"):
        return _sample_alerts[::-1]
    conn = _alerts_conn()
    try:
        rows = conn.run(f"SELECT {', '.join(ALERT_COLUMNS)} FROM dengueradar_app.alerts ORDER BY created_at DESC LIMIT 100")
    finally:
        conn.close()
    return [dict(zip(ALERT_COLUMNS, map(_plain, r))) for r in rows]


@app.post("/api/alerts")
def save_alert(request: Request, b: dict = Body(...)):
    if b.get("status") not in ("draft", "sent") or not b.get("area") or not (b.get("message") or "").strip():
        raise HTTPException(400, "need area, message and status draft|sent")
    row = {k: b.get(k) for k in ("area", "scope", "audience", "channel", "language", "status", "homes", "blocks")}
    row["message"] = b["message"][:5000]
    email, user = _identity(request)
    row["created_by"] = email or user  # kept for audit, never sent back to the page
    row["created_by_name"] = (b.get("created_by_name") or "").strip()[:80] or None
    if os.environ.get("DENGUERADAR_SAMPLE"):
        row.update(id=len(_sample_alerts) + 1, created_at=datetime.now().isoformat())
        _sample_alerts.append({k: row.get(k) for k in ALERT_COLUMNS})
        return _sample_alerts[-1]
    conn = _alerts_conn()
    try:
        r = conn.run("""INSERT INTO dengueradar_app.alerts
                          (created_by, created_by_name, area, scope, audience, channel, language, status, homes, blocks, message)
                        VALUES (:created_by, :created_by_name, :area, :scope, :audience, :channel, :language, :status,
                                :homes, :blocks, :message)
                        RETURNING id, created_at""", **row)
    finally:
        conn.close()
    out = {k: row.get(k) for k in ALERT_COLUMNS}
    return {**out, "id": r[0][0], "created_at": _plain(r[0][1])}


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")))
