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
from typing import Dict, Optional, Set
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    UploadFile,
    File,
    Form,
)
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


@app.on_event("startup")
async def _register_main_loop() -> None:
    """Register this process's event loop with the trace emitter.

    Document extraction (Feed uploads) runs in a worker thread via
    asyncio.to_thread() so it doesn't block the server while parsing a
    file — but emit_trace() calls from that thread need a reference to
    THIS loop to actually reach connected WebSocket clients.
    """
    from tools.trace_emitter import set_main_loop

    set_main_loop(asyncio.get_running_loop())


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
    # ── System-awareness check (uniform across ALL workspaces) ────────────
    from web.handlers.system_awareness import is_system_question, answer_system_question

    if is_system_question(text):
        try:
            return answer_system_question(text, session_id=session_id)
        except Exception as e:
            logger.error("[SystemAwareness] Handler crashed: %s", e)
            # Fall through to normal workspace dispatch on failure

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
        "a": ("heatmap", lambda: get_coverage_heatmap(session_id=session_id)),
        "b": ("confidence", lambda: get_confidence_breakdown(session_id=session_id)),
        "c": ("contradictions", lambda: get_contradictions_list(session_id=session_id)),
        "d": ("stale", lambda: get_stale_data_report(session_id=session_id)),
        "e": ("dependencies", lambda: get_dependency_view(session_id=session_id)),
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
        result = answer_inspect_question(text, session_id=session_id)
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
        _get_all_sections,
    )

    commands = {
        "a": ("full_plan", lambda: build_full_plan(session_id)),
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

    # "b" — single section build. Prompt for which section, same pattern as
    # Inspect's deep-dive ("f"): first show the picker, then treat the next
    # free-text reply as the section identifier itself.
    if text_lower == "b":
        try:
            sections = _get_all_sections()
        except Exception as e:
            logger.error("[Build] Error listing sections: %s", e)
            sections = []
        section_list = ", ".join(sections) if sections else "e.g. 'BP.9' or '9'"
        return f"Which section would you like to build?\n\nAvailable: {section_list}"

    # Anything question-shaped ("can you tell me where we are in opportunity
    # part") used to fall straight into build_section() below and get
    # silently normalized into a garbage section ID like
    # "BP.can you tell me where we are in opportunity part" — same class of
    # bug Feed had before its question gate. Answer it (or redirect to the
    # right workspace) instead of trying to build it.
    from tools.question_gate import looks_like_question, handle_workspace_question

    if looks_like_question(text):
        return handle_workspace_question(text, "Build", session_id=session_id)

    # Anything else while in Build isn't a menu key — treat it as a section
    # identifier for a single-section build (this is what "b" used to skip).
    if text.strip():
        try:
            data = build_section(text.strip(), session_id)
            return format_build_response(data)
        except Exception as e:
            logger.error("[Build] Error building section '%s': %s", text, e)
            return f"Error building section '{text}': {e}"

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
        "a": ("weakest", lambda: challenge_weakest_assumptions(session_id=session_id)),
        "b": ("full_plan", lambda: challenge_full_plan(session_id=session_id)),
        "c": ("vulnerabilities", lambda: get_vulnerability_list(session_id=session_id)),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_challenge_response(data)
        except Exception as e:
            logger.error("[Challenge] Error handling '%s': %s", text_lower, e)
            return f"Error running challenge: {e}"

    # Same misparse risk as Build: a plain question ("what's our weakest
    # section?") would otherwise get treated as a literal claim to
    # stress-test, producing a nonsensical "challenge" built around the
    # question text itself.
    from tools.question_gate import looks_like_question, handle_workspace_question

    if looks_like_question(text):
        return handle_workspace_question(text, "Challenge", session_id=session_id)

    try:
        result = challenge_claim(text, session_id=session_id)
        return format_challenge_response(result)
    except Exception as e:
        logger.error("[Challenge] Error challenging claim: %s", e)
        return f"Error: {e}"


def _dispatch_validate(text_lower: str, text: str, session_id: str) -> str:
    from web.handlers.validate_handler import (
        get_assumption_queue,
        format_validate_response,
        handle_pending_response,
        request_kill,
        request_confirm,
    )

    pending_result = handle_pending_response(text, session_id=session_id)
    if pending_result is not None:
        return pending_result

    if text_lower == "a":
        try:
            data = get_assumption_queue(session_id=session_id)
            return format_validate_response(data)
        except Exception as e:
            logger.error("[Validate] Error: %s", e)
            return f"Error: {e}"

    if text_lower.startswith("kill "):
        parts = text[5:].split("|", 1)
        assumption_text = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else "Killed by CEO"
        if assumption_text:
            return request_kill(assumption_text, reason, session_id=session_id)

    if text_lower.startswith("validate "):
        parts = text[9:].split("|", 1)
        assumption_text = parts[0].strip()
        evidence = parts[1].strip() if len(parts) > 1 else "CEO confirmed"
        if assumption_text:
            return request_confirm(assumption_text, evidence, session_id=session_id)

    # Anything question-shaped that isn't a menu key or a kill/validate
    # command used to fall through to a bare "" here, which server.py then
    # papers over with a generic "nothing to show for that input" — not
    # useful if Alex actually asked something answerable.
    from tools.question_gate import looks_like_question, handle_workspace_question

    if looks_like_question(text):
        return handle_workspace_question(text, "Validate", session_id=session_id)

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
        "d": ("readiness", lambda: get_export_readiness(session_id=session_id)),
    }

    if text_lower in commands:
        query_type, fn = commands[text_lower]
        try:
            data = fn()
            return format_export_response(data)
        except Exception as e:
            logger.error("[Export] Error handling '%s': %s", text_lower, e)
            return f"Error: {e}"

    from tools.question_gate import looks_like_question, handle_workspace_question

    if looks_like_question(text):
        return handle_workspace_question(text, "Export", session_id=session_id)

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

    try:
        store_ceo_message(
            message=req.text.strip(),
            session_id=session_key,
            channel="web",
            metadata={"chat_id": chat_id},
        )
    except Exception as e:
        logger.warning("[Server] store_ceo_message failed (non-blocking): %s", e)

    from web.handlers.feed_handler import get_feed_state
    feed_state = get_feed_state(session_key)
    if feed_state and feed_state.startswith("FEED_AWAITING"):
        await manager.broadcast(
            session_key,
            {
                "role": "user",
                "text": req.text.strip(),
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "web",
            },
        )
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

    # ── Final delivery gate: intercept "deliver"/"cancel" ─────────────────
    text_lower_trimmed = req.text.strip().lower()
    if text_lower_trimmed in ("deliver", "cancel"):
        try:
            from memory.redis_client import RedisClient

            _redis = RedisClient()
            pending_keys = _redis.client.keys("final_delivery_state:*")
            if pending_keys:
                key = pending_keys[0]
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                run_id = key.replace("final_delivery_state:", "")
                _redis.client.set(
                    f"final_delivery_response:{run_id}",
                    text_lower_trimmed,
                    ex=3600,
                )
                ack = (
                    "Delivering the final plan now."
                    if text_lower_trimmed == "deliver"
                    else "Plan delivery held. You can review sections and say 'deliver' when ready."
                )
                await manager.broadcast(
                    session_key,
                    {
                        "role": "assistant",
                        "text": ack,
                        "timestamp": datetime.utcnow().isoformat(),
                        "channel": "system",
                    },
                )
                return {
                    "status": "delivery_gate",
                    "action": text_lower_trimmed,
                    "session_key": session_key,
                }
        except Exception as e:
            logger.warning("[Server] Final delivery gate check failed: %s", e)

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

    if current_ws != Workspace.AUTO:
        await manager.broadcast(
            session_key,
            {"role": "status", "text": "Processing...", "timestamp": datetime.utcnow().isoformat()},
        )
        try:
            response_text = await asyncio.wait_for(
                asyncio.to_thread(
                    _handle_workspace_message, current_ws, req.text.strip(), session_key
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"[Workspace:{current_ws.value}] Handler timed out after 120s")
            response_text = "That took too long. Please try again or simplify your request."
        except Exception as e:
            logger.error(f"[Workspace:{current_ws.value}] Handler crashed: {e}", exc_info=True)
            response_text = "Something went wrong processing that. Please try again."
        finally:
            await manager.broadcast(
                session_key,
                {"role": "status", "text": "", "timestamp": datetime.utcnow().isoformat()},
            )
        if not response_text:
            response_text = "Received — nothing to show for that input."
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

    from web.handlers.auto_handler import classify_intent, handle_auto_message, format_auto_response

    classification = classify_intent(req.text.strip())
    intent = classification["intent"]

    pipeline_intents = {"decision", "new_data"}
    use_pipeline = (
        _pipeline_handler is not None
        and intent in pipeline_intents
    )

    if not use_pipeline:
        result = await asyncio.to_thread(
            handle_auto_message, req.text.strip(), session_id=session_key
        )
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


@app.get("/api/digest")
async def get_session_digest(token: str = "", since_minutes: int = 120):
    """Return a 'What Changed' digest of recent activity.

    Shows facts filed, assumptions validated/killed, contradictions
    detected, and coverage change since the given time window.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from datetime import timedelta
        from services.rag_service import retrieve, _get_supabase

        cutoff = (datetime.utcnow() - timedelta(minutes=since_minutes)).isoformat()
        supabase = _get_supabase()

        facts_filed = (
            supabase.table("knowledge_base")
            .select("id, content, source_type, section, metadata", count="exact")
            .gte("created_at", cutoff)
            .in_("source_type", ["ceo_doc", "conversation", "decision"])
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        contradictions = (
            supabase.table("knowledge_base")
            .select("id, content", count="exact")
            .gte("created_at", cutoff)
            .eq("source_type", "contradiction_resolution")
            .execute()
        )

        killed = (
            supabase.table("knowledge_base")
            .select("id, content", count="exact")
            .gte("created_at", cutoff)
            .eq("source_type", "negative_knowledge")
            .execute()
        )

        validated = (
            supabase.table("knowledge_base")
            .select("id, content", count="exact")
            .gte("created_at", cutoff)
            .eq("epistemic_status", "CONFIRMED")
            .in_("source_type", ["ceo_doc", "decision"])
            .execute()
        )

        return {
            "since_minutes": since_minutes,
            "facts_filed": {
                "count": facts_filed.count or len(facts_filed.data or []),
                "recent": [
                    {
                        "content": r["content"][:100],
                        "node_id": (r.get("metadata") or {}).get("node_id"),
                    }
                    for r in (facts_filed.data or [])[:5]
                ],
            },
            "contradictions_detected": contradictions.count or len(contradictions.data or []),
            "assumptions_killed": killed.count or len(killed.data or []),
            "facts_validated": validated.count or len(validated.data or []),
        }
    except Exception as e:
        logger.error(f"Error computing digest: {e}")
        return {
            "since_minutes": since_minutes,
            "facts_filed": {"count": 0, "recent": []},
            "contradictions_detected": 0,
            "assumptions_killed": 0,
            "facts_validated": 0,
        }


@app.get("/api/assumptions/lifecycle")
async def get_assumption_lifecycle(token: str = ""):
    """Return assumption lifecycle stats for the ambient tracker.

    Shows: active, validated today, killed today, aging (>30d untested).
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase
        from datetime import timedelta

        supabase = _get_supabase()
        today_start = (datetime.utcnow().replace(hour=0, minute=0, second=0)).isoformat()
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()

        active = (
            supabase.table("knowledge_base")
            .select("id", count="exact")
            .eq("epistemic_status", "ASSUMPTION")
            .is_("superseded_by", "null")
            .execute()
        )

        validated_today = (
            supabase.table("knowledge_base")
            .select("id", count="exact")
            .eq("epistemic_status", "CONFIRMED")
            .gte("created_at", today_start)
            .execute()
        )

        killed_today = (
            supabase.table("knowledge_base")
            .select("id", count="exact")
            .eq("source_type", "negative_knowledge")
            .gte("created_at", today_start)
            .execute()
        )

        aging = (
            supabase.table("knowledge_base")
            .select("id", count="exact")
            .eq("epistemic_status", "ASSUMPTION")
            .is_("superseded_by", "null")
            .lte("created_at", thirty_days_ago)
            .execute()
        )

        return {
            "active": active.count or 0,
            "validated_today": validated_today.count or 0,
            "killed_today": killed_today.count or 0,
            "aging_30d": aging.count or 0,
        }
    except Exception as e:
        logger.error(f"Error computing assumption lifecycle: {e}")
        return {"active": 0, "validated_today": 0, "killed_today": 0, "aging_30d": 0}


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

    # Broadcast the switch to any other connected clients (other tabs, or a
    # workspace change triggered from Telegram) so they stay in sync live
    # instead of only updating whichever client made this request.
    try:
        await push_workspace_update(session_key, ws.value, sub_menu)
    except Exception as e:
        logger.warning("Could not broadcast workspace update: %s", e)

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

        return await asyncio.to_thread(get_coverage_heatmap, session_id=_get_session_key())
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

        return await asyncio.to_thread(get_contradictions_list, session_id=_get_session_key())
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

        return await asyncio.to_thread(get_stale_data_report, session_id=_get_session_key())
    except Exception as e:
        logger.error(f"Error getting stale data report: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to compute stale data report"
        )


# ============================================================================
# EPISTEMIC TRACKING — surfaces data the backend already computes (council
# quality-review scores, killed ideas, fact staleness) that had no endpoint
# before. Read-only except confirm-chunk, which just resets a freshness
# timestamp — no agent/pipeline logic is touched by any of these.
# ============================================================================


@app.get("/api/epistemic/council")
async def get_epistemic_council(token: str = "", limit: int = 30) -> dict:
    """Return recent council quality-review reports (pass/revise/escalate)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase

        supabase = _get_supabase()
        result = (
            supabase.table("council_reports")
            .select(
                "id, section_number, agent_name, attempt, score, decision, "
                "critiques, improvements_made, revision_instructions, created_at"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"reports": result.data or []}
    except Exception as e:
        logger.error(f"Error getting council reports: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve council reports"
        )


@app.get("/api/epistemic/killed-ideas")
async def get_epistemic_killed_ideas(token: str = "", limit: int = 30) -> dict:
    """Return ideas explicitly killed (negative knowledge) so they're never
    silently re-suggested — surfaced here for transparency."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("id, content, section, created_at")
            .eq("source_type", "negative_knowledge")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"killed_ideas": result.data or []}
    except Exception as e:
        logger.error(f"Error getting killed ideas: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve killed ideas"
        )


class ConfirmChunkRequest(BaseModel):
    """Payload for POST /api/epistemic/confirm-chunk."""

    chunk_id: str
    token: str


@app.post("/api/epistemic/confirm-chunk")
async def post_confirm_chunk(req: ConfirmChunkRequest) -> dict:
    """Re-confirm a fact, resetting its staleness clock."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.temporal_decay import confirm_chunk

        success = confirm_chunk(req.chunk_id)
        if not success:
            raise HTTPException(status_code=404, detail="Chunk not found")
        return {"success": True, "chunk_id": req.chunk_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming chunk {req.chunk_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to confirm chunk")


def _get_session_key() -> str:
    """Resolve the current CEO session key the same way every other endpoint does."""
    try:
        from memory.supabase_client import get_ceo_context

        ceo_context = get_ceo_context()
    except Exception:
        ceo_context = None
    if not ceo_context:
        ceo_context = {"chat_id": 1, "name": "Alex"}
    return str(ceo_context.get("chat_id", 1))


@app.post("/api/feed/upload")
async def upload_feed_document(
    file: UploadFile = File(...), token: str = Form(...)
) -> dict:
    """Upload a raw document (PDF/DOCX/XLSX/TXT/CSV) into Feed mode.

    Extracts text, classifies every atomic fact found, and returns the
    full batch for review — the Process panel shows the extraction steps
    live via trace events, then flips to this batch for bulk review.
    Nothing is written to the knowledge base yet; see /api/feed/bulk-approve.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    session_key = _get_session_key()
    filename = file.filename or "upload"
    file_bytes = await file.read()

    await manager.broadcast(
        session_key,
        {
            "role": "user",
            "text": f"[Uploaded: {filename}]",
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "web",
        },
    )

    from services.document_extractor import extract_text, ExtractionError
    from web.handlers.feed_handler import process_uploaded_document

    try:
        # Both of these are synchronous, CPU-bound (PDF parsing, local
        # embedding inference for node matching) and can take several
        # seconds on a real document. Running them directly here would
        # block the event loop — no other request, no WebSocket ping/pong,
        # and critically none of the trace broadcasts scheduled during
        # extraction would actually go out until this endpoint returned,
        # defeating the entire point of "live" narration. to_thread() keeps
        # the loop free so trace events stream out as they're emitted.
        text = await asyncio.to_thread(extract_text, file_bytes, filename, session_key)
        batch = await asyncio.to_thread(
            process_uploaded_document, text, filename, session_key
        )
    except ExtractionError as e:
        await manager.broadcast(
            session_key,
            {
                "role": "assistant",
                "text": str(e),
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "system",
                "workspace": "feed",
            },
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing uploaded document {filename}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to process {filename}"
        )

    if batch.get("total_facts", 0) == 0:
        await manager.broadcast(
            session_key,
            {
                "role": "assistant",
                "text": f"No extractable facts found in {filename}.",
                "timestamp": datetime.utcnow().isoformat(),
                "channel": "system",
                "workspace": "feed",
            },
        )
        return {"status": "no_facts", "session_key": session_key, "batch": batch}

    await manager.broadcast(
        session_key,
        {
            "role": "assistant",
            "text": (
                f"Extracted {batch['total_facts']} fact(s) from {filename}. "
                f"Open the Process panel to review before storing."
            ),
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "system",
            "workspace": "feed",
        },
    )

    return {"status": "awaiting_review", "session_key": session_key, "batch": batch}


class BulkApproveRequest(BaseModel):
    """Payload for POST /api/feed/bulk-approve."""

    token: str
    accepted_fact_ids: list[str]
    edited_texts: Optional[dict] = None


@app.post("/api/feed/bulk-approve")
async def bulk_approve_feed_batch(req: BulkApproveRequest) -> dict:
    """Store the Alex-approved subset of a classified document batch."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    session_key = _get_session_key()

    from web.handlers.feed_handler import bulk_store_facts

    # Same reasoning as the upload endpoint: store() does network calls
    # (Supabase writes, RAG retrieval for contradiction/dedup checks) per
    # fact — off the event loop so trace events stream live instead of
    # arriving in one burst when the request finally returns.
    result = await asyncio.to_thread(
        bulk_store_facts, session_key, req.accepted_fact_ids, req.edited_texts
    )

    if result.get("error"):
        summary = result["error"]
    else:
        summary = f"Stored {result['stored_count']} fact(s)"
        if result.get("duplicate_count"):
            summary += f", {result['duplicate_count']} duplicate(s) skipped"
        if result.get("skipped_count"):
            summary += f", {result['skipped_count']} not selected"
        summary += "."

    await manager.broadcast(
        session_key,
        {
            "role": "assistant",
            "text": summary,
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "system",
            "workspace": "feed",
        },
    )

    return {"status": "done", "session_key": session_key, **result}


@app.get("/api/validate/queue")
async def get_validate_queue(token: str = "") -> dict:
    """Return assumptions awaiting validation."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.validate_handler import get_assumption_queue

        return await asyncio.to_thread(get_assumption_queue, session_id=_get_session_key())
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


@app.get("/api/challenge/vulnerabilities")
async def get_challenge_vulnerabilities(token: str = "") -> dict:
    """Return the ranked vulnerability list — used to badge the Challenge tile."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.challenge_handler import get_vulnerability_list

        return await asyncio.to_thread(get_vulnerability_list, session_id=_get_session_key())
    except Exception as e:
        logger.error(f"Error getting vulnerability list: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve vulnerability list"
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

        return await asyncio.to_thread(_get_export_readiness, session_id=_get_session_key())
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
        session_key = _get_session_key()
        result = await asyncio.to_thread(handler_fn, session_id=session_key)
        return result
    except Exception as e:
        logger.error(f"Error generating export ({req.format}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate {req.format} export",
        )


@app.get("/api/export/download/{filename}")
async def download_export_file(filename: str, token: str = ""):
    """Serve a previously generated export file for download.

    The Process panel surfaces a Download button the moment export_full_plan()
    finishes — this is what it links to.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from web.handlers.export_handler import OUTPUTS_DIR

    # Resolve strictly within OUTPUTS_DIR — reject anything that escapes it
    # (path traversal via "../", absolute paths, etc.) before touching disk.
    safe_name = Path(filename).name
    file_path = (OUTPUTS_DIR / safe_name).resolve()
    try:
        file_path.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=str(file_path),
        filename=safe_name,
        media_type="application/octet-stream",
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


# Supabase/PostgREST silently caps any single request at this many rows
# (its default `max-rows` setting) no matter what limit/range is requested
# in the client — discovered live when a 20000-row range() request for
# node-order sorting came back with exactly 1000 rows out of ~4k that
# actually exist. Pulling a large result set reliably means paging in
# chunks of this size and looping until a short page comes back, since
# there's no single-call way around the server-side cap.
_SUPABASE_PAGE_CAP = 1000


async def _fetch_all_knowledge_rows(epistemic_status: str = "", hard_cap: int = 50000) -> list[dict]:
    """Page through knowledge_base past Supabase's per-request row cap.

    Used anywhere the full (or near-full) table needs to be in memory at
    once — node-order sorting and the xlsx export both need this, since
    neither can be done with a single DB-level .order()/.range() call.
    """
    from services.rag_service import _get_supabase, TABLE_NAME

    def _page(start: int, end: int):
        supabase = _get_supabase()
        q = (
            supabase.table(TABLE_NAME)
            .select(
                "id, content, source_type, section, epistemic_status, "
                "topic_tags, confidence, metadata, created_at, superseded_by"
            )
            .is_("superseded_by", "null")
            .order("created_at", desc=True)
        )
        if epistemic_status:
            q = q.eq("epistemic_status", epistemic_status)
        q = q.range(start, end)
        return q.execute()

    all_rows: list[dict] = []
    start = 0
    while start < hard_cap:
        end = start + _SUPABASE_PAGE_CAP - 1
        result = await asyncio.to_thread(_page, start, end)
        page = result.data or []
        all_rows.extend(page)
        if len(page) < _SUPABASE_PAGE_CAP:
            break
        start += _SUPABASE_PAGE_CAP
    return all_rows


@app.get("/api/knowledge/stored")
async def get_stored_knowledge(
    token: str = "",
    limit: int = 100,
    offset: int = 0,
    node_id: str = "",
    epistemic_status: str = "",
    sort: str = "node",
) -> dict:
    """Return stored knowledge_base rows as a flat table — Alex's ask for
    visibility into exactly what got stored and under which node, without
    needing to open Supabase directly.

    Same headings every row uses, regardless of source: node_id,
    node_title, content_type, epistemic_status, content, source,
    stored_at. Supports pagination and optional filtering by node_id
    (exact) or epistemic_status, so the in-app table can page through the
    full knowledge base without pulling everything at once.

    sort="node" (default) orders rows the same way Alex's source BP
    architecture spreadsheet is laid out — BP.1, BP.1.1, BP.1.1.1, ...,
    BP.1.2, BP.2, ... (depth-first, numeric per dotted segment). That's not
    something Postgres/Supabase can do with a plain .order() on a text
    column (lexicographic sort would wrongly put "BP.1.10" before "BP.1.2"),
    so this pulls the full matching set (via _fetch_all_knowledge_rows,
    which pages past Supabase's row cap) and sorts it in Python against
    bp_architecture.json's own row order, which is already in that exact
    sequence. sort="recent" keeps the original newest-first, single-page
    DB-paginated behavior for anyone who wants that instead.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        node_sort = sort != "recent"

        if node_sort:
            raw_rows = await _fetch_all_knowledge_rows(epistemic_status)
            result = None
        else:
            def _query():
                supabase = _get_supabase()
                q = (
                    supabase.table(TABLE_NAME)
                    .select(
                        "id, content, source_type, section, epistemic_status, "
                        "topic_tags, confidence, metadata, created_at, superseded_by",
                        count="exact",
                    )
                    .is_("superseded_by", "null")
                    .order("created_at", desc=True)
                )
                if epistemic_status:
                    q = q.eq("epistemic_status", epistemic_status)
                q = q.range(offset, offset + limit - 1)
                return q.execute()

            result = await asyncio.to_thread(_query)
            raw_rows = result.data or []

        rows = []
        for row in raw_rows:
            meta = row.get("metadata") or {}
            row_node_id = meta.get("node_id") or row.get("section") or ""
            if node_id and row_node_id != node_id:
                continue
            rows.append({
                "id": row["id"],
                "node_id": row_node_id,
                "node_title": meta.get("node_title", ""),
                "content_type": meta.get("content_type") or (
                    row.get("topic_tags") or [""]
                )[0],
                "epistemic_status": row.get("epistemic_status", ""),
                "content": row.get("content", ""),
                "source": meta.get("source") or row.get("source_type", ""),
                "stored_at": row.get("created_at", ""),
                "confidence": row.get("confidence"),
            })

        if node_sort:
            from web.handlers.feed_handler import _load_bp_architecture

            order_index = {
                n["node_id"]: i for i, n in enumerate(_load_bp_architecture())
                if n.get("node_id")
            }
            # Facts with no node_id, or one that no longer matches any real
            # node, sort after every real node instead of crashing or
            # landing arbitrarily first. Python's sort is stable, so rows
            # sharing a node_id keep their existing newest-first order.
            rows.sort(key=lambda r: order_index.get(r["node_id"], len(order_index)))
            total = len(rows)
            rows = rows[offset:offset + limit]
        else:
            total = getattr(result, "count", None) or len(rows)

        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": "node" if node_sort else "recent",
        }
    except Exception as e:
        logger.error(f"Error fetching stored knowledge table: {e}")
        raise HTTPException(status_code=500, detail="Failed to load stored knowledge")


@app.get("/api/knowledge/stored/export")
async def export_stored_knowledge(token: str = "") -> FileResponse:
    """Export the full stored-knowledge table as an .xlsx with the same
    headings shown in the in-app table, for Alex to open outside the app.

    Rows are ordered the same way the in-app table's default "BP order"
    view is — matching Alex's source architecture spreadsheet's own row
    order (BP.1, BP.1.1, BP.1.1.1, ..., BP.1.2, BP.2, ...) rather than
    newest-first, so the export reads like the sheet he's used to."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from web.handlers.feed_handler import _load_bp_architecture

        export_rows = await _fetch_all_knowledge_rows()

        order_index = {
            n["node_id"]: i for i, n in enumerate(_load_bp_architecture())
            if n.get("node_id")
        }
        export_rows.sort(
            key=lambda row: order_index.get(
                (row.get("metadata") or {}).get("node_id") or row.get("section") or "",
                len(order_index),
            )
        )

        import openpyxl
        from openpyxl.styles import Font

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Knowledge Base"
        headers = ["Node ID", "Node Title", "Content Type", "Epistemic Status", "Content", "Source", "Stored At"]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)

        for row in export_rows:
            meta = row.get("metadata") or {}
            ws.append([
                meta.get("node_id") or row.get("section") or "",
                meta.get("node_title", ""),
                meta.get("content_type") or (row.get("topic_tags") or [""])[0],
                row.get("epistemic_status", ""),
                row.get("content", ""),
                meta.get("source") or row.get("source_type", ""),
                row.get("created_at", ""),
            ])

        for col in ws.columns:
            max_len = max((len(str(c.value)) for c in col if c.value), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

        outputs_dir = Path(__file__).parent.parent / "outputs"
        outputs_dir.mkdir(exist_ok=True)
        file_path = outputs_dir / f"knowledge_base_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        wb.save(file_path)

        return FileResponse(
            path=file_path,
            filename=file_path.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        logger.error(f"Error exporting stored knowledge table: {e}")
        raise HTTPException(status_code=500, detail="Export failed")


@app.get("/api/search")
async def search_knowledge_base(q: str = "", token: str = "", limit: int = 8) -> dict:
    """Semantic search across the whole knowledge base — powers the command palette."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not q.strip():
        return {"query": q, "results": []}

    try:
        from services.rag_service import retrieve
        from tools.trace_emitter import emit_trace

        session_key = _get_session_key()
        emit_trace(session_key, "Search", "searching", f"Searching for: \"{q[:60]}\"...")

        chunks = await asyncio.to_thread(retrieve, query=q.strip(), top_k=limit, threshold=0.3)

        results = [
            {
                "id": c.id,
                "content": c.content[:200],
                "source_type": c.source_type,
                "section": c.section,
                "epistemic_status": c.epistemic_status,
                "similarity": round(c.similarity, 3),
            }
            for c in chunks
        ]

        emit_trace(session_key, "Search", "search_complete", f"Found {len(results)} result(s)")

        return {"query": q, "results": results}
    except Exception as e:
        logger.error(f"Error searching knowledge base: {e}")
        raise HTTPException(status_code=500, detail="Search failed")


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
