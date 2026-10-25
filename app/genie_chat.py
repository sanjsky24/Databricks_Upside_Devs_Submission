"""Ask DengueRadar: the app's chat box, answered by the DengueRadar Genie space
(scripts/build_genie_space.py).

POST /api/ask                       {"question": "...", "conversation_id": optional}
                                    -> {"conversation_id", "message_id"}
GET  /api/ask/{conversation}/{msg}  -> {"status", "done"} while Genie works, then also
                                       {"text", "sql", "columns", "rows", "row_count", "suggestions", "error"}

The page polls the GET so a slow answer never hits the Apps proxy timeout. Genie writes and runs the
SQL as the app's service principal, which needs CAN RUN on the space, CAN USE on the space's SQL
warehouse and SELECT on its tables. Set GENIE_SPACE_ID to skip looking the space up by title.
"""

import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

TITLE = "DengueRadar Genie"
MAX_ROWS = 100
DONE = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}
ID = re.compile(r"^[0-9a-f-]{8,64}$")

router = APIRouter()
_state = {"client": None, "space_id": os.environ.get("GENIE_SPACE_ID")}


def _client():
    if _state["client"] is None:
        from databricks.sdk import WorkspaceClient

        _state["client"] = WorkspaceClient()
    return _state["client"]


def _api(method, path, body=None, query=None):
    return _client().api_client.do(method, path, body=body, query=query)


def _space():
    if not _state["space_id"]:
        token = None
        while True:
            r = _api("GET", "/api/2.0/genie/spaces", query={"page_token": token} if token else None)
            match = [s["space_id"] for s in r.get("spaces", []) if s.get("title") == TITLE]
            if match:
                _state["space_id"] = match[0]
                break
            token = r.get("next_page_token")
            if not token:
                raise RuntimeError(f"No Genie space titled '{TITLE}' is shared with this app.")
    return f"/api/2.0/genie/spaces/{_state['space_id']}"


def _error(e, status=502):
    return JSONResponse({"error": str(e) or e.__class__.__name__, "done": True}, status_code=status)


class Question(BaseModel):
    question: str
    conversation_id: str | None = None


@router.post("/api/ask")
def ask(q: Question):
    text = q.question.strip()[:1000]
    if not text:
        return _error("Type a question first.", 400)
    if q.conversation_id and not ID.match(q.conversation_id):
        return _error("Bad conversation id.", 400)
    try:
        base = _space()
        if q.conversation_id:
            m = _api("POST", f"{base}/conversations/{q.conversation_id}/messages", body={"content": text})
            conv = q.conversation_id
        else:
            m = _api("POST", f"{base}/start-conversation", body={"content": text})
            conv = m.get("conversation_id") or m["message"]["conversation_id"]
        msg = m.get("message_id") or m.get("id") or m["message"]["id"]
        return {"conversation_id": conv, "message_id": msg}
    except Exception as e:  # shown to the user in the chat
        return _error(e)


@router.get("/api/ask/{conversation_id}/{message_id}")
def answer(conversation_id: str, message_id: str):
    if not (ID.match(conversation_id) and ID.match(message_id)):
        return _error("Bad id.", 400)
    try:
        base = _space()
        path = f"{base}/conversations/{conversation_id}/messages/{message_id}"
        m = _api("GET", path)
        status = m.get("status", "")
        if status not in DONE:
            return {"status": status, "done": False}

        out = {"status": status, "done": True, "text": "", "sql": "", "description": "", "columns": [], "rows": [],
               "row_count": 0, "suggestions": [], "error": ""}
        if status != "COMPLETED":
            err = m.get("error") or {}
            out["error"] = err.get("error") or err.get("message") or f"Genie stopped ({status.lower()})."
        for a in m.get("attachments") or []:
            if a.get("text"):
                out["text"] = a["text"].get("content", "")
            if a.get("suggested_questions"):
                out["suggestions"] = a["suggested_questions"].get("questions", [])[:3]
            if a.get("query"):
                out["sql"] = a["query"].get("query", "")
                out["description"] = a["query"].get("description", "")
                r = _api("GET", f"{path}/attachments/{a['attachment_id']}/query-result").get("statement_response", {})
                manifest = r.get("manifest", {})
                out["columns"] = [c["name"] for c in manifest.get("schema", {}).get("columns", [])]
                rows = (r.get("result") or {}).get("data_array") or []
                out["row_count"] = manifest.get("total_row_count", len(rows))
                out["rows"] = rows[:MAX_ROWS]
        return out
    except Exception as e:
        return _error(e)
