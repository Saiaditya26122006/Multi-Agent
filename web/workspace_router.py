"""
Workspace Router — tracks and dispatches based on active workspace per session.

Each session has one active workspace at a time. The router manages transitions,
dispatches messages to the correct handler, and handles meta-commands (back, menu).
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

WORKSPACE_TTL = 86400  # 24 hours


class Workspace(str, Enum):
    """Available workspace modes (simplified to 3 core workspaces)."""

    FEED = "feed"
    BUILD = "build"
    AUTO = "auto"


WORKSPACE_LABELS = {
    Workspace.FEED: "Feed Data",
    Workspace.BUILD: "Build Plan",
    Workspace.AUTO: "Auto & Ask",
}

WORKSPACE_DESCRIPTIONS = {
    Workspace.FEED: "Give me new information → auto-classifies into business plan",
    Workspace.BUILD: "Generate and refine business plan sections (autonomous quality loop)",
    Workspace.AUTO: "Inspect coverage, challenge assumptions, export plan, ask anything",
}

WORKSPACE_BY_NUMBER = {
    "1": Workspace.FEED,
    "2": Workspace.BUILD,
    "3": Workspace.AUTO,
}

# Legacy workspace mappings (for backward compatibility)
LEGACY_WORKSPACES = {
    "inspect": Workspace.AUTO,
    "challenge": Workspace.AUTO,
    "validate": Workspace.AUTO,
    "export": Workspace.AUTO,
}

META_COMMANDS = {"back", "menu", "home", "/menu", "/back", "/home"}


def _redis_key(session_id: str) -> str:
    return f"workspace:{session_id}"


def _get_redis_client():
    """Get the Upstash Redis client (lazy import to avoid circular deps)."""
    from memory.redis_client import redis_client
    return redis_client


def get_workspace(session_id: str) -> Workspace:
    """Return the current active workspace for a session.

    Args:
        session_id: The session identifier.

    Returns:
        The active Workspace enum value. Defaults to AUTO if not set.
    """
    try:
        r = _get_redis_client()
        value = r.get(_redis_key(session_id))
        if value and value in [w.value for w in Workspace]:
            return Workspace(value)
    except Exception as e:
        logger.error("[WorkspaceRouter] Redis error in get_workspace: %s", e)
    return Workspace.AUTO


def set_workspace(session_id: str, workspace: Workspace) -> None:
    """Switch the active workspace for a session.

    Args:
        session_id: The session identifier.
        workspace: The workspace to switch to.
    """
    try:
        r = _get_redis_client()
        r.set(_redis_key(session_id), workspace.value, ex=WORKSPACE_TTL)
        logger.info(
            "[WorkspaceRouter] Session %s switched to workspace: %s",
            session_id,
            workspace.value,
        )
    except Exception as e:
        logger.error("[WorkspaceRouter] Redis error in set_workspace: %s", e)


def is_meta_command(text: str) -> bool:
    """Check if the message is a meta navigation command.

    Args:
        text: The raw message text.

    Returns:
        True if this is a back/menu/home command.
    """
    return text.strip().lower() in META_COMMANDS


def is_workspace_switch(text: str) -> Optional[Workspace]:
    """Check if the message is a workspace switch command (number or name).

    Args:
        text: The raw message text.

    Returns:
        The target Workspace if this is a switch command, None otherwise.
    """
    cleaned = text.strip().lower()

    if cleaned in WORKSPACE_BY_NUMBER:
        return WORKSPACE_BY_NUMBER[cleaned]

    for ws in Workspace:
        if cleaned == ws.value:
            return ws
        if cleaned == WORKSPACE_LABELS[ws].lower():
            return ws

    # Names of the workspaces that were folded into Auto & Ask still route there,
    # so habits learned from the old 7-workspace menu keep working.
    if cleaned in LEGACY_WORKSPACES:
        return LEGACY_WORKSPACES[cleaned]

    return None


def dispatch(
    session_id: str,
    message: str,
) -> dict:
    """Route a message based on the active workspace and meta-commands.

    This is the main entry point. It checks for meta-commands and workspace
    switches first, then routes to the active workspace handler.

    Args:
        session_id: The session identifier.
        message: The raw message text from Alex.

    Returns:
        A dict with:
            - action: "show_menu" | "switch_workspace" | "handle_message"
            - workspace: the active/target workspace
            - data: any additional data for the action
    """
    text = message.strip()

    if is_meta_command(text):
        set_workspace(session_id, Workspace.AUTO)
        return {
            "action": "show_menu",
            "workspace": Workspace.AUTO,
            "data": None,
        }

    target = is_workspace_switch(text)
    if target is not None:
        set_workspace(session_id, target)
        return {
            "action": "switch_workspace",
            "workspace": target,
            "data": {"label": WORKSPACE_LABELS[target]},
        }

    current = get_workspace(session_id)
    return {
        "action": "handle_message",
        "workspace": current,
        "data": {"message": text},
    }


_active_builds: set = set()


def is_build_active(session_id: str) -> bool:
    """Check if a pipeline build is currently running for this session."""
    return session_id in _active_builds


def mark_build_started(session_id: str) -> None:
    """Mark that a build is running for this session."""
    _active_builds.add(session_id)
    logger.info("[WorkspaceRouter] Build started for session %s", session_id)


def mark_build_completed(session_id: str) -> None:
    """Mark that a build has completed for this session."""
    _active_builds.discard(session_id)
    logger.info("[WorkspaceRouter] Build completed for session %s", session_id)
