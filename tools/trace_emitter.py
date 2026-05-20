"""
Trace emitter — broadcasts real-time pipeline step events over WebSocket.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def emit_trace(
    session_key: str,
    agent: str,
    step: str,
    detail: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Broadcast a trace event over WebSocket. Non-blocking, never crashes the pipeline.

    Args:
        session_key: str(telegram_chat_id) — the ConnectionManager key
        agent: Agent identifier (e.g. "L0", "L1", "L3", "Router", "Memory")
        step: Machine-readable step name (e.g. "calling_llm")
        detail: Human-readable description (e.g. "Generating clarifying question...")
        data: Optional dict of metadata to expose in the trace UI
    """
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
            loop.create_task(_safe_broadcast(manager, session_key, message))
        else:
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
