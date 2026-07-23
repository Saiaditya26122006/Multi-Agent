"""Build v2 — read-side views for the threaded Build UX (Phases 1–3).

Everything here is a *view* over durable Build state (the `bp_sections` row and
`data_requests`) — no re-runs, no `events_logs` coupling. Three public views:

- ``section_thread`` — one section rendered as a chronological conversation
  (assignment → data requests → agent draft → grounding → Council critique).
- ``section_card`` — the compact approval-card summary (scope line + evidence
  badges + Council chip) shown at the top of a thread and on the roster.
- ``board_snapshot`` — a lightweight whole-board state blob for the live SSE
  stream (Phase 2).
- ``focus_options`` — LLM-proposed tap-to-answer focus choices for a section
  kickoff (Phase 3).
"""

import json
import logging
from typing import Any, Optional

from services import section_state

logger = logging.getLogger(__name__)

# Every writing agent has the same three data-gathering tools, attached at the
# base-agent level — surfaced on the card as "what this specialist will use".
_TOOLS = ["search", "data", "ask"]

# Keys we never surface as section content (agent/LLM bookkeeping).
_META_KEYS = {
    "task_id",
    "model_used",
    "input_tokens",
    "output_tokens",
    "section_number",
    "reasoning_trace",
    "_fallback_used",
}

# Output keys that read as a one-line "what this covers", in priority order.
_SCOPE_KEYS = (
    "summary",
    "overview",
    "headline",
    "thesis",
    "positioning",
    "objective",
    "description",
    "value_proposition",
)


def _output_of(sec: dict) -> Any:
    """Pull the agent's structured output out of a section's stored draft."""
    draft = sec.get("draft")
    if isinstance(draft, dict) and "output" in draft:
        return draft.get("output")
    return draft


def _scope_line(output: Any) -> str:
    """A one-sentence 'what this section covers' for the card."""
    if isinstance(output, str):
        return output.strip()[:180]
    if isinstance(output, dict):
        for key in _SCOPE_KEYS:
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:180]
        topics = [
            k.replace("_", " ")
            for k in output.keys()
            if not k.startswith("_") and k not in _META_KEYS
        ]
        if topics:
            return "Covers: " + ", ".join(topics[:4])
    return ""


def _render_output(output: Any, max_chars: int = 2000) -> str:
    """Render a section's structured output as compact markdown for the thread."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output[:max_chars]
    if not isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False)[:max_chars]

    parts: list[str] = []
    for key, val in output.items():
        if key.startswith("_") or key in _META_KEYS or not val:
            continue
        label = key.replace("_", " ").title()
        if isinstance(val, list):
            items = [
                json.dumps(i, ensure_ascii=False) if isinstance(i, (dict, list)) else str(i)
                for i in val[:8]
            ]
            parts.append(f"**{label}**\n" + "\n".join(f"- {i[:300]}" for i in items))
        elif isinstance(val, dict):
            parts.append(f"**{label}**\n" + json.dumps(val, ensure_ascii=False)[:600])
        else:
            parts.append(f"**{label}** — {str(val)[:600]}")
    text = "\n\n".join(parts)
    return text[:max_chars] if text else "_(no structured content)_"


def _grounding_badges(grounding: Optional[dict]) -> list[dict]:
    """Evidence badges — Oasis's 'connect these tools' slot, re-purposed as
    'how evidence-backed is this'."""
    if not isinstance(grounding, dict) or not grounding.get("available"):
        return []
    badges: list[dict] = []
    rate = grounding.get("rate")
    if rate is not None:
        pct = int(rate * 100)
        tone = "good" if rate >= 0.7 else ("warn" if rate >= 0.4 else "bad")
        badges.append({"label": f"{pct}% evidence-backed", "tone": tone})
    n_ungrounded = len(grounding.get("ungrounded") or [])
    if n_ungrounded:
        badges.append({"label": f"{n_ungrounded} unverified", "tone": "warn"})
    return badges


def section_card(session_id: str, section_id: str) -> Optional[dict]:
    """Compact approval-card summary for one section (scope + badges + council)."""
    sec = section_state.get_section(session_id, section_id)
    if not sec:
        return None
    draft = sec.get("draft") if isinstance(sec.get("draft"), dict) else None
    output = _output_of(sec)
    grounding = draft.get("grounding") if draft else None
    critique = draft.get("critique") if draft else None

    badges = _grounding_badges(grounding)
    if sec.get("status") == "blocked_on_data":
        badges.insert(0, {"label": "needs data", "tone": "bad"})

    council = None
    if isinstance(critique, dict) and critique.get("gated"):
        council = {
            "verdict": critique.get("verdict", "pass"),
            "issue_count": critique.get("issue_count", 0),
        }

    return {
        "section_id": section_id,
        "title": sec.get("title", section_id),
        "agent": sec.get("agent"),
        "status": sec.get("status", "not_started"),
        "scope": _scope_line(output),
        "badges": badges,
        "council": council,
        "tools": _TOOLS,
        "version": sec.get("version", 0),
        "blocked_on": sec.get("blocked_on"),
    }


def _msg(participant: str, role: str, kind: str, body: str, **extra: Any) -> dict:
    msg = {"participant": participant, "role": role, "kind": kind, "body": body}
    msg.update(extra)
    return msg


def section_thread(session_id: str, section_id: str) -> dict:
    """One section as a chronological conversation between Alex and his team.

    Assembled from durable sources only: the section's stored draft
    (agent output + grounding + Council critique) and its data requests.
    """
    section_state.init_sections(session_id)
    sec = section_state.get_section(session_id, section_id)
    if not sec:
        return {"section_id": section_id, "found": False, "messages": [], "card": None}

    agent = sec.get("agent") or "agent"
    draft = sec.get("draft") if isinstance(sec.get("draft"), dict) else None
    output = _output_of(sec)
    grounding = draft.get("grounding") if draft else None
    critique = draft.get("critique") if draft else None

    messages: list[dict] = [
        _msg(
            agent,
            "system",
            "status",
            f"{agent} owns this section. Tools: search · your data · ask you.",
            title="Assigned",
        )
    ]

    # Data requests (what the specialist still needs from Alex).
    try:
        from services.data_requests import list_open

        for req in list_open(session_id):
            if req.get("section_id") != section_id:
                continue
            why = f"\n\n_Why: {req['why']}_" if req.get("why") else ""
            messages.append(
                _msg(
                    "Feed",
                    "feed",
                    "data_request",
                    f"{req.get('description', 'Needs data.')}{why}",
                    title="Needs your data",
                    badges=[{"label": "open", "tone": "warn"}],
                    ts=req.get("created_at"),
                )
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[SectionView] data requests unavailable for %s: %s", section_id, e)

    # The agent's draft.
    if output is not None and sec.get("status") in ("needs_review", "done", "blocked_on_data"):
        messages.append(
            _msg(
                agent,
                "agent",
                "draft",
                _render_output(output),
                title="Draft",
                ts=sec.get("last_updated"),
            )
        )

    # Grounding pass.
    if isinstance(grounding, dict) and grounding.get("available"):
        rate = grounding.get("rate")
        pct = f"{int(rate * 100)}%" if rate is not None else "n/a"
        body = (
            f"{grounding.get('grounded', 0)}/{grounding.get('total_claims', 0)} "
            f"claims backed by your evidence ({pct})."
        )
        ungrounded = grounding.get("ungrounded") or []
        if ungrounded:
            body += "\n\n" + "\n".join(f"⚠ unverified: {c[:200]}" for c in ungrounded[:6])
        messages.append(
            _msg(
                "Grounding",
                "system",
                "grounding",
                body,
                title="Evidence check",
                badges=_grounding_badges(grounding),
            )
        )

    # Council critique (one message per persona review + coherence).
    if isinstance(critique, dict) and critique.get("gated"):
        for review in critique.get("reviews", []):
            issues = review.get("issues") or []
            body = "\n".join(f"- {i}" for i in issues) if issues else "No issues — pass."
            messages.append(
                _msg(
                    review.get("persona", "Council"),
                    "critic",
                    "critique",
                    body,
                    title=review.get("role", "Review"),
                    badges=[_verdict_badge(review.get("verdict", "pass"))],
                )
            )
        coherence = critique.get("coherence")
        if isinstance(coherence, dict) and coherence.get("contradictions"):
            messages.append(
                _msg(
                    "Coherence",
                    "critic",
                    "critique",
                    "\n".join(f"- {c}" for c in coherence["contradictions"][:6]),
                    title="Cross-section check",
                    badges=[_verdict_badge(coherence.get("verdict", "pass"))],
                )
            )

    return {
        "section_id": section_id,
        "found": True,
        "card": section_card(session_id, section_id),
        "messages": messages,
    }


def _verdict_badge(verdict: str) -> dict:
    return {
        "label": "pass" if verdict == "pass" else "revise",
        "tone": "good" if verdict == "pass" else "warn",
    }


def board_snapshot(session_id: str) -> dict:
    """Lightweight whole-board state for the live stream (Phase 2).

    Small on purpose — it is diffed every poll to decide whether to push.
    """
    from services.build_v2 import next_actions

    section_state.init_sections(session_id)
    states = section_state.list_sections(session_id)
    registry = section_state.effective_registry(session_id)

    sections = []
    done = 0
    for sid in registry:
        s = states.get(sid, {})
        status = s.get("status", "not_started")
        if status == "done":
            done += 1
        sections.append(
            {
                "section_id": sid,
                "title": s.get("title") or registry[sid].get("title", sid),
                "agent": s.get("agent") or registry[sid].get("agent"),
                "status": status,
                "blocked_on": s.get("blocked_on"),
            }
        )

    nxt = next_actions(session_id)
    inbox = list(nxt.get("needs_review", [])) + list((nxt.get("blocked_on_data") or {}).keys())
    return {
        "done": done,
        "total": len(registry),
        "sections": sections,
        "next": nxt,
        "inbox_count": len(inbox),
    }


def focus_options(session_id: str, section_id: str) -> dict:
    """Tap-to-answer focus choices for a section kickoff (Phase 3).

    Asks the LLM for 3–5 concrete angles this specialist could focus on, given
    the CEO's idea. On any failure, degrades to a clearly-generic option set
    (logged) so the kickoff still works offline — never fabricated specifics.
    """
    registry = section_state.effective_registry(session_id)
    meta = registry.get(section_id)
    if not meta:
        return {"section_id": section_id, "options": [], "question": ""}
    title = meta.get("title", section_id)

    idea = ""
    try:
        from services.pipeline_orchestrator import get_orchestrator

        phase1 = get_orchestrator()._read_phase1_session(session_id) or {}
        idea = phase1.get("idea", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("[SectionView] could not read idea for focus options: %s", e)

    question = f"What should the {title} section focus on?"
    try:
        from agents.phase1.llm_client import get_client

        prompt = (
            f"Business idea: {idea[:600] or '(not yet specified)'}\n\n"
            f"For the business-plan section '{title}', list 3 to 5 short, concrete "
            f"focus options the CEO could pick from (2-5 words each). "
            f'Return ONLY JSON: {{"options": ["...", "..."]}}'
        )
        raw = get_client().generate_content(
            prompt,
            system_instruction="You propose crisp, concrete options. JSON only.",
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        options = json.loads(cleaned).get("options", [])
        options = [str(o).strip()[:60] for o in options if str(o).strip()][:5]
        if options:
            return {"section_id": section_id, "question": question, "options": options}
        logger.warning("[SectionView] focus options empty for %s; using generic", section_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[SectionView] focus options LLM failed for %s: %s", section_id, e)

    return {
        "section_id": section_id,
        "question": question,
        "options": ["Broad overview", "Deep dive on key risks", "Data-driven detail"],
        "generic": True,
    }
