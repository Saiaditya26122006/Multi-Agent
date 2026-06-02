"""
FastAPI web server for multi-agent system chat interface.
Provides REST + WebSocket endpoints so the CEO can use
the same pipeline from a browser, synced with Telegram.
"""

import os
import logging
import asyncio
import uuid
import json
from typing import Dict, Set
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Chat")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

WEB_AUTH_TOKEN = os.getenv("WEB_AUTH_TOKEN", "changeme")


class ConnectionManager:
    """Manages active WebSocket connections per session."""

    def __init__(self) -> None:
        self.active: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_key: str) -> None:
        """Accept connection and register it."""
        await websocket.accept()
        if session_key not in self.active:
            self.active[session_key] = set()
        self.active[session_key].add(websocket)
        logger.info(f"WebSocket connected for session_key={session_key}")

    def disconnect(self, websocket: WebSocket, session_key: str) -> None:
        """Remove connection from registry."""
        if session_key in self.active:
            self.active[session_key].discard(websocket)
            if not self.active[session_key]:
                del self.active[session_key]

    async def broadcast(self, session_key: str, message: dict) -> None:
        """Send a message to all connections for a session_key."""
        if session_key not in self.active:
            return
        dead = []
        for ws in self.active[session_key]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active[session_key].discard(ws)


manager = ConnectionManager()

_pipeline_handler = None


def set_pipeline_handler(handler):
    """Register the pipeline handler function from main.py."""
    global _pipeline_handler
    _pipeline_handler = handler


class SendMessageRequest(BaseModel):
    """Payload for POST /api/messages."""

    text: str
    token: str


class AddFactRequest(BaseModel):
    """Payload for POST /api/knowledge-base/add."""

    topic: str
    fact: str
    status: str
    token: str


@app.get("/")
async def index():
    """Serve the chat HTML page."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/session-key")
async def get_session_key(token: str = ""):
    """Return the CEO's session key (telegram_chat_id) for WebSocket connection."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from memory.supabase_client import get_ceo_context

    ceo_context = get_ceo_context()
    if not ceo_context:
        raise HTTPException(status_code=500, detail="No CEO context configured")

    return {"session_key": str(ceo_context.get("telegram_chat_id"))}


@app.get("/api/messages/{session_key}")
async def get_messages(session_key: str, token: str = ""):
    """Fetch all messages for a given session_key (telegram_chat_id)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from memory.supabase_client import supabase

    try:
        sessions_resp = (
            supabase.table("sessions")
            .select("id")
            .eq("telegram_chat_id", int(session_key))
            .execute()
        )
        if not sessions_resp.data:
            return {"messages": []}

        session_ids = [s["id"] for s in sessions_resp.data]

        msgs_resp = (
            supabase.table("messages")
            .select("*")
            .in_("session_id", session_ids)
            .order("received_at", desc=False)
            .execute()
        )
        return {"messages": msgs_resp.data or []}
    except Exception as e:
        logger.error(f"Error fetching messages: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch messages")


@app.post("/api/messages")
async def post_message(req: SendMessageRequest):
    """Receive a message from the web client and push it through the pipeline."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    if _pipeline_handler is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    from memory.supabase_client import get_ceo_context

    ceo_context = get_ceo_context()
    if not ceo_context:
        raise HTTPException(status_code=500, detail="No CEO context configured")

    chat_id = ceo_context.get("telegram_chat_id")

    message_data = {
        "message_id": f"web_{uuid.uuid4().hex[:12]}",
        "chat_id": chat_id,
        "text": req.text.strip(),
        "channel": "web",
        "from_user": {
            "id": chat_id,
            "username": "ceo_web",
            "first_name": ceo_context.get("name", "CEO"),
        },
    }

    session_key = str(chat_id)
    await manager.broadcast(
        session_key,
        {
            "role": "user",
            "text": req.text.strip(),
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "web",
        },
    )

    await manager.broadcast(
        session_key,
        {"role": "status", "text": "Thinking...", "timestamp": datetime.utcnow().isoformat()},
    )

    asyncio.create_task(_run_pipeline_with_status(message_data, session_key))

    return {"status": "queued", "session_key": session_key}


async def _run_pipeline_with_status(message_data: dict, session_key: str) -> None:
    """Run pipeline and clear typing indicator when done."""
    try:
        await _pipeline_handler(message_data)
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await manager.broadcast(
            session_key,
            {"role": "assistant", "text": "Something went wrong. Please try again.",
             "timestamp": datetime.utcnow().isoformat(), "channel": "system"},
        )
    finally:
        await manager.broadcast(
            session_key,
            {"role": "status", "text": "", "timestamp": datetime.utcnow().isoformat()},
        )


@app.websocket("/ws/{session_key}")
async def websocket_endpoint(websocket: WebSocket, session_key: str):
    """WebSocket for real-time message streaming."""
    token = websocket.query_params.get("token", "")
    if token != WEB_AUTH_TOKEN:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket, session_key)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_key)


@app.get("/api/knowledge-base")
async def get_knowledge_base(token: str = ""):
    """Return all CEO knowledge base data with epistemic tags."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from ceo_data.loader import load_all_ceo_data

    try:
        data = load_all_ceo_data()
        return {"topics": data}
    except Exception as e:
        logger.error(f"Error loading knowledge base: {e}")
        raise HTTPException(status_code=500, detail="Failed to load knowledge base")


@app.post("/api/knowledge-base/add")
async def add_knowledge_fact(req: AddFactRequest):
    """Add a new fact to the knowledge base."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not req.fact.strip():
        raise HTTPException(status_code=400, detail="Fact cannot be empty")

    if req.status not in ["CONFIRMED", "ASSUMPTION", "INFERRED", "CONTRADICTION"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    ceo_data_dir = Path(__file__).parent.parent / "ceo_data"
    topic_file = ceo_data_dir / f"{req.topic}.json"

    try:
        if topic_file.exists():
            with open(topic_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {
                "_meta": {
                    "source": "Web Interface",
                    "created": datetime.utcnow().isoformat(),
                    "last_updated": datetime.utcnow().isoformat(),
                },
                "facts": [],
            }

        if "facts" not in data:
            data["facts"] = []

        new_fact = {
            "fact": req.fact.strip(),
            "status": req.status,
            "added_at": datetime.utcnow().isoformat(),
        }
        data["facts"].append(new_fact)

        if "_meta" in data:
            data["_meta"]["last_updated"] = datetime.utcnow().isoformat()

        with open(topic_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Added fact to {req.topic}: {req.fact[:50]}...")
        return {"success": True, "topic": req.topic, "fact_added": req.fact}

    except Exception as e:
        logger.error(f"Error adding fact: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add fact: {str(e)}")
