"""DengueRadar app: the Monday screen, served from Lakebase.

GET /            the page (static/index.html)
GET /api/data    everything the page shows, as one JSON document

Data lives in Lakebase Postgres, schema `dengueradar`, written by notebooks/05_publish_app_data.py.
On Databricks Apps the database resource sets PGHOST / PGDATABASE / PGUSER / PGPORT, and the app's
service principal signs in with a short-lived OAuth token (pg8000, a pure-Python driver).

Local preview without Lakebase: DENGUERADAR_SAMPLE=/path/to/data.json python app.py
"""

import json
import os
import time
from datetime import date, datetime

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = ["meta", "areas", "wolbachia_zones", "high_aedes", "clusters", "blocks", "team_plan",
          "track_record", "test_headline", "test_by_year", "weekly_cases"]
GEOMETRY = {"areas", "wolbachia_zones"}
CACHE_SECONDS = 300

app = FastAPI(title="DengueRadar")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
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


@app.get("/api/data")
def data():
    if _cache["data"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
        _cache["data"], _cache["at"] = _load(), time.time()
    return JSONResponse(_cache["data"])


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")))
