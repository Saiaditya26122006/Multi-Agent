"""Build v2 — per-section invocation (Phase 1) + on-demand assembly.

"Work on section X" runs just that section's agent (installment model), instead
of the whole batch pipeline. Reuses the existing orchestrator agent machinery
(instantiate/register/dispatch over the MessageBus) but for ONE section, writing
the result into the section-state backbone.
"""

import asyncio
import logging
import threading
import uuid
from typing import Optional

from services import section_state

logger = logging.getLogger(__name__)


def run_section(session_id: str, section_id: str, force: bool = False,
                feedback: Optional[str] = None) -> dict:
    """Start one section's agent in the background. Returns immediately.

    Returns {status, section_id, [reason]} where status is:
      - "started"  — agent dispatched
      - "blocked"  — dependencies not done (unless force=True)
      - "unknown_section"
    `feedback` (Adjust flow) is passed to the agent to steer a revision.
    """
    registry = section_state.load_registry()
    if section_id not in registry:
        return {"status": "unknown_section", "section_id": section_id}

    section_state.init_sections(session_id)
    states = section_state.list_sections(session_id)

    if not force and not section_state.deps_met(section_id, states, registry):
        deps = registry[section_id].get("depends_on", [])
        pending = [d for d in deps if states.get(d, {}).get("status") != "done"]
        return {"status": "blocked", "section_id": section_id,
                "reason": f"waiting on sections {pending}", "pending_deps": pending}

    cur = (states.get(section_id) or {}).get("status")
    # re-open a done/needs_review section for a revision
    section_state.update_section(session_id, section_id,
                                 status="in_progress" if cur != "in_progress" else cur)

    def _worker() -> None:
        try:
            asyncio.run(_run_section_async(session_id, section_id, registry, feedback))
        except Exception as e:  # noqa: BLE001
            logger.exception("[BuildV2] section %s crashed", section_id)
            try:
                section_state.update_section(session_id, section_id,
                                             status="failed", blocked_on=str(e)[:200])
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True, name=f"section-{section_id}").start()
    return {"status": "started", "section_id": section_id,
            "agent": registry[section_id].get("agent")}


def adjust_section(session_id: str, section_id: str, feedback: str) -> dict:
    """Re-open a done/needs_review section and re-run it with Alex's feedback.

    Replaces the old stubbed 'Adjust' — real section-level revision.
    """
    sec = section_state.get_section(session_id, section_id)
    if not sec:
        return {"status": "unknown_section"}
    if sec.get("status") not in ("done", "needs_review", "failed"):
        return {"status": "not_adjustable", "reason": f"section is {sec.get('status')}"}
    return run_section(session_id, section_id, force=True, feedback=feedback)


def export_plan(session_id: str) -> dict:
    """Compile the current section drafts into one markdown business plan.

    A view over section state (not a re-run). Includes only sections that have a
    draft; marks the rest as pending. DOCX export can adapt evaluation/export_docx.
    """
    section_state.init_sections(session_id)
    registry = section_state.load_registry()
    states = section_state.list_sections(session_id)
    lines = ["# Business Plan\n"]
    included = 0
    for sid, meta in registry.items():
        sec = states.get(sid, {})
        draft = sec.get("draft")
        output = draft.get("output") if isinstance(draft, dict) else draft
        lines.append(f"\n## {sid}. {meta.get('title', sid)}\n")
        if output and sec.get("status") in ("done", "needs_review"):
            included += 1
            if isinstance(output, (dict, list)):
                import json as _json
                lines.append("```json\n" + _json.dumps(output, indent=2, ensure_ascii=False)[:4000] + "\n```")
            else:
                lines.append(str(output)[:4000])
        else:
            lines.append(f"_{sec.get('status', 'not_started')} — not yet drafted._")
    return {"markdown": "\n".join(lines), "sections_included": included, "total": len(registry)}


async def _run_section_async(session_id: str, section_id: str, registry: dict,
                             feedback: Optional[str] = None) -> None:
    from services.pipeline_orchestrator import get_orchestrator
    from ceo_data.loader import get_relevant_ceo_data

    orch = get_orchestrator()
    agent_name = registry[section_id]["agent"]

    phase1 = orch._read_phase1_session(session_id) or {}
    ceo_data = get_relevant_ceo_data(phase1.get("idea", ""))

    # Prior section drafts become context (blackboard: read shared state).
    states = section_state.list_sections(session_id)
    prior_outputs = {
        sid: s.get("draft")
        for sid, s in states.items()
        if s.get("status") == "done" and s.get("draft")
    }

    await orch._ensure_agents_registered([agent_name])
    input_package = orch._build_input_package(agent_name, phase1, prior_outputs, ceo_data)
    if feedback:
        # Adjust flow: give the agent Alex's revision note + its prior draft.
        input_package["revision_feedback"] = feedback
        prior = section_state.get_section(session_id, section_id) or {}
        pd = prior.get("draft")
        input_package["prior_draft"] = pd.get("output") if isinstance(pd, dict) else pd
    run_id = f"secrun_{uuid.uuid4().hex[:8]}"

    result = await orch._dispatch_to_agent(agent_name, session_id, run_id, input_package)

    if result:
        # Phase 3: grounding report travels with the draft so Alex sees how
        # evidence-backed it is before accepting. Best-effort — never blocks.
        grounding = None
        try:
            from services.grounding import check_draft
            grounding = check_draft(result, session_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("[BuildV2] grounding skipped for %s: %s", section_id, e)
        # Phase 5: adversarial quality gate for gated sections (Skeptic/Operator).
        critique = None
        try:
            from services.quality_gate import critique_section
            critique = critique_section(section_id, result)
        except Exception as e:  # noqa: BLE001
            logger.debug("[BuildV2] critique skipped for %s: %s", section_id, e)
        draft = {"output": result, "grounding": grounding, "critique": critique}
        section_state.update_section(session_id, section_id,
                                     status="needs_review", draft=draft, blocked_on=None)
        rate = (grounding or {}).get("rate")
        verdict = (critique or {}).get("verdict")
        logger.info("[BuildV2] section %s -> needs_review (grounding=%s, critique=%s)",
                    section_id, rate, verdict)
    else:
        section_state.update_section(session_id, section_id,
                                     status="failed", blocked_on="agent returned no output")


def accept_section(session_id: str, section_id: str) -> dict:
    """Alex accepts a needs_review section -> done."""
    sec = section_state.get_section(session_id, section_id)
    if not sec:
        return {"status": "unknown_section"}
    section_state.update_section(session_id, section_id, status="done")
    return {"status": "done", "section_id": section_id}


def get_plan(session_id: str) -> dict:
    """On-demand assembly of the whole plan (done/WIP/blocked/not-started)."""
    section_state.init_sections(session_id)
    return section_state.assemble(session_id)


def next_actions(session_id: str) -> dict:
    """What Alex can do now: ready sections + what's blocked/in-review."""
    section_state.init_sections(session_id)
    states = section_state.list_sections(session_id)
    ready = section_state.ready_sections(session_id)
    review = [sid for sid, s in states.items() if s.get("status") == "needs_review"]
    blocked = {sid: s.get("blocked_on") for sid, s in states.items()
               if s.get("status") == "blocked_on_data"}
    return {"ready": ready, "needs_review": review, "blocked_on_data": blocked}
