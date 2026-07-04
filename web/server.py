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

from services.conversation_store import store_ceo_message
from web.workspace_router import Workspace, dispatch as workspace_dispatch, get_workspace, set_workspace
from web.menu_generator import generate_main_menu, generate_sub_menu, format_menu_as_text, format_sub_menu_as_text

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


def _handle_workspace_message(workspace: Workspace, text: str, session_id: str) -> str:
    """Dispatch a message to the active workspace handler.

    Returns response text, or empty string if not handled.
    """
    text_lower = text.strip().lower()

    if workspace == Workspace.INSPECT:
        return _dispatch_inspect(text_lower, text, session_id)
    elif workspace == Workspace.BUILD:
        return _dispatch_build(text_lower, text, session_id)
    elif workspace == Workspace.FEED:
        return _dispatch_feed(text_lower, text, session_id)
    elif workspace == Workspace.CHALLENGE:
        return _dispatch_challenge(text_lower, text, session_id)
    elif workspace == Workspace.VALIDATE:
        return _dispatch_validate(text_lower, text, session_id)
    elif workspace == Workspace.EXPORT:
        return _dispatch_export(text_lower, text, session_id)

    return ""


def _dispatch_inspect(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.inspect_handler import (
        get_coverage_heatmap,
        get_confidence_breakdown,
        get_contradictions_list,
        get_stale_data_report,
        get_dependency_view,
        get_section_deep_dive,
        answer_inspect_question,
        format_inspect_response,
    )

    commands = {
        "a": ("heatmap", lambda: get_coverage_heatmap()),
        "b": ("confidence", lambda: get_confidence_breakdown()),
        "c": ("contradictions", lambda: get_contradictions_list()),
        "d": ("stale", lambda: get_stale_data_report()),
        "e": ("dependencies", lambda: get_dependency_view()),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_inspect_response(data, query_type)
        except Exception as e:
            logger.error("[Inspect] Error handling '%s': %s", text_lower, e)
            return f"Error running {query_type} analysis: {e}"

    if text_lower == "f":
        return (
            "Which section would you like to deep-dive into?\n\n"
            "Type a section name (e.g. 'opportunity', 'financials', 'marketing') "
            "or ask a specific question."
        )

    try:
        result = answer_inspect_question(text)
        return format_inspect_response(result, "question")
    except Exception as e:
        logger.error("[Inspect] Error answering question: %s", e)
        return f"Error: {e}"


def _dispatch_build(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.build_handler import (
        build_full_plan,
        build_section,
        build_incremental,
        build_weak_sections,
        get_build_status,
        format_build_response,
    )

    commands = {
        "a": ("full_plan", lambda: build_full_plan(session_id)),
        "b": ("section", lambda: get_build_status()),
        "c": ("incremental", lambda: build_incremental(session_id)),
        "d": ("weak", lambda: build_weak_sections(session_id=session_id)),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_build_response(data)
        except Exception as e:
            logger.error("[Build] Error handling '%s': %s", text_lower, e)
            return f"Error running build command: {e}"

    return ""


def _dispatch_feed(text_lower: str, text: str, session_id: str) -> str:
    import traceback
    from web.handlers.feed_handler import handle_feed_message

    if len(text.strip()) > 0:
        try:
            response = handle_feed_message(text, session_id)
            if not response:
                logger.warning("[Feed] handle_feed_message returned empty for input: %s", text[:80])
                return "Received your input but couldn't generate a response. Try again or type 'back' for the menu."
            return response
        except Exception as e:
            logger.error("[Feed] Error handling input: %s\n%s", e, traceback.format_exc())
            return f"Error processing input: {e}"

    return ""


def _dispatch_challenge(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.challenge_handler import (
        challenge_weakest_assumptions,
        challenge_section,
        challenge_claim,
        challenge_full_plan,
        get_vulnerability_list,
        format_challenge_response,
    )

    commands = {
        "a": ("weakest", lambda: challenge_weakest_assumptions()),
        "b": ("full_plan", lambda: challenge_full_plan()),
        "c": ("vulnerabilities", lambda: get_vulnerability_list()),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_challenge_response(data)
        except Exception as e:
            logger.error("[Challenge] Error handling '%s': %s", text_lower, e)
            return f"Error running challenge: {e}"

    try:
        result = challenge_claim(text)
        return format_challenge_response(result)
    except Exception as e:
        logger.error("[Challenge] Error challenging claim: %s", e)
        return f"Error: {e}"


def _dispatch_validate(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.validate_handler import (
        get_assumption_queue,
        format_validate_response,
    )

    if text_lower == "a":
        try:
            data = get_assumption_queue()
            return format_validate_response(data)
        except Exception as e:
            logger.error("[Validate] Error: %s", e)
            return f"Error: {e}"

    return ""


def _dispatch_export(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.export_handler import (
        export_full_plan,
        export_executive_summary,
        export_gap_report,
        get_export_readiness,
        format_export_response,
    )

    commands = {
        "a": ("full", lambda: export_full_plan(session_id)),
        "b": ("summary", lambda: export_executive_summary(session_id)),
        "c": ("gaps", lambda: export_gap_report(session_id)),
        "d": ("readiness", lambda: get_export_readiness()),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_export_response(data)
        except Exception as e:
            logger.error("[Export] Error handling '%s': %s", text_lower, e)
            return f"Error: {e}"

    return ""


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
    """Return the CEO's session key (chat_id) for WebSocket connection."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from memory.supabase_client import get_ceo_context

        ceo_context = get_ceo_context()
        if ceo_context and ceo_context.get("chat_id"):
            return {"session_key": str(ceo_context.get("chat_id"))}
    except Exception as e:
        logger.warning(f"Could not get CEO context: {e}")

    return {"session_key": "1"}


@app.get("/api/messages/{session_key}")
async def get_messages(session_key: str, token: str = ""):
    """Fetch all messages for a given session_key (chat_id)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from memory.supabase_client import supabase

    try:
        sessions_resp = (
            supabase.table("sessions")
            .select("id")
            .eq("chat_id", int(session_key))
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

    try:
        from memory.supabase_client import get_ceo_context

        ceo_context = get_ceo_context()
    except Exception:
        ceo_context = None

    if not ceo_context:
        ceo_context = {"chat_id": 1, "name": "Alex"}

    chat_id = ceo_context.get("chat_id", 1)
    session_key = str(chat_id)

    routed = workspace_dispatch(session_key, req.text.strip())

    if routed["action"] == "show_menu":
        menu = generate_main_menu()
        menu_text = format_menu_as_text(menu)
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
            {
                "role": "assistant",
                "text": menu_text,
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "system",
                "workspace_action": "show_menu",
                "menu_data": menu,
            },
        )
        return {"status": "menu_shown", "session_key": session_key}

    if routed["action"] == "switch_workspace":
        ws = routed["workspace"]
        sub_menu = generate_sub_menu(ws)
        sub_text = format_sub_menu_as_text(sub_menu)
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
            {
                "role": "assistant",
                "text": sub_text,
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "system",
                "workspace_action": "switch_workspace",
                "workspace": ws.value,
                "sub_menu_data": sub_menu,
            },
        )
        return {"status": "workspace_switched", "workspace": ws.value, "session_key": session_key}

    await manager.broadcast(
        session_key,
        {
            "role": "user",
            "text": req.text.strip(),
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "web",
        },
    )

    current_ws = routed["workspace"]

    if current_ws == Workspace.AUTO:
        from web.handlers.feed_handler import get_feed_state
        feed_state = get_feed_state(session_key)
        if feed_state and feed_state.startswith("FEED_AWAITING"):
            response_text = _dispatch_feed("", req.text.strip(), session_key)
            if response_text:
                await manager.broadcast(
                    session_key,
                    {
                        "role": "assistant",
                        "text": response_text,
                        "timestamp": datetime.utcnow().isoformat(),
                        "channel": "system",
                        "workspace": "feed",
                    },
                )
                return {"status": "feed_approval", "session_key": session_key, "workspace": "feed"}

    if current_ws != Workspace.AUTO:
        response_text = _handle_workspace_message(current_ws, req.text.strip(), session_key)
        if response_text:
            await manager.broadcast(
                session_key,
                {
                    "role": "assistant",
                    "text": response_text,
                    "timestamp": datetime.utcnow().isoformat(),
                    "channel": "system",
                    "workspace": current_ws.value,
                },
            )
            return {"status": "workspace_handled", "session_key": session_key, "workspace": current_ws.value}

    if _pipeline_handler is None:
        from web.handlers.auto_handler import handle_auto_message, format_auto_response

        result = handle_auto_message(req.text.strip(), session_id=session_key)
        response_text = format_auto_response(result)

        await manager.broadcast(
            session_key,
            {
                "role": "assistant",
                "text": response_text,
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "system",
                "workspace": current_ws.value,
            },
        )
        return {"status": "handled_locally", "session_key": session_key, "workspace": current_ws.value}

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

    store_ceo_message(
        message=req.text.strip(),
        session_id=message_data["message_id"],
        channel="web",
        metadata={"chat_id": chat_id, "message_id": message_data["message_id"]},
    )

    await manager.broadcast(
        session_key,
        {"role": "status", "text": "Thinking...", "timestamp": datetime.utcnow().isoformat()},
    )

    asyncio.create_task(_run_pipeline_with_status(message_data, session_key))

    return {"status": "queued", "session_key": session_key, "workspace": routed["workspace"].value}


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


class WorkspaceSwitchRequest(BaseModel):
    """Payload for POST /api/workspace/switch."""

    workspace: str
    token: str


class ExportRequest(BaseModel):
    """Payload for POST /api/export/generate."""

    format: str
    token: str


@app.get("/api/dashboard")
async def get_dashboard(token: str = ""):
    """Return dashboard statistics: coverage, confidence, contradictions, stale."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.coverage_calculator import get_dashboard_stats

        stats = get_dashboard_stats()
        return stats
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute dashboard stats")


@app.get("/api/recommendation")
async def get_recommendation(token: str = ""):
    """Return the current highest-leverage action recommendation."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.recommendation_engine import get_highest_leverage_action

        return get_highest_leverage_action()
    except Exception as e:
        logger.error(f"Error getting recommendation: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute recommendation")


@app.get("/api/menu")
async def get_menu(token: str = ""):
    """Return the main menu with live badges and stats."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        menu = generate_main_menu()
        menu["formatted_text"] = format_menu_as_text(menu)
        return menu
    except Exception as e:
        logger.error(f"Error generating menu: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate menu")


@app.get("/api/menu/{workspace_id}")
async def get_workspace_menu(workspace_id: str, token: str = ""):
    """Return the sub-menu for a specific workspace."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        ws = Workspace(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid workspace: {workspace_id}")

    try:
        sub_menu = generate_sub_menu(ws)
        sub_menu["formatted_text"] = format_sub_menu_as_text(sub_menu)
        return sub_menu
    except Exception as e:
        logger.error(f"Error generating sub-menu: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate sub-menu")


@app.post("/api/workspace/switch")
async def switch_workspace(req: WorkspaceSwitchRequest):
    """Switch the active workspace for the current session."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        ws = Workspace(req.workspace)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid workspace: {req.workspace}")

    from memory.supabase_client import get_ceo_context

    ceo_context = get_ceo_context()
    if not ceo_context:
        raise HTTPException(status_code=500, detail="No CEO context configured")

    session_key = str(ceo_context.get("chat_id"))
    set_workspace(session_key, ws)

    sub_menu = generate_sub_menu(ws)
    sub_menu["formatted_text"] = format_sub_menu_as_text(sub_menu)

    return {
        "workspace": ws.value,
        "sub_menu": sub_menu,
    }


@app.get("/api/workspace/state")
async def get_workspace_state(token: str = ""):
    """Return the current workspace and its panel data."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from memory.supabase_client import get_ceo_context

    ceo_context = get_ceo_context()
    if not ceo_context:
        raise HTTPException(status_code=500, detail="No CEO context configured")

    session_key = str(ceo_context.get("chat_id"))
    current_ws = get_workspace(session_key)

    sub_menu = generate_sub_menu(current_ws)
    sub_menu["formatted_text"] = format_sub_menu_as_text(sub_menu)

    return {
        "workspace": current_ws.value,
        "sub_menu": sub_menu,
    }


@app.get("/api/inspect/coverage")
async def get_inspect_coverage(token: str = "") -> dict:
    """Return coverage heatmap data for the Inspect workspace."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.inspect_handler import get_coverage_heatmap

        return get_coverage_heatmap()
    except Exception as e:
        logger.error(f"Error getting coverage heatmap: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to compute coverage heatmap"
        )


@app.get("/api/inspect/contradictions")
async def get_inspect_contradictions(token: str = "") -> dict:
    """Return list of detected contradictions."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.inspect_handler import get_contradictions_list

        return get_contradictions_list()
    except Exception as e:
        logger.error(f"Error getting contradictions: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve contradictions"
        )


@app.get("/api/inspect/stale")
async def get_inspect_stale(token: str = "") -> dict:
    """Return stale data report — items that may need refreshing."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.inspect_handler import get_stale_data_report

        return get_stale_data_report()
    except Exception as e:
        logger.error(f"Error getting stale data report: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to compute stale data report"
        )


@app.get("/api/validate/queue")
async def get_validate_queue(token: str = "") -> dict:
    """Return assumptions awaiting validation."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.validate_handler import get_assumption_queue

        return get_assumption_queue()
    except Exception as e:
        logger.error(f"Error getting assumption queue: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve assumption queue"
        )


@app.get("/api/build/status")
async def get_build_status(token: str = "") -> dict:
    """Return current pipeline build progress."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.build_handler import (
            get_build_status as _get_build_status,
        )

        return _get_build_status()
    except Exception as e:
        logger.error(f"Error getting build status: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve build status"
        )


@app.get("/api/export/readiness")
async def get_export_readiness(token: str = "") -> dict:
    """Return export readiness check — which formats are ready."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.export_handler import (
            get_export_readiness as _get_export_readiness,
        )

        return _get_export_readiness()
    except Exception as e:
        logger.error(f"Error getting export readiness: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to check export readiness"
        )


@app.post("/api/export/generate")
async def post_export_generate(req: ExportRequest) -> dict:
    """Trigger export generation for the specified format."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    valid_formats = {
        "full_plan",
        "executive_summary",
        "investor",
        "internal",
        "gap_report",
    }
    if req.format not in valid_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format: {req.format}. Must be one of: {sorted(valid_formats)}",
        )

    try:
        from web.handlers.export_handler import (
            export_full_plan,
            export_executive_summary,
            export_investor_version,
            export_internal_version,
            export_gap_report,
        )

        format_dispatch = {
            "full_plan": export_full_plan,
            "executive_summary": export_executive_summary,
            "investor": export_investor_version,
            "internal": export_internal_version,
            "gap_report": export_gap_report,
        }

        handler_fn = format_dispatch[req.format]
        result = handler_fn()
        return result
    except Exception as e:
        logger.error(f"Error generating export ({req.format}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate {req.format} export",
        )


@app.get("/api/non-scope/queue")
async def get_non_scope_queue(token: str = "") -> dict:
    """Return non-scope items pending human review."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.non_scope_router import get_non_scope_queue as _get_queue

        items = _get_queue()
        return {"pending": items, "count": len(items)}
    except Exception as e:
        logger.error(f"Error getting non-scope queue: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve non-scope queue"
        )


async def push_workspace_update(
    session_key: str, workspace: str, data: dict
) -> None:
    """Push workspace-specific update to connected clients."""
    await manager.broadcast(
        session_key,
        {
            "role": "system",
            "type": "workspace_update",
            "workspace": workspace,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


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
