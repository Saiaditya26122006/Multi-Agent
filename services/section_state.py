"""Build v2 — section-state backbone (Phase 1).

The unit of work is the SECTION. Each has a durable status/draft that survives
days, so Alex builds the plan in installments. A section is runnable when its
dependencies are `done` (dependency DAG from config/phase2/bp_sections.yaml).

Storage: the dedicated `bp_sections` table (database/migrations/004, applied) —
one row per (session_id, section_id).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "phase2" / "bp_sections.yaml"

VALID_STATUS = {
    "not_started", "in_progress", "blocked_on_data", "needs_review", "done", "failed",
}
# Allowed transitions (None-from = initial seed). Kept liberal but non-nonsensical.
_TRANSITIONS = {
    "not_started": {"in_progress", "blocked_on_data"},
    "in_progress": {"blocked_on_data", "needs_review", "done", "failed", "not_started"},
    "blocked_on_data": {"in_progress", "not_started"},
    "needs_review": {"in_progress", "done", "failed"},
    "done": {"in_progress", "needs_review"},   # re-open for adjust
    "failed": {"in_progress", "not_started"},
}

_STATUS_BADGE = {
    "not_started": "⬜ not started",
    "in_progress": "🚧 in progress",
    "blocked_on_data": "⛔ blocked on data",
    "needs_review": "🔎 needs review",
    "done": "✅ done",
    "failed": "❌ failed",
}


# ── registry (pure, no DB) ────────────────────────────────────────────────────

def load_registry() -> dict:
    """Section registry: {section_id: {title, agent, depends_on}}."""
    data = yaml.safe_load(_REGISTRY_PATH.read_text())
    return data.get("sections", {})


def valid_transition(frm: Optional[str], to: str) -> bool:
    if to not in VALID_STATUS:
        return False
    if frm is None:
        return True
    return to in _TRANSITIONS.get(frm, set())


def deps_met(section_id: str, states: dict, registry: dict) -> bool:
    """True if every dependency of section_id is `done` in states."""
    deps = registry.get(section_id, {}).get("depends_on", [])
    return all(states.get(d, {}).get("status") == "done" for d in deps)


# ── storage (dedicated bp_sections table; migration 004) ──────────────────────

def _get_sb():
    """Client for bp_sections. The table has RLS on and this is server-managed
    build state, so use the service-role key (bypasses RLS) when available;
    fall back to the anon client otherwise."""
    import os

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        if not hasattr(_get_sb, "_client"):
            from supabase import create_client

            _get_sb._client = create_client(url, key)
        return _get_sb._client
    from services.rag_service import _get_supabase

    return _get_supabase()


def _load(session_id: str) -> dict:
    rows = _get_sb().table("bp_sections").select("*").eq("session_id", session_id).execute().data or []
    return {r["section_id"]: r for r in rows}


# ── public API ────────────────────────────────────────────────────────────────

def init_sections(session_id: str) -> dict:
    """Seed every registry section as not_started (idempotent — keeps existing)."""
    registry = load_registry()
    existing = _load(session_id)
    to_insert = [
        {
            "session_id": session_id,
            "section_id": sid,
            "title": meta.get("title", sid),
            "agent": meta.get("agent"),
            "status": "not_started",
            "draft": None,
            "blocked_on": None,
            "depends_on": meta.get("depends_on", []),
            "version": 0,
        }
        for sid, meta in registry.items()
        if sid not in existing
    ]
    if to_insert:
        _get_sb().table("bp_sections").insert(to_insert).execute()
    return _load(session_id)


def effective_registry(session_id: str) -> dict:
    """The static registry plus any custom sections Alex added this session.

    Custom sections live only as bp_sections rows (section_id not in the YAML);
    they carry their own title/agent/depends_on, so they behave like built-ins
    for the DAG, assembly, and next-actions.
    """
    reg = dict(load_registry())
    for sid, row in _load(session_id).items():
        if sid not in reg:
            reg[sid] = {
                "title": row.get("title", sid),
                "agent": row.get("agent"),
                "depends_on": row.get("depends_on") or [],
            }
    return reg


def add_custom_section(
    session_id: str,
    title: str,
    agent: str = "generic_analyst",
    depends_on: Optional[list] = None,
) -> dict:
    """Register a custom section for this session (independent DAG node).

    Returns the new section row. Section id is 'c<N>' so it never collides with
    the numeric built-in ids. Defaults to no dependencies so it can run anytime
    and nothing depends on it — it can't stall the built-in waves.
    """
    init_sections(session_id)
    existing = _load(session_id)
    n = 1
    while f"c{n}" in existing:
        n += 1
    sid = f"c{n}"
    row = {
        "session_id": session_id,
        "section_id": sid,
        "title": title.strip()[:200] or sid,
        "agent": agent,
        "status": "not_started",
        "draft": None,
        "blocked_on": None,
        "depends_on": depends_on or [],
        "version": 0,
    }
    _get_sb().table("bp_sections").insert(row).execute()
    logger.info("[SectionState] added custom section %s (%s) for %s", sid, title, session_id)
    return row


def get_section(session_id: str, section_id: str) -> Optional[dict]:
    return _load(session_id).get(section_id)


def list_sections(session_id: str) -> dict:
    return _load(session_id)


def update_section(session_id: str, section_id: str, **fields) -> dict:
    """Update a section's fields (status validated). Bumps version + timestamp."""
    states = _load(session_id)
    if section_id not in states:
        init_sections(session_id)
        states = _load(session_id)
    sec = states.get(section_id) or {"section_id": section_id, "status": "not_started", "version": 0}

    if "status" in fields:
        new = fields["status"]
        if not valid_transition(sec.get("status"), new):
            raise ValueError(f"invalid transition {sec.get('status')} -> {new} for {section_id}")

    update = dict(fields)
    update["version"] = (sec.get("version") or 0) + 1
    update["last_updated"] = datetime.now(timezone.utc).isoformat()
    _get_sb().table("bp_sections").update(update).eq(
        "session_id", session_id
    ).eq("section_id", section_id).execute()
    return {**sec, **update}


def ready_sections(session_id: str) -> list[str]:
    """Sections that are not_started/blocked and whose dependencies are all done."""
    registry = effective_registry(session_id)
    states = _load(session_id) or init_sections(session_id)
    out = []
    for sid in registry:
        st = states.get(sid, {}).get("status", "not_started")
        if st in ("not_started", "blocked_on_data") and deps_met(sid, states, registry):
            out.append(sid)
    return out


def assemble(session_id: str) -> dict:
    """Compile the current plan: per-section status + a rendered overview.

    Cheap (a view over state, not a re-run). Returns counts, a markdown overview
    with badges, and the section drafts that exist.
    """
    registry = effective_registry(session_id)
    states = _load(session_id) or {}
    counts: dict[str, int] = {}
    lines = ["# Business Plan — current state", ""]
    for sid, meta in registry.items():
        st = states.get(sid, {}).get("status", "not_started")
        counts[st] = counts.get(st, 0) + 1
        blocked = states.get(sid, {}).get("blocked_on")
        suffix = f" — needs: {blocked}" if (st == "blocked_on_data" and blocked) else ""
        lines.append(f"- **{sid}. {meta.get('title', sid)}** — {_STATUS_BADGE.get(st, st)}{suffix}")
    done = counts.get("done", 0)
    total = len(registry)
    lines.insert(1, f"Progress: {done}/{total} sections done")
    return {
        "counts": counts,
        "done": done,
        "total": total,
        "overview_markdown": "\n".join(lines),
        "sections": states,
    }
