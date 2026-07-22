"""Build v2 — section-state backbone (Phase 1).

The unit of work is the SECTION. Each has a durable status/draft that survives
days, so Alex builds the plan in installments. A section is runnable when its
dependencies are `done` (dependency DAG from config/phase2/bp_sections.yaml).

Storage: interim, per-session JSONB under sessions.archived_state["bp_sections"]
(merge-safe read-modify-write — works today with no DDL). Production target is a
dedicated table: database/migrations/004_add_bp_sections.sql. Swap _load/_save
to the table once the migration is applied; the public API is unchanged.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "phase2" / "bp_sections.yaml"
_STATE_KEY = "bp_sections"  # namespaced key inside sessions.archived_state

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


# ── storage (interim: sessions.archived_state) ────────────────────────────────

def _load(session_id: str) -> dict:
    from services.rag_service import _get_supabase

    sb = _get_supabase()
    row = sb.table("sessions").select("archived_state").eq("id", session_id).limit(1).execute()
    if not row.data:
        return {}
    archived = row.data[0].get("archived_state") or {}
    return archived.get(_STATE_KEY) or {}


def _save(session_id: str, states: dict) -> bool:
    from services.rag_service import _get_supabase

    sb = _get_supabase()
    row = sb.table("sessions").select("archived_state").eq("id", session_id).limit(1).execute()
    archived = (row.data[0].get("archived_state") if row.data else None) or {}
    archived[_STATE_KEY] = states  # merge-safe: preserve other archival keys
    sb.table("sessions").update({"archived_state": archived}).eq("id", session_id).execute()
    return True


# ── public API ────────────────────────────────────────────────────────────────

def init_sections(session_id: str) -> dict:
    """Seed every registry section as not_started (idempotent — keeps existing)."""
    registry = load_registry()
    states = _load(session_id)
    for sid, meta in registry.items():
        if sid not in states:
            states[sid] = {
                "section_id": sid,
                "title": meta.get("title", sid),
                "agent": meta.get("agent"),
                "status": "not_started",
                "draft": None,
                "blocked_on": None,
                "version": 0,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
    _save(session_id, states)
    return states


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

    sec.update(fields)
    sec["version"] = sec.get("version", 0) + 1
    sec["last_updated"] = datetime.now(timezone.utc).isoformat()
    states[section_id] = sec
    _save(session_id, states)
    return sec


def ready_sections(session_id: str) -> list[str]:
    """Sections that are not_started/blocked and whose dependencies are all done."""
    registry = load_registry()
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
    registry = load_registry()
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
