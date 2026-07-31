"""
FastAPI web server for multi-agent system chat interface.
Provides REST + WebSocket endpoints so the CEO can use
the same pipeline from a browser, synced with Telegram.
"""

import os
import logging
import asyncio
import re
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
    Request,
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
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


_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    """The server's event loop, for scheduling work from worker threads."""
    global _MAIN_LOOP
    if _MAIN_LOOP is None:
        from tools.trace_emitter import _main_loop as trace_loop

        _MAIN_LOOP = trace_loop
    return _MAIN_LOOP


@app.on_event("startup")
async def _register_main_loop() -> None:
    """Register this process's event loop with the trace emitter.

    Document extraction (Feed uploads) runs in a worker thread via
    asyncio.to_thread() so it doesn't block the server while parsing a
    file — but emit_trace() calls from that thread need a reference to
    THIS loop to actually reach connected WebSocket clients.
    """
    from tools.trace_emitter import set_main_loop

    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
    set_main_loop(_MAIN_LOOP)

    # Keep the augmented BP-node index in sync with bp_architecture.json. Feed
    # node creation upserts incrementally, but a direct edit to the file would
    # otherwise leave the index stale until a manual rebuild. This does nothing
    # when the file is unchanged (hash match); a real rebuild (840 embeds, a few
    # minutes) runs in a worker thread so it never blocks server startup.
    async def _refresh_aug_index() -> None:
        try:
            from services.bp_aug_index import ensure_fresh

            result = await asyncio.to_thread(ensure_fresh)
            if result.get("rebuilt"):
                logger.info("Aug index rebuilt on startup: %s", result)
        except Exception as e:  # noqa: BLE001
            logger.warning("Aug index refresh skipped: %s", e)

    asyncio.create_task(_refresh_aug_index())


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


def _handle_workspace_message(workspace: Workspace, text: str, session_id: str):
    """Dispatch a message to the active workspace handler.

    Returns response text (str) or AnswerResponse object, or empty string.
    """
    # ── Pipeline interaction check: if Build is active, route to orchestrator ──
    if workspace == Workspace.BUILD:
        from services.pipeline_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        status = orchestrator.get_status(session_id)

        # If pipeline is waiting for Alex, this message is his response
        if status.get("status") == "waiting_for_alex":
            try:
                import asyncio
                result = asyncio.run(orchestrator.handle_alex_response(session_id, text))
                if result.get("status") == "response_received":
                    return ""  # Pipeline will send its own next message
                logger.warning("[Server] Pipeline response failed: %s", result)
            except Exception as e:
                logger.error("[Server] Pipeline interaction error: %s", e)
                return f"Error processing your response: {str(e)}"

        # Build v2 typed commands ("work on section 8", "build all", "accept 8",
        # "show plan") — handle BEFORE the Answer Engine so they aren't swallowed
        # as knowledge queries.
        bv2 = _dispatch_build_v2(text.strip().lower(), session_id)
        if bv2 is not None:
            return bv2

    # ── Answer Engine: handles ANY question across all workspaces ──────────
    from web.handlers.answer_engine import is_question, answer_question

    if is_question(text):
        try:
            response = answer_question(text, session_id=session_id)
            if response:
                return response
        except Exception as e:
            logger.error("[AnswerEngine] Handler crashed: %s", e)
            # Fall through to normal workspace dispatch on failure

    text_lower = text.strip().lower()

    if workspace == Workspace.BUILD:
        return _dispatch_build(text_lower, text, session_id)
    elif workspace == Workspace.FEED:
        return _dispatch_feed(text_lower, text, session_id)
    elif workspace == Workspace.AUTO:
        # Auto & Ask consolidates Inspect, Challenge, Validate, Export
        return _dispatch_auto(text_lower, text, session_id)

    return ""


def _dispatch_auto(text_lower: str, text: str, session_id: str) -> str:
    """
    Auto & Ask workspace — consolidates Inspect, Challenge, Validate, Export.

    Detects intent and routes to the appropriate handler.
    """
    from web.handlers.inspect_handler import (
        get_coverage_heatmap,
        get_confidence_breakdown,
        get_contradictions_list,
        get_stale_data_report,
        get_dependency_view,
        format_inspect_response,
    )
    from web.handlers.challenge_handler import challenge_full_plan
    from web.handlers.export_handler import export_full_plan

    # Command menu
    if text_lower in ("?", "help", "menu"):
        return (
            "**Auto & Ask Mode** — System awareness & operations\n\n"
            "**Inspect (coverage, confidence, contradictions):**\n"
            "• Type 'a' → Coverage heatmap\n"
            "• Type 'b' → Confidence breakdown\n"
            "• Type 'c' → Contradictions\n"
            "• Type 'd' → Stale data report\n"
            "• Type 'e' → Dependencies\n\n"
            "**Validate & Challenge:**\n"
            "• Type 'challenge' → Stress-test entire plan\n"
            "• Type 'validate' → Check core assumptions\n\n"
            "**Export:**\n"
            "• Type 'export' → Export plan to DOCX/PDF\n\n"
            "**Or just ask any question about your plan.**\n"
        )

    # Inspect commands
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
            logger.error("[Auto] Error handling '%s': %s", text_lower, e)
            return f"Error: {e}"

    # Challenge/Validate
    if text_lower in ("challenge", "stress"):
        try:
            result = challenge_full_plan(session_id=session_id)
            return result.get("message", "Challenge complete")
        except Exception as e:
            logger.error("[Auto] Challenge failed: %s", e)
            return f"Error challenging plan: {e}"

    if text_lower in ("validate", "confirm"):
        return "Validate flow — work in progress. For now, type 'challenge' to stress-test."

    # Export
    if text_lower in ("export", "download", "docx", "pdf"):
        try:
            result = export_full_plan(session_id=session_id)
            return result.get("message", "Export complete")
        except Exception as e:
            logger.error("[Auto] Export failed: %s", e)
            return f"Error exporting: {e}"

    # Pipeline status
    if any(word in text_lower for word in ("status", "progress", "running", "building")):
        from services.pipeline_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        status = orchestrator.get_status(session_id)
        if status.get("status") == "idle":
            return "No build in progress. Type 'Build' workspace to start a new build."
        else:
            return (
                f"Pipeline Status: {status.get('status')}\n"
                f"Group: {status.get('current_group', '?')}/4\n"
                f"Run ID: {status.get('run_id', '?')}\n"
            )

    # Default: free-form question (answered by Answer Engine upstream)
    from tools.question_gate import looks_like_question

    if looks_like_question(text):
        # Answer Engine already handled this upstream, fall back to generic response
        return (
            "That's a great question! Use the Inspect commands (a-e) to explore your plan, "
            "or ask specific questions about sections, assumptions, or metrics."
        )

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


def _dispatch_build_v2(text_lower: str, session_id: str):
    """Parse Build v2 natural-language commands. Returns a response string, or
    None if the text isn't a build command (so other handlers can try)."""
    import re
    from services import build_v2, section_state

    # The workspace router passes the chat_id; Build v2 state keys on the session
    # UUID. Resolve it (no-op if already a UUID).
    resolved = _chat_to_session_uuid(session_id)
    if not resolved:
        return ("I couldn't find an active session to build into — send your business "
                "idea first, then we can work on sections.")
    session_id = resolved

    SEC = r"(bp\.?\d{1,2}|\d{1,2}|executive[ _]?summary|exec|summary)"

    def _sid(raw: str):
        raw = raw.strip().lower().replace("bp.", "").replace("bp", "").strip()
        if raw in ("exec", "executive summary", "executive_summary", "summary"):
            return "executive_summary"
        return raw if raw.isdigit() else None

    reg = section_state.load_registry()
    title = lambda s: reg.get(s, {}).get("title", s)

    # Build everything
    if re.search(r"\bbuild (all|everything|the (whole )?plan|full plan)\b", text_lower):
        build_v2.build_all(session_id)
        return ("🏗️ Building **all sections** in dependency order. Watch the Build board "
                "(side panel) — sections move to *needs review* as their agents finish, and "
                "any that need more data will ask for it.")

    # Show / plan / status
    if text_lower in ("plan", "show plan", "status", "board", "sections", "progress"):
        p = build_v2.get_plan(session_id)
        return p.get("overview_markdown", "No plan yet.")

    # Accept section N
    m = re.search(r"\baccept\s+(?:section\s+)?" + SEC, text_lower)
    if m and _sid(m.group(1)):
        s = _sid(m.group(1))
        build_v2.accept_section(session_id, s)
        return f"✓ Section **{s} — {title(s)}** accepted."

    # Adjust section N: <feedback>
    m = re.search(r"\badjust\s+(?:section\s+)?" + SEC + r"\s*[:\-]?\s*(.*)", text_lower)
    if m and _sid(m.group(1)) and m.group(2).strip():
        s = _sid(m.group(1))
        r = build_v2.adjust_section(session_id, s, m.group(2).strip())
        if r.get("status") == "started":
            return f"🔧 Revising section **{s} — {title(s)}** with your feedback…"
        return f"Couldn't adjust section {s}: {r.get('reason', r.get('status'))}."

    # Work on / build / run section N
    m = re.search(r"\b(?:work on|build|run|do|start|generate)\s+(?:section\s+)?" + SEC, text_lower)
    if m and _sid(m.group(1)):
        s = _sid(m.group(1))
        r = build_v2.run_section(session_id, s)
        st = r.get("status")
        if st == "started":
            return (f"🔨 Working on section **{s} — {title(s)}**. Its agent is drafting now; "
                    f"it'll appear on the Build board as *needs review* shortly (with a grounding "
                    f"report, and a council review for the key sections).")
        if st == "blocked":
            return (f"Section **{s} — {title(s)}** is waiting on {r.get('reason','earlier sections')}. "
                    f"Build those first, then come back to it.")
        if st == "unknown_section":
            return f"There's no section '{m.group(1)}'. Sections are 1–14 and the executive summary."
    return None


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


def _load_bp_architecture() -> list[dict]:
    """Load BP architecture nodes from ceo_data/bp_architecture.json.

    Inlined here after web/handlers/feed_handler.py (its previous home) was
    deleted in the Feed removal. Three non-Feed endpoints still need node
    order/metadata: the knowledge-table BP ordering, the xlsx export
    ordering, and the node-detail panel. Drops any row without a real BP.x
    node_id, matching the old loader's defensive filter.
    """
    import json
    from pathlib import Path

    path = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
    if not path.exists():
        logger.warning("[Server] bp_architecture.json not found at %s", path)
        return []
    try:
        nodes = json.loads(path.read_text(encoding="utf-8")).get("nodes", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[Server] Failed to load bp_architecture.json: %s", e)
        return []
    return [n for n in nodes if str(n.get("node_id", "")).startswith("BP.")]


FEED_HELP = (
    "**Feed** — paste text here, or upload a document.\n\n"
    "- Paste any text and I'll extract the facts and match each to a node.\n"
    "- `queue` — your batches and how many are still unreviewed\n"
    "- `review` — open the newest review queue\n"
    "- `upload` — the drop page for PDF / docx / txt\n\n"
    "_Nothing is filed until you confirm it._"
)

# Below this a message is a command, not content worth extracting facts from.
FEED_MIN_INGEST_CHARS = 40


def _feed_link(path: str) -> str:
    """A tokenised link to a Feed page, for chat replies."""
    return f"{path}{'&' if '?' in path else '?'}token={WEB_AUTH_TOKEN}"


def _dispatch_feed(text_lower: str, text: str, session_id: str) -> str:
    """Feed workspace: navigation commands, or ingest whatever was pasted.

    Runs on a worker thread (see the asyncio.to_thread call in POST
    /api/messages), so ingestion is scheduled onto the main loop rather than
    awaited here — a real document takes minutes and the chat must answer now.
    """
    from services import feed_batch_store

    stripped = text.strip()

    if text_lower in ("", "help", "?", "feed"):
        return FEED_HELP

    if text_lower in ("upload", "file", "document", "doc"):
        return f"[Open the upload page]({_feed_link('/feed')}) — PDF, docx, txt, xlsx or csv."

    if text_lower in ("queue", "status", "batches"):
        batches = feed_batch_store.list_batches()
        if not batches:
            return "No batches yet. Paste some text here, or [upload a document]" \
                   f"({_feed_link('/feed')})."
        lines = [
            f"- **{b['source_document']}** — {b['resolved']}/{b['total_facts']} resolved "
            f"([results]({_feed_link('/feed/results?run=' + b['run_id'])}))"
            for b in batches
        ]
        return "**Your batches**\n\n" + "\n".join(lines)

    if text_lower in ("review", "queue review", "open review"):
        batches = feed_batch_store.list_batches()
        pending = [b for b in batches if b["resolved"] < b["total_facts"]]
        if not pending:
            return "Nothing waiting for review."
        b = pending[0]
        left = b["total_facts"] - b["resolved"]
        return (f"**{b['source_document']}** — {left} fact(s) to review. "
                f"[Open the queue]({_feed_link('/feed/review?run=' + b['run_id'])})")

    if text_lower == "facts":
        return f"[Open the facts table]({_feed_link('/feed/facts')}) — every stored fact by node."

    if len(stripped) < FEED_MIN_INGEST_CHARS:
        return (f"That's too short to extract facts from. {FEED_HELP}")

    # Anything else is content. Ingest it the same way an upload is ingested.
    import uuid as _uuid

    run_id = f"feed-{_uuid.uuid4().hex[:12]}"
    loop = _get_main_loop()
    if loop is None:
        logger.error("[Feed] no event loop registered; cannot ingest pasted text")
        return "Feed is still starting up — try again in a moment."

    asyncio.run_coroutine_threadsafe(
        _run_feed_pipeline(stripped, "pasted text", session_id, run_id), loop
    )
    words = len(stripped.split())
    return (
        f"Reading {words} words — extracting facts and matching nodes now.\n\n"
        f"[Watch it live]({_feed_link('/feed/results?run=' + run_id)}) "
        f"— results appear as each fact is classified. Nothing is filed until you confirm."
    )


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


_TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AddFactRequest(BaseModel):
    """Payload for POST /api/knowledge-base/add."""

    topic: str
    fact: str
    status: str
    token: str


@app.get("/")
async def index():
    """Serve the chat HTML page.

    no-cache so UI/JS updates (the app JS is inline in this file) land on the
    next load instead of being served stale from the browser cache.
    """
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


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

    feed_state = None  # Feed handler removed — rebuild pending
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
            # The handler is still running in its worker thread and will deliver
            # its result over WebSocket when done (Feed self-delivers its summary).
            # Show a processing status instead of an error, and keep the indicator
            # up — do NOT clear the spinner or claim failure here.
            logger.warning(
                f"[Workspace:{current_ws.value}] Handler exceeded 120s — continuing in background"
            )
            await manager.broadcast(
                session_key,
                {
                    "role": "status",
                    "text": "Processing your input — extracting facts…",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            return {
                "status": "processing_in_background",
                "session_key": session_key,
                "workspace": current_ws.value,
            }
        except Exception as e:
            logger.error(f"[Workspace:{current_ws.value}] Handler crashed: {e}", exc_info=True)
            response_text = "Something went wrong processing that. Please try again."

        # Synchronous result in hand — clear the processing spinner.
        await manager.broadcast(
            session_key,
            {"role": "status", "text": "", "timestamp": datetime.utcnow().isoformat()},
        )

        # The handler may have already delivered its chat output over WebSocket
        # (Feed batch auto-filing) — in that case don't re-broadcast.
        FEED_ASYNC_SENTINEL = "__FEED_ASYNC_SENTINEL__"  # Feed removed — rebuild pending
        if response_text == FEED_ASYNC_SENTINEL:
            return {
                "status": "handled_self_delivered",
                "session_key": session_key,
                "workspace": current_ws.value,
            }

        if not response_text:
            response_text = "Received — nothing to show for that input."

        # Check if the response is a structured AnswerResponse (from answer_engine)
        from web.handlers.answer_engine import AnswerResponse
        if isinstance(response_text, AnswerResponse):
            await manager.broadcast(
                session_key,
                {
                    "role": "assistant",
                    "text": response_text.answer,
                    "metadata": {
                        "rich_type": "answer_card",
                        "answer": response_text.answer,
                        "confidence": response_text.confidence,
                        "sources": response_text.sources,
                        "search_ops": response_text.search_ops_run,
                        "total_results": response_text.total_results,
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                    "channel": "system",
                    "workspace": current_ws.value,
                },
            )
        else:
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


@app.get("/api/build-status")
async def get_build_status(token: str = "", session_id: Optional[str] = None):
    """Get the current pipeline build status."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.pipeline_orchestrator import get_orchestrator
        from web.handlers.build_handler import get_build_status as handler_get_build_status

        session_key = request.headers.get('X-Session-ID', session_id or '')
        if not session_key:
            return {"running": False, "status": "idle"}

        orchestrator = get_orchestrator()
        status = orchestrator.get_status(session_key)
        return status
    except Exception as e:
        logger.error(f"Error getting build status: {e}")
        return {"running": False, "status": "idle", "error": str(e)}


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


class ConfirmLeafRequest(BaseModel):
    """Payload for POST /api/feed/confirm-leaf."""

    chunk_id: str
    leaf_id: str
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


@app.post("/api/feed/confirm-leaf")
async def post_confirm_leaf(req: ConfirmLeafRequest) -> dict:
    """Confirm/refine the leaf of a section-filed Feed fact (one-click action).

    Moves the stored chunk from its provisional section to the chosen leaf and
    records the labeled correction. `leaf_id` may be the system's suggested leaf
    (confirm) or any other node id (correct).
    """
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Feed handler removed — rebuild pending.
    result = {"success": False, "error": "Feed workspace is being rebuilt. Check back soon."}
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Confirm failed"))
    return result


class BuildV2SectionRequest(BaseModel):
    """Payload for Build v2 per-section actions."""

    section_id: str
    token: str
    force: bool = False
    focus: Optional[str] = None


def _chat_to_session_uuid(chat) -> Optional[str]:
    """Resolve a chat id to the latest session UUID. Queries the real column
    (telegram_chat_id); returns the input unchanged if it's already a UUID."""
    if chat is None:
        return None
    if "-" in str(chat):  # already a UUID
        return str(chat)
    try:
        from services.rag_service import _get_supabase

        cid = int(chat) if str(chat).isdigit() else chat
        r = (_get_supabase().table("sessions").select("id")
             .eq("telegram_chat_id", cid).order("started_at", desc=True).limit(1).execute())
        if r.data:
            return r.data[0]["id"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not resolve session uuid for chat %s: %s", chat, e)
    return None


def _resolve_session_id() -> Optional[str]:
    """Resolve the active session's UUID (not the chat_id) for Build v2 state.

    ceo_context carries `telegram_chat_id` (not `chat_id`), so the plain
    _get_session_key() lookup can miss. Try, in order: the session-key chat id,
    the ceo_context telegram_chat_id, then the latest session overall (this is a
    single-CEO app, so the newest session is the current one).
    """
    sid = _chat_to_session_uuid(_get_session_key())
    if sid:
        return sid
    try:
        from memory.supabase_client import get_ceo_context

        ctx = get_ceo_context() or {}
        tg = ctx.get("telegram_chat_id")
        if tg is not None:
            sid = _chat_to_session_uuid(tg)
            if sid:
                return sid
    except Exception as e:  # noqa: BLE001
        logger.warning("Build v2 session resolve via ceo_context failed: %s", e)
    try:
        from services.rag_service import _get_supabase

        r = (_get_supabase().table("sessions").select("id")
             .order("started_at", desc=True).limit(1).execute())
        if r.data:
            return r.data[0]["id"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Build v2 session resolve via latest-session failed: %s", e)
    return None


@app.get("/api/build-v2/plan")
async def get_build_v2_plan(token: str) -> dict:
    """On-demand assembly of the whole plan (done/WIP/blocked/not-started)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import get_plan

    return await asyncio.to_thread(get_plan, session_id)


@app.get("/api/build-v2/next")
async def get_build_v2_next(token: str) -> dict:
    """What Alex can act on now: ready / needs_review / blocked sections."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import next_actions

    return await asyncio.to_thread(next_actions, session_id)


@app.get("/api/build-v2/section-thread")
async def get_build_v2_section_thread(token: str, section_id: str) -> dict:
    """One section rendered as a conversation (draft, grounding, Council, data)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.section_view import section_thread

    return await asyncio.to_thread(section_thread, session_id, section_id)


@app.get("/api/build-v2/focus-options")
async def get_build_v2_focus_options(token: str, section_id: str) -> dict:
    """Tap-to-answer focus choices for a section kickoff (Phase 3)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.section_view import focus_options

    return await asyncio.to_thread(focus_options, session_id, section_id)


class BuildV2AddSectionRequest(BaseModel):
    """Payload to add a custom specialist/section (Phase 4)."""

    title: str
    token: str


@app.post("/api/build-v2/add-section")
async def post_build_v2_add_section(req: BuildV2AddSectionRequest) -> dict:
    """Add a custom section written by the generic analyst agent."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not req.title or not req.title.strip():
        raise HTTPException(status_code=400, detail="Title required")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.section_state import add_custom_section

    row = await asyncio.to_thread(add_custom_section, session_id, req.title.strip())
    return {"status": "added", "section_id": row["section_id"], "title": row["title"]}


@app.get("/api/build-v2/stream")
async def get_build_v2_stream(request: Request, token: str) -> StreamingResponse:
    """Live board updates over one SSE connection (Phase 2).

    Polls the durable board snapshot and pushes only when it changes — one
    stream for the whole roster, not one connection per agent.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.section_view import board_snapshot

    async def _events():
        last = None
        while True:
            if await request.is_disconnected():
                break
            try:
                snap = await asyncio.to_thread(board_snapshot, session_id)
                blob = json.dumps(snap, sort_keys=True)
                if blob != last:
                    last = blob
                    yield f"data: {blob}\n\n"
                else:
                    yield ": heartbeat\n\n"
            except Exception as e:  # noqa: BLE001
                logger.warning("[BuildV2] stream snapshot failed: %s", e)
                yield ": error\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/build-v2/section")
async def post_build_v2_section(req: BuildV2SectionRequest) -> dict:
    """Run one section's agent (installment model)."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import run_section

    return await asyncio.to_thread(
        run_section, session_id, req.section_id, req.force, None, req.focus
    )


@app.post("/api/build-v2/accept")
async def post_build_v2_accept(req: BuildV2SectionRequest) -> dict:
    """Accept a needs_review section → done."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import accept_section

    return await asyncio.to_thread(accept_section, session_id, req.section_id)


class BuildV2AdjustRequest(BaseModel):
    section_id: str
    feedback: str
    token: str


@app.post("/api/build-v2/adjust")
async def post_build_v2_adjust(req: BuildV2AdjustRequest) -> dict:
    """Re-open a section and re-run it with Alex's feedback (real Adjust)."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import adjust_section

    return await asyncio.to_thread(adjust_section, session_id, req.section_id, req.feedback)


@app.get("/api/build-v2/export")
async def get_build_v2_export(token: str) -> dict:
    """Compile the current section drafts into one markdown business plan."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import export_plan

    return await asyncio.to_thread(export_plan, session_id)


@app.get("/api/build-v2/export-docx")
async def get_build_v2_export_docx(token: str):
    """Download the current business plan as a .docx file."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.build_v2 import export_docx
    from fastapi import Response

    data = await asyncio.to_thread(export_docx, session_id)
    if not data:
        raise HTTPException(status_code=500, detail="DOCX export unavailable")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="business_plan.docx"'},
    )


@app.get("/api/build-v2/data-requests")
async def get_build_v2_data_requests(token: str) -> dict:
    """The 'Needs your data' inbox — open data requests for this session."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.data_requests import list_open

    return {"requests": await asyncio.to_thread(list_open, session_id)}


class DataRequestCreate(BaseModel):
    """Payload to raise a data request (agent/manual)."""

    section_id: str
    target_nodes: list[str]
    description: str
    why: Optional[str] = None
    agent: Optional[str] = None
    token: str


@app.post("/api/build-v2/data-request")
async def post_build_v2_data_request(req: DataRequestCreate) -> dict:
    """Raise a data request → marks the section blocked_on_data."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    session_id = _resolve_session_id()
    if not session_id:
        raise HTTPException(status_code=404, detail="No active session")
    from services.data_requests import create

    result = await asyncio.to_thread(
        create, session_id, req.section_id, req.target_nodes, req.description, req.why, req.agent
    )
    return {"created": result}


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


# Feed review batches live in Redis (services/feed_batch_store), which is the
# documented home for ephemeral Feed state. Railway restarts containers
# routinely; a module-level dict lost a reviewer's unreviewed cards every time.
from services import feed_batch_store

# Which unit an upload is ingested as. PASSAGE stores each paragraph verbatim and
# attaches it to every node it populates; FACT is the older pipeline that cut a
# document into atomic claims and filed each at one node. Both are live so the
# two can be compared on the same input:
#
#     FEED_UNIT=fact python main.py     # the default
#     FEED_UNIT=passage python main.py  # the passage pipeline
#
# The passage WRITE path is verified — a 4-node direct write produced 4 rows with
# no collapse, and POST /api/feed/confirm with two node_ids produced 2 rows, 2
# sections, 1 text body. FACT is still the default for a different reason:
# **coverage**. On a four-block claim document passage mode attached 1 of 4
# blocks where fact mode found homes for 5 of its 8 fragments. The passage
# judge's span test is stricter, and against a candidate pool that lacks the
# right section it returns nothing rather than forcing a fit. Correct behaviour,
# but not something to ship as the live path on one reconstructed input.
#
# Flip only after both modes have been run on Alex's real document.
#
# Also still fact-shaped and untested in passage mode: the review queue at
# /feed/review and the batch-list view.
UNIT_PASSAGE = "passage"
UNIT_FACT = "fact"
DEFAULT_UNIT = UNIT_FACT


def feed_unit() -> str:
    """The ingest unit for this process, from FEED_UNIT. Defaults to passage."""
    unit = (os.getenv("FEED_UNIT") or DEFAULT_UNIT).strip().lower()
    if unit not in (UNIT_PASSAGE, UNIT_FACT):
        logger.warning(
            "FEED_UNIT=%r is not %r or %r — using %r",
            unit, UNIT_PASSAGE, UNIT_FACT, DEFAULT_UNIT,
        )
        return DEFAULT_UNIT
    return unit


# The architecture (801 leaf vectors) is loaded once and reused. Loading costs
# ~7.5s; doing it per upload would dominate the whole pipeline.
_feed_architecture = None
_feed_arch_lock = asyncio.Lock()


async def _get_feed_architecture():
    """Load and cache the BP architecture for the lifetime of the process."""
    global _feed_architecture
    async with _feed_arch_lock:
        if _feed_architecture is None:
            from services.feed_classifier_v3 import load_architecture

            _feed_architecture = await asyncio.to_thread(load_architecture)
    return _feed_architecture


async def _run_feed_pipeline(
    text: str, filename: str, session_key: str, run_id: str
) -> None:
    """Process one uploaded document in the background, then broadcast results.

    Runs detached from the upload request. The pipeline is synchronous and
    thread-parallel internally, so it goes to a worker thread to keep the event
    loop free for the WebSocket that will carry the result back.
    """
    unit = feed_unit()
    if unit == UNIT_PASSAGE:
        from services.passage_pipeline import process_document
    else:
        from services.feed_pipeline import process_document

    # The pipeline is synchronous and runs on a worker thread, so its progress
    # callback fires off-loop. Hop back onto this loop to reach the sockets.
    loop = asyncio.get_running_loop()

    def on_event(event: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(run_id, event), loop
        )

    try:
        arch = await _get_feed_architecture()
        batch = await asyncio.to_thread(
            process_document, text, filename, arch=arch, run_id=run_id,
            on_event=on_event,
        )
        payload = batch.to_dict()
        payload["unit"] = unit
        if not feed_batch_store.save_batch(run_id, payload):
            logger.warning(
                "Feed batch %s is process-local only — a restart will lose it", run_id
            )

        if unit == UNIT_PASSAGE:
            msg = (
                f"**{filename}** — {payload['total_passages']} passage(s) in "
                f"{payload['seconds']}s, ready to review.\n\n"
                f"**{payload['attached']}** have suggested nodes "
                f"({payload['total_attachments']} attachment(s) in total) · "
                f"**{payload['unattached']}** need you to pick\n\n"
                f"_Your text is stored exactly as written. Each passage is filed "
                f"at every node you confirm._"
            )
        else:
            msg = (
                f"**{filename}** — {payload['total_facts']} fact(s) in "
                f"{payload['seconds']}s, ready to review.\n\n"
                f"**{payload['proposed']}** have a suggested node · "
                f"**{payload['no_proposal']}** need you to pick one\n\n"
                f"Flagged: {payload['flagged_extraction']} by the extraction audit, "
                f"{payload['flagged_degraded']} pointing at an incomplete node.\n\n"
                f"_Nothing has been filed — each fact is stored when you confirm it._"
            )
    except Exception as exc:  # noqa: BLE001 — reported to the user, not swallowed
        logger.error("Feed pipeline failed for %s: %s", filename, exc, exc_info=True)
        feed_batch_store.save_batch(run_id, {"run_id": run_id, "error": str(exc)[:300]})
        await manager.broadcast(
            run_id, {"event": "failed", "run_id": run_id, "error": str(exc)[:300]}
        )
        msg = f"**{filename}** — processing failed: {str(exc)[:200]}"

    await manager.broadcast(
        session_key,
        {
            "role": "assistant",
            "text": msg,
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "system",
            "workspace": "feed",
            "run_id": run_id,
        },
    )


@app.get("/api/feed/batch/{run_id}")
async def get_feed_batch(run_id: str, token: str) -> dict:
    """Fetch a Feed batch of review cards by run id."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    batch = feed_batch_store.get_batch(run_id)
    if batch is None:
        return {"status": "processing", "run_id": run_id}
    return {"status": "done", "run_id": run_id, "batch": batch}


@app.get("/api/feed/batches")
async def list_feed_batches(token: str) -> dict:
    """List the review batches this process is holding."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"batches": feed_batch_store.list_batches()}


class ConfirmCardRequest(BaseModel):
    """Payload for POST /api/feed/confirm.

    `node_id` files one fact at one node — the fact pipeline's shape. `node_ids`
    files one passage at several — the passage pipeline's whole point, which a
    single `node_id` cannot express. Either may be sent; `node_ids` wins when
    both are, and a lone `node_id` is accepted in passage mode as a one-element
    list so an older client is not broken by the addition.
    """

    run_id: str
    index: int
    node_id: Optional[str] = None
    node_ids: Optional[list[str]] = None
    action: str = "confirm"  # confirm | none | skip
    confirmed_by: str = "reviewer"
    token: str

    def targets(self) -> list[str]:
        """The nodes to file at, in request order, de-duplicated."""
        chosen = self.node_ids if self.node_ids else ([self.node_id] if self.node_id else [])
        seen: list[str] = []
        for node in chosen:
            node = (node or "").strip()
            if node and node not in seen:
                seen.append(node)
        return seen


@app.post("/api/feed/confirm")
async def confirm_feed_card(req: ConfirmCardRequest) -> dict:
    """Resolve one review card.

    `confirm` stores the fact at the chosen node — the ONLY path that writes
    `knowledge_base.section`. `none` records that no candidate fitted, which is
    a signal about the architecture (a node may be missing) and not a failure to
    be retried silently. `skip` defers the card without recording anything.
    """
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    batch = feed_batch_store.get_batch(req.run_id)
    if not batch or "cards" not in batch:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    card_data = next(
        (c for c in batch["cards"] if c.get("index") == req.index), None
    )
    if card_data is None:
        raise HTTPException(status_code=404, detail="Unknown card index")

    if req.action in ("none", "skip"):
        card_data["status"] = "rejected" if req.action == "none" else "awaiting_confirmation"
        card_data["confirmed_by"] = req.confirmed_by if req.action == "none" else None
        feed_batch_store.update_card(req.run_id, req.index, card_data)
        logger.info(
            "Feed card %s/%d marked %s by %s",
            req.run_id,
            req.index,
            req.action,
            req.confirmed_by,
        )
        return {"success": True, "action": req.action, "card": card_data}

    targets = req.targets()
    if not targets:
        raise HTTPException(
            status_code=400, detail="node_id or node_ids required to confirm"
        )

    # Dispatch on the batch's own unit, not on the process default. A batch
    # ingested as passages must be confirmed as passages even if the server has
    # since been restarted with the other flag — the cards in Redis have the
    # shape they were written with, and outlive the environment variable.
    unit = batch.get("unit") or ("passage" if "attachments" in card_data else UNIT_FACT)

    import dataclasses

    if unit == UNIT_PASSAGE:
        from services.passage_pipeline import PassageCard, confirm_passage

        known = {f.name for f in dataclasses.fields(PassageCard)}
        card = PassageCard(**{k: v for k, v in card_data.items() if k in known})
        try:
            card = await asyncio.to_thread(
                confirm_passage, card, targets, req.confirmed_by, req.run_id
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the reviewer
            logger.error(
                "Passage confirm failed for %s/%d: %s", req.run_id, req.index, exc
            )
            raise HTTPException(status_code=500, detail=f"Store failed: {str(exc)[:200]}")

        feed_batch_store.update_card(req.run_id, req.index, card.to_dict())
        return {
            "success": True,
            "action": "confirm",
            "unit": unit,
            "stored": len(card.stored_ids),
            "nodes": card.confirmed_node_ids,
            "card": card.to_dict(),
        }

    if len(targets) > 1:
        raise HTTPException(
            status_code=400,
            detail="a fact files at one node; node_ids with more than one entry "
            "requires a passage batch",
        )

    from services.feed_pipeline import ReviewCard, confirm_card

    known = {f.name for f in dataclasses.fields(ReviewCard)}
    card = ReviewCard(**{k: v for k, v in card_data.items() if k in known})
    try:
        card = await asyncio.to_thread(
            confirm_card, card, targets[0], req.confirmed_by, req.run_id
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the reviewer
        logger.error("Feed confirm failed for %s/%d: %s", req.run_id, req.index, exc)
        raise HTTPException(status_code=500, detail=f"Store failed: {str(exc)[:200]}")

    feed_batch_store.update_card(req.run_id, req.index, card.to_dict())
    return {
        "success": True,
        "action": "confirm",
        "unit": unit,
        "stored": 1 if card.stored_id else 0,
        "nodes": [card.confirmed_node_id] if card.confirmed_node_id else [],
        "card": card.to_dict(),
    }


@app.get("/api/facts/by-node")
async def facts_by_node(token: str = "", populated_only: bool = False) -> dict:
    """Fact counts per architecture node, joined to the node tree.

    Counting happens here rather than in Postgres because PostgREST cannot
    express GROUP BY. `section` is indexed, but we need every node — including
    the empty ones, which are the interesting half of this view — so the query
    is a projection of one column over the table and the rollup is done in
    Python. At a few thousand rows that is cheaper than 912 round trips.
    """
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    def _work() -> dict:
        from collections import Counter

        from services.feed_classifier_v3 import get_architecture_client
        from services.rag_service import _get_supabase, TABLE_NAME

        nodes, start = [], 0
        arch_db = get_architecture_client()
        while True:
            resp = (
                arch_db.table("bp_architecture")
                .select("node_id,node_title,parent_node,degraded_target")
                .range(start, start + 999)
                .execute()
            )
            if not resp.data:
                break
            nodes += resp.data
            start += 1000

        rows, start = [], 0
        supabase = _get_supabase()
        while True:
            resp = (
                supabase.table(TABLE_NAME)
                .select("section,metadata,superseded_by")
                .range(start, start + 999)
                .execute()
            )
            if not resp.data:
                break
            rows += resp.data
            start += 1000

        counts: Counter = Counter()
        for row in rows:
            if row.get("superseded_by"):
                continue
            meta = row.get("metadata") or {}
            # Architecture index rows share source_type with real facts; they
            # are the retrieval index, not Alex's data.
            layer = meta.get("layer")
            if isinstance(layer, str) and layer.startswith("bp_architecture"):
                continue
            # `section` is where a confirmed fact lands. metadata.node_id is the
            # older path and is still the only marker on some legacy rows.
            node_id = row.get("section") or meta.get("node_id")
            if node_id:
                counts[node_id] += 1

        parents = {n["parent_node"] for n in nodes if n.get("parent_node")}
        out = []
        for n in nodes:
            nid = n["node_id"]
            if populated_only and not counts.get(nid):
                continue
            out.append({
                "node_id": nid,
                "title": n.get("node_title") or "",
                "parent": n.get("parent_node"),
                "depth": nid.count("."),
                "is_leaf": nid not in parents,
                "degraded": bool(n.get("degraded_target")),
                "facts": counts.get(nid, 0),
            })
        out.sort(key=lambda r: [int(p) if p.isdigit() else p
                                for p in r["node_id"].replace("BP.", "").split(".")])

        leaves = [r for r in out if r["is_leaf"]]
        unplaced = sum(v for k, v in counts.items()
                       if k not in {n["node_id"] for n in nodes})
        return {
            "nodes": out,
            "totals": {
                "nodes": len(nodes),
                "leaves": len(leaves),
                "populated": sum(1 for r in out if r["facts"]),
                "populated_leaves": sum(1 for r in leaves if r["facts"]),
                "facts": sum(counts.values()),
                "facts_outside_the_tree": unplaced,
                "degraded": sum(1 for r in out if r["degraded"]),
            },
        }

    try:
        return await asyncio.to_thread(_work)
    except Exception as e:
        logger.error("[Facts] by-node failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.get("/api/facts/node/{node_id:path}")
async def facts_for_node(node_id: str, token: str = "", limit: int = 200) -> dict:
    """Every stored fact filed at one node, with its provenance."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    def _work() -> dict:
        from services.rag_service import _get_supabase, TABLE_NAME

        supabase = _get_supabase()
        rows = (
            supabase.table(TABLE_NAME)
            .select("id,content,section,epistemic_status,source_type,run_id,"
                    "agent_name,metadata,created_at,superseded_by")
            .eq("section", node_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []

        facts = []
        for row in rows:
            if row.get("superseded_by"):
                continue
            meta = row.get("metadata") or {}
            facts.append({
                "id": row["id"],
                "content": row["content"],
                "epistemic_status": row.get("epistemic_status"),
                "source_type": row.get("source_type"),
                "created_at": row.get("created_at"),
                "run_id": row.get("run_id"),
                # provenance — written by feed_pipeline.confirm_card()
                "source_document": meta.get("source_document"),
                "source_quote": meta.get("source_quote"),
                "start_char": meta.get("start_char"),
                "end_char": meta.get("end_char"),
                # the two review signals, kept separate as ever
                "needs_review": meta.get("needs_review"),
                "verdict": meta.get("verdict"),
                "degraded_target": meta.get("degraded_target"),
                "degraded_reason": meta.get("degraded_reason"),
                # the confirmation label
                "proposed_node_id": meta.get("proposed_node_id"),
                "confirmed_by": meta.get("confirmed_by"),
                "rank_of_confirmed": meta.get("rank_of_confirmed"),
                "accepted_proposal": meta.get("accepted_proposal"),
            })
        return {"node_id": node_id, "count": len(facts), "facts": facts}

    try:
        return await asyncio.to_thread(_work)
    except Exception as e:
        logger.error("[Facts] node %s failed: %s", node_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:200])


class UndoCardRequest(BaseModel):
    """Payload for POST /api/feed/undo."""

    run_id: str
    index: int
    token: str


@app.post("/api/feed/undo")
async def undo_feed_card(req: UndoCardRequest) -> dict:
    """Retract a confirmed fact: delete the stored row, reopen the card.

    The counterpart to confirm_card(). Under review-assist every filing is a
    human decision, so a human needs a way to take one back — and the row must
    actually leave knowledge_base, not merely be unlinked from the card, or
    Build keeps reading a fact nobody stands behind.
    """
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    batch = feed_batch_store.get_batch(req.run_id)
    if not batch or "cards" not in batch:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    card = next((c for c in batch["cards"] if c.get("index") == req.index), None)
    if card is None:
        raise HTTPException(status_code=404, detail="Unknown card index")
    if card.get("status") != "confirmed":
        raise HTTPException(status_code=400, detail="Card is not confirmed")

    stored_id = card.get("stored_id")
    node = card.get("confirmed_node_id")

    def _work() -> bool:
        from services.rag_service import delete as rag_delete

        if not stored_id:
            # A duplicate confirmation returns SKIPPED_DUPLICATE with no id, so
            # there is no row of ours to remove. Reopening the card is still
            # correct; say so rather than reporting a delete that did not happen.
            return False
        return rag_delete(stored_id)

    try:
        deleted = await asyncio.to_thread(_work)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller
        logger.error("[Feed] undo failed for %s/%d: %s", req.run_id, req.index, exc)
        raise HTTPException(status_code=500, detail=f"Undo failed: {str(exc)[:200]}")

    card["status"] = "awaiting_confirmation"
    for key in ("confirmed_node_id", "confirmed_by", "confirmed_at",
                "rank_of_confirmed", "stored_id"):
        card[key] = None
    feed_batch_store.update_card(req.run_id, req.index, card)

    logger.info(
        "[Feed] undo %s/%d — row %s deleted=%s, card reopened (was %s)",
        req.run_id, req.index, stored_id, deleted, node,
    )
    return {"success": True, "row_deleted": deleted, "was_filed_at": node, "card": card}


@app.get("/feed/facts")
async def feed_facts_page():
    """The facts table — every stored fact against the architecture tree."""
    return _feed_page("facts_table.html")


def _feed_page(name: str) -> FileResponse:
    """Serve one of the Feed surfaces."""
    return FileResponse(
        str(STATIC_DIR / name),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/feed")
async def feed_upload_page():
    """Step 1 — drop a document in."""
    return _feed_page("feed_upload.html")


@app.get("/feed/results")
async def feed_results_page():
    """Step 2 — the batch split: filed / ready / flagged / no match."""
    return _feed_page("feed_results.html")


@app.get("/feed/review")
async def feed_review_page():
    """Step 3 — the review queue, optionally scoped by ?filter=."""
    return _feed_page("feed_review.html")


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
        batch = {"total_facts": 0, "run_id": None}
        if text and text.strip():
            run_id = f"feed-{uuid.uuid4().hex[:12]}"
            batch = {"total_facts": None, "run_id": run_id, "status": "processing"}
            # Fire and forget: chunking + classification take ~20s for a
            # 20-fact document, far too long to hold the request open. The
            # batch lands over the WebSocket when it's done.
            asyncio.create_task(
                _run_feed_pipeline(text, filename, session_key, run_id)
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

    if batch.get("run_id") is None:
        await manager.broadcast(
            session_key,
            {
                "role": "assistant",
                "text": f"No extractable text found in {filename}.",
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
                f"**{filename}** received — extracting and filing facts. "
                f"Results will appear here when it finishes."
            ),
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "system",
            "workspace": "feed",
        },
    )
    return {
        "status": "processing",
        "session_key": session_key,
        "run_id": batch["run_id"],
        "batch": batch,
    }


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

    # Feed handler removed — rebuild pending.
    result = {"error": "Feed workspace is being rebuilt. Check back soon."}

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


@app.get("/api/quarantine")
async def get_quarantine(token: str = "") -> dict:
    """Return all quarantined facts for the current session."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Feed handler removed — rebuild pending.
    session_key = _get_session_key()
    items: list = []
    count = 0

    return {"count": count, "items": items, "session_id": session_key}


@app.post("/api/quarantine/resolve")
async def resolve_quarantine(token: str = "", index: int = 0, action: str = "skip") -> dict:
    """Resolve a quarantined fact by index (approve/skip)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Feed handler removed — rebuild pending.
    return {"status": "Feed workspace is being rebuilt. Check back soon."}


@app.get("/api/bp12/register")
async def get_bp12_register(
    token: str = "", item_type: str = "", severity: str = "", limit: int = 50
) -> dict:
    """Return open BP.12 register items (unresolved governance issues)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from services.bp12_register import get_open_items

    items = get_open_items(
        item_type=item_type or None,
        severity=severity or None,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@app.post("/api/bp12/resolve")
async def resolve_bp12_item(
    token: str = "", item_id: str = "", decision: str = "", reasoning: str = ""
) -> dict:
    """Resolve a BP.12 register item with a controller decision."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not item_id or not decision:
        raise HTTPException(status_code=400, detail="item_id and decision required")

    from services.bp12_register import resolve_item

    success = resolve_item(item_id, decision, reasoning)
    if success:
        return {"status": "resolved", "item_id": item_id, "decision": decision}
    raise HTTPException(status_code=500, detail="Failed to resolve item")


@app.get("/api/evidence-links/{node_id:path}")
async def get_evidence_links_for_node(node_id: str, token: str = "") -> dict:
    """Return all evidence links targeting a node (what evidence supports it)."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    from services.evidence_links import get_links_for_node

    links = get_links_for_node(node_id)
    return {"node_id": node_id, "links": links, "count": len(links)}


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
    needs_review: bool = False,
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
            # Skip architecture embedding-index rows — they are the retrieval index
            # (metadata.layer='bp_architecture'/'bp_architecture_aug'), not Alex's
            # facts, and must never appear in the Stored Data table as if they were.
            _layer = meta.get("layer")
            if isinstance(_layer, str) and _layer.startswith("bp_architecture"):
                continue
            row_node_id = meta.get("node_id") or row.get("section") or ""
            if node_id and row_node_id != node_id:
                continue
            if needs_review and not meta.get("needs_review"):
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
                "tier_decision": meta.get("tier_decision", ""),
                "needs_review": meta.get("needs_review", False),
                "secondary_node_ids": meta.get("secondary_node_ids", []),
                "evidence_use_boundary": meta.get("evidence_use_boundary", ""),
            })

        if node_sort:
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
            # Skip architecture embedding-index rows (metadata.layer) — index, not facts.
            _layer = meta.get("layer")
            if isinstance(_layer, str) and _layer.startswith("bp_architecture"):
                continue
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

    # Topic is a bare slug: it is a grouping key in metadata and (historically)
    # a path component, so anything with a separator is rejected outright.
    if not _TOPIC_RE.match(req.topic):
        raise HTTPException(
            status_code=400,
            detail="Invalid topic — use lowercase letters, digits and underscores only",
        )

    # Facts go to Supabase, never to ceo_data/*.json: Railway's filesystem is
    # ephemeral, so a local write is lost on the next deploy. ceo_data/*.json
    # stays read-only seed data; load_all_ceo_data() merges these rows back in.
    from services.rag_service import RagStoreError, StoreOutcome, store

    try:
        result = await asyncio.to_thread(
            store,
            content=req.fact.strip(),
            source_type="ceo_doc",
            epistemic_status=req.status,
            topic_tags=["ceo-fact", "manual-entry", req.topic],
            metadata={"topic": req.topic, "origin": "web_add_fact"},
        )
    except RagStoreError as e:
        logger.error(f"Error adding fact to {req.topic}: {e}")
        raise HTTPException(status_code=500, detail="Fact was not stored")
    except Exception as e:
        logger.error(f"Error adding fact: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add fact: {str(e)}")

    if result.outcome is StoreOutcome.SKIPPED_DUPLICATE:
        logger.info(f"Fact already in {req.topic}: {req.fact[:50]}...")
        return {
            "success": True,
            "duplicate": True,
            "topic": req.topic,
            "fact_added": req.fact,
            "chunk_id": result.duplicate_of,
        }

    logger.info(f"Added fact to {req.topic}: {req.fact[:50]}...")
    return {
        "success": True,
        "topic": req.topic,
        "fact_added": req.fact,
        "chunk_id": result.id,
    }


# ─── Stored Data Mutations ──────────────────────────────────────────────────


def _record_move_correction(
    content: str, from_node: Optional[str], to_node: str
) -> None:
    """Log a human re-filing into feed_corrections.

    The table exists in Supabase (its migration was deleted from the repo along
    with the old handler, but the table itself survived). Every move is a
    labelled example — what was suggested vs where it actually belongs — and
    that is the accumulation the auto-file decision depends on. Failure here is
    logged, never raised: losing a training label must not fail the move.
    """
    if not from_node or from_node == to_node:
        return
    try:
        # feed_corrections has RLS on and the anon role cannot insert, so this
        # needs the service-role client — the same reason bp_architecture does.
        from services.feed_classifier_v3 import get_architecture_client

        get_architecture_client().table("feed_corrections").insert({
            "original_node_id": from_node,
            "corrected_node_id": to_node,
            "fact_content": content[:2000],
            "correction_type": "corrected",
            "session_id": _get_session_key(),
        }).execute()
    except Exception as e:  # noqa: BLE001 — a lost label must not fail the move
        logger.warning("[Facts] could not record move correction: %s", str(e)[:160])


class StoredDataUpdateRequest(BaseModel):
    """Payload for PATCH /api/knowledge/stored/{id}."""

    token: str
    content: Optional[str] = None
    epistemic_status: Optional[str] = None
    node_id: Optional[str] = None


class StoredDataBulkRequest(BaseModel):
    """Payload for POST /api/knowledge/stored/bulk-action."""

    token: str
    ids: list[str]
    action: str  # "update_status" | "delete"
    value: Optional[str] = None  # new status for update_status


@app.patch("/api/knowledge/stored/{row_id}")
async def update_stored_row(row_id: str, req: StoredDataUpdateRequest):
    """Inline edit a single knowledge_base row."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        updates = {}
        if req.content is not None:
            updates["content"] = req.content.strip()
        if req.epistemic_status is not None:
            valid = ["CONFIRMED", "ASSUMPTION", "CONTRADICTION", "MISSING", "KILLED"]
            if req.epistemic_status not in valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status. Must be one of: {valid}",
                )
            updates["epistemic_status"] = req.epistemic_status
        if req.node_id is not None:
            meta_update = True
        else:
            meta_update = False

        if not updates and not meta_update:
            raise HTTPException(status_code=400, detail="No fields to update")

        def _do_update():
            supabase = _get_supabase()
            moved_from = None
            if meta_update:
                current = (
                    supabase.table(TABLE_NAME)
                    .select("metadata, section, content")
                    .eq("id", row_id)
                    .single()
                    .execute()
                )
                meta = current.data.get("metadata") or {}
                moved_from = current.data.get("section") or meta.get("node_id")
                meta["node_id"] = req.node_id
                meta["moved_from"] = moved_from
                updates["metadata"] = meta
                # `section` is the indexed column every reader filters on —
                # retrieve(section=...), the facts table, coverage. Writing only
                # metadata.node_id left a moved fact retrievable at its OLD node
                # and invisible at its new one.
                updates["section"] = req.node_id
                _record_move_correction(
                    current.data.get("content") or "", moved_from, req.node_id
                )

            result = (
                supabase.table(TABLE_NAME)
                .update(updates)
                .eq("id", row_id)
                .execute()
            )
            return result.data

        data = await asyncio.to_thread(_do_update)
        if not data:
            raise HTTPException(status_code=404, detail="Row not found")

        logger.info("[StoredData] Updated row %s: %s", row_id, list(updates.keys()))
        return {"success": True, "updated": list(updates.keys())}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[StoredData] Update error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/knowledge/stored/bulk-action")
async def bulk_action_stored(req: StoredDataBulkRequest):
    """Bulk update status or delete multiple knowledge_base rows."""
    if req.token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not req.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")

    if len(req.ids) > 100:
        raise HTTPException(status_code=400, detail="Max 100 rows per batch")

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        def _do_bulk():
            supabase = _get_supabase()
            if req.action == "update_status":
                valid = ["CONFIRMED", "ASSUMPTION", "CONTRADICTION", "MISSING", "KILLED"]
                if req.value not in valid:
                    raise ValueError(f"Invalid status: {req.value}")
                result = (
                    supabase.table(TABLE_NAME)
                    .update({"epistemic_status": req.value})
                    .in_("id", req.ids)
                    .execute()
                )
                return len(result.data) if result.data else 0
            elif req.action == "delete":
                result = (
                    supabase.table(TABLE_NAME)
                    .delete()
                    .in_("id", req.ids)
                    .execute()
                )
                return len(result.data) if result.data else 0
            else:
                raise ValueError(f"Unknown action: {req.action}")

        count = await asyncio.to_thread(_do_bulk)
        logger.info(
            "[StoredData] Bulk %s on %d rows (affected: %d)",
            req.action, len(req.ids), count,
        )
        return {"success": True, "action": req.action, "affected": count}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[StoredData] Bulk action error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Side Panel APIs ────────────────────────────────────────────────────────


@app.get("/api/panel/node/{node_id:path}")
async def get_node_detail(node_id: str, token: str = ""):
    """Return full detail for a node: metadata, children, all facts stored under it."""
    if token != WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        from services.rag_service import _get_supabase, TABLE_NAME

        arch = _load_bp_architecture()
        node_info = next((n for n in arch if n.get("node_id") == node_id), None)

        children = [
            {"node_id": n["node_id"], "name": n.get("name", "")}
            for n in arch
            if n.get("parent_id") == node_id
        ]

        def _fetch_facts():
            supabase = _get_supabase()
            result = (
                supabase.table(TABLE_NAME)
                .select(
                    "id, content, epistemic_status, confidence, "
                    "source_type, metadata, created_at"
                )
                .is_("superseded_by", "null")
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            facts = []
            for row in result.data or []:
                meta = row.get("metadata") or {}
                if meta.get("node_id") == node_id or row.get("section") == node_id:
                    facts.append({
                        "id": row["id"],
                        "content": row["content"],
                        "epistemic_status": row.get("epistemic_status", ""),
                        "confidence": row.get("confidence"),
                        "source_type": row.get("source_type", ""),
                        "created_at": row.get("created_at", ""),
                    })
            return facts

        facts = await asyncio.to_thread(_fetch_facts)

        status_breakdown = {}
        for f in facts:
            s = f["epistemic_status"] or "UNKNOWN"
            status_breakdown[s] = status_breakdown.get(s, 0) + 1

        return {
            "node_id": node_id,
            "name": node_info.get("name", "") if node_info else "",
            "parent_id": node_info.get("parent_id", "") if node_info else "",
            "depth": node_info.get("depth", 0) if node_info else 0,
            "children": children,
            "facts": facts,
            "total_facts": len(facts),
            "status_breakdown": status_breakdown,
            "last_updated": facts[0]["created_at"] if facts else None,
        }

    except Exception as e:
        logger.error("[Panel] Node detail error for %s: %s", node_id, e)
        raise HTTPException(status_code=500, detail=str(e))
