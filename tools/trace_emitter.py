"""
Trace emitter — broadcasts real-time pipeline step events over WebSocket
and persists them to the events_logs table in Supabase.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The FastAPI app's main event loop, captured once at startup (see
# set_main_loop(), called from web/server.py's startup handler). Needed
# because emit_trace() is also called from worker threads (e.g. Feed's
# document extraction runs via asyncio.to_thread() so it doesn't block the
# loop) — a plain `asyncio.run(...)` from a worker thread would spin up an
# unrelated event loop and silently fail to reach the real WebSocket
# connections, which live on the main loop.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the main event loop so worker threads can broadcast to it."""
    global _main_loop
    _main_loop = loop


def _persist_trace(
    session_key: str, agent: str, step: str, detail: str, data: Dict[str, Any]
) -> None:
    """Write trace event to events_logs table. Never raises."""
    try:
        from memory.supabase_client import log_event

        log_event(
            agent_id=agent,
            action=step,
            session_id=session_key,
            state_before=None,
            state_after=None,
            input_ref=detail,
            output_ref=json.dumps(data, default=str)[:500] if data else None,
        )
    except Exception:
        pass


def emit_trace(
    session_key: str,
    agent: str,
    step: str,
    detail: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Broadcast a trace event over WebSocket and persist to events_logs.
    Non-blocking, never crashes the pipeline.

    Safe to call from either the main event loop thread (e.g. a request
    handler) or a worker thread (e.g. code run via asyncio.to_thread()) —
    in both cases the broadcast is scheduled on the main loop so it actually
    reaches connected WebSocket clients.

    Args:
        session_key: str(telegram_chat_id) — the ConnectionManager key
        agent: Agent identifier (e.g. "L0", "L1", "L3", "Router", "Memory")
        step: Machine-readable step name (e.g. "calling_llm")
        detail: Human-readable description (e.g. "Generating clarifying question...")
        data: Optional dict of metadata to expose in the trace UI
    """
    _persist_trace(session_key, agent, step, detail, data or {})

    try:
        from web.server import manager

        message = {
            "role": "trace",
            "agent": agent,
            "step": step,
            "detail": detail,
            "data": data or {},
            "timestamp": datetime.utcnow().isoformat(),
        }

        loop = _get_event_loop()
        if loop and loop.is_running():
            # We're already on a running loop (a normal async request
            # handler) — cache it in case a worker thread needs it later.
            global _main_loop
            if _main_loop is None:
                _main_loop = loop
            loop.create_task(_safe_broadcast(manager, session_key, message))
        elif _main_loop is not None and _main_loop.is_running():
            # Called from a worker thread — hand the coroutine to the main
            # loop instead of creating a disconnected one of our own.
            asyncio.run_coroutine_threadsafe(
                _safe_broadcast(manager, session_key, message), _main_loop
            )
        else:
            # No running loop anywhere we know of (e.g. a standalone
            # script) — best effort, isolated loop.
            asyncio.run(_safe_broadcast(manager, session_key, message))
    except Exception:
        pass


async def _safe_broadcast(manager, session_key: str, message: dict) -> None:
    """Broadcast with exception swallowing."""
    try:
        await manager.broadcast(session_key, message)
    except Exception:
        pass


def _get_event_loop():
    """Get the running event loop or None."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
