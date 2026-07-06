"""
CHALLENGE Workspace Handler — stress tests Alex's assumptions.

Handles: weakest assumption attacks, section challenges, specific claim challenges,
full devil's advocate runs, competitor comparisons.
"""

import logging
from typing import Optional

from tools.trace_emitter import emit_trace

logger = logging.getLogger(__name__)


def _trace(session_id: Optional[str], step: str, detail: str, data: Optional[dict] = None) -> None:
    """Emit a trace event only if we actually have a session to broadcast to."""
    if session_id:
        emit_trace(session_id, "Challenge", step, detail, data or {})


def challenge_weakest_assumptions(top_k: int = 3, session_id: Optional[str] = None) -> dict:
    """Auto-pick the top vulnerable assumptions and attack them.

    Args:
        top_k: Number of assumptions to challenge.
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with assumptions list and their vulnerabilities.
    """
    try:
        from services.coverage_calculator import get_oldest_assumptions

        _trace(session_id, "finding_targets", f"Ranking assumptions by age to find the top {top_k} most vulnerable...")
        oldest = get_oldest_assumptions(top_k=top_k)

        if not oldest:
            return {
                "status": "no_targets",
                "message": "No unvalidated assumptions found. Either everything is confirmed or no data exists yet.",
                "assumptions": [],
            }

        vulnerabilities = []
        for assumption in oldest:
            vulnerability = {
                "id": assumption["id"],
                "claim": assumption["content_preview"],
                "age_days": assumption["age_days"],
                "risk_level": "critical" if assumption["age_days"] > 30 else "high",
                "challenge": _generate_challenge_text(assumption["content_preview"]),
            }
            vulnerabilities.append(vulnerability)

        _trace(session_id, "challenges_ready", f"Built {len(vulnerabilities)} challenge(s)")

        return {
            "status": "challenges_ready",
            "message": f"Found {len(vulnerabilities)} vulnerable assumption(s) to challenge.",
            "assumptions": vulnerabilities,
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error finding weak assumptions: %s", e)
        return {"status": "error", "message": str(e), "assumptions": []}


def challenge_section(section_id: str, session_id: Optional[str] = None) -> dict:
    """Stress test a specific business plan section.

    Args:
        section_id: The section to challenge (e.g., "9" or "BP.9").
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with section vulnerabilities and challenges.
    """
    if not section_id.startswith("BP."):
        section_id = f"BP.{section_id}"

    try:
        from services.rag_service import retrieve

        _trace(session_id, "retrieving_claims", f"Retrieving claims and assumptions in {section_id}...")
        chunks = retrieve(
            query=f"section {section_id} assumptions claims hypotheses",
            section=section_id.replace("BP.", ""),
            top_k=10,
            threshold=0.3,
        )

        assumptions_in_section = [
            c for c in chunks if c.epistemic_status == "ASSUMPTION"
        ]
        confirmed_in_section = [
            c for c in chunks if c.epistemic_status == "CONFIRMED"
        ]

        challenges = []
        for assumption in assumptions_in_section:
            challenges.append({
                "claim": assumption.content[:100],
                "status": "ASSUMPTION",
                "challenge": _generate_challenge_text(assumption.content),
                "severity": "high",
            })

        _trace(
            session_id, "section_verdict",
            f"{section_id}: {len(assumptions_in_section)} assumption(s), {len(confirmed_in_section)} confirmed",
        )

        return {
            "status": "section_challenged",
            "section_id": section_id,
            "total_claims": len(chunks),
            "assumptions": len(assumptions_in_section),
            "confirmed": len(confirmed_in_section),
            "challenges": challenges,
            "verdict": _section_verdict(len(assumptions_in_section), len(confirmed_in_section)),
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error challenging section: %s", e)
        return {"status": "error", "section_id": section_id, "message": str(e)}


def challenge_claim(claim_text: str, session_id: Optional[str] = None) -> dict:
    """Adversarial analysis of one specific claim.

    Args:
        claim_text: The claim to challenge.
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with challenge details, evidence gaps, and counter-arguments.
    """
    try:
        from services.rag_service import retrieve

        _trace(session_id, "gathering_evidence", f"Gathering evidence for/against: \"{claim_text[:60]}\"...")
        related = retrieve(
            query=claim_text,
            top_k=5,
            threshold=0.35,
        )

        supporting = [c for c in related if c.epistemic_status == "CONFIRMED"]
        contradicting = [c for c in related if c.epistemic_status == "CONTRADICTION"]

        _trace(
            session_id, "claim_assessed",
            f"{len(supporting)} supporting, {len(contradicting)} contradicting",
        )

        return {
            "status": "claim_challenged",
            "claim": claim_text,
            "supporting_evidence": len(supporting),
            "contradicting_evidence": len(contradicting),
            "challenge": _generate_challenge_text(claim_text),
            "evidence_gap": _identify_evidence_gap(claim_text, related),
            "risk_assessment": _assess_claim_risk(supporting, contradicting),
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error challenging claim: %s", e)
        return {"status": "error", "claim": claim_text, "message": str(e)}


def challenge_full_plan(session_id: Optional[str] = None) -> dict:
    """Run full devil's advocate pass on the entire plan.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with per-section vulnerability summary.
    """
    try:
        from services.coverage_calculator import get_sections, get_oldest_assumptions

        _trace(session_id, "full_challenge_start", "Starting full devil's-advocate pass across the plan...")
        sections = get_sections()
        oldest = get_oldest_assumptions(top_k=10)

        section_risks = []
        for section_id in sorted(sections.keys()):
            _trace(session_id, "challenging_section", f"Challenging {section_id}...")
            result = challenge_section(section_id, session_id=session_id)
            if result.get("status") == "section_challenged":
                section_risks.append({
                    "section_id": section_id,
                    "assumptions": result.get("assumptions", 0),
                    "confirmed": result.get("confirmed", 0),
                    "verdict": result.get("verdict", "unknown"),
                })

        _trace(session_id, "full_challenge_complete", f"Challenged {len(section_risks)} section(s)")

        return {
            "status": "full_challenge_complete",
            "message": f"Challenged {len(section_risks)} section(s).",
            "section_risks": section_risks,
            "top_vulnerabilities": oldest[:5],
            "overall_verdict": _overall_plan_verdict(section_risks),
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error in full plan challenge: %s", e)
        return {"status": "error", "message": str(e)}


def compare_competitor(competitor_name: str, session_id: Optional[str] = None) -> dict:
    """Position check against a named competitor.

    Args:
        competitor_name: Name of the competitor to compare against.
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with comparison data.
    """
    try:
        from services.rag_service import retrieve

        _trace(session_id, "researching_competitor", f"Looking up data on {competitor_name}...")
        chunks = retrieve(
            query=f"competitor {competitor_name} comparison positioning",
            source_types=["ceo_doc", "external_research"],
            top_k=5,
            threshold=0.35,
        )

        if not chunks:
            return {
                "status": "no_data",
                "competitor": competitor_name,
                "message": f"No data found about {competitor_name}. Feed competitor intelligence first.",
            }

        competitor_data = [
            {"content": c.content[:150], "source": c.source_type}
            for c in chunks
        ]

        return {
            "status": "comparison_ready",
            "competitor": competitor_name,
            "data_points": len(competitor_data),
            "data": competitor_data,
            "message": f"Found {len(competitor_data)} data point(s) about {competitor_name}.",
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error comparing competitor: %s", e)
        return {"status": "error", "competitor": competitor_name, "message": str(e)}


def get_vulnerability_list(session_id: Optional[str] = None) -> dict:
    """Panel view of all weak points ranked by severity.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with ranked vulnerabilities.
    """
    try:
        from services.coverage_calculator import get_oldest_assumptions, get_stale_items

        _trace(session_id, "scanning_vulnerabilities", "Scanning assumptions and stale data for weak points...")
        assumptions = get_oldest_assumptions(top_k=10)
        stale = get_stale_items(max_age_days=21)

        vulnerabilities = []

        for a in assumptions:
            score = a["age_days"] * 2
            vulnerabilities.append({
                "type": "unvalidated_assumption",
                "content": a["content_preview"],
                "age_days": a["age_days"],
                "severity_score": score,
                "severity": "critical" if score > 60 else "high",
            })

        for s in stale:
            score = s["age_days"]
            vulnerabilities.append({
                "type": "stale_data",
                "content": s["content_preview"],
                "age_days": s["age_days"],
                "severity_score": score,
                "severity": "medium",
            })

        vulnerabilities.sort(key=lambda v: v["severity_score"], reverse=True)

        _trace(session_id, "vulnerabilities_ready", f"Found {len(vulnerabilities)} vulnerability/vulnerabilities, ranked")

        return {
            "count": len(vulnerabilities),
            "vulnerabilities": vulnerabilities[:20],
        }
    except Exception as e:
        logger.error("[ChallengeHandler] Error getting vulnerability list: %s", e)
        return {"count": 0, "vulnerabilities": []}


def format_challenge_response(result: dict) -> str:
    """Format challenge results as a chat message.

    Args:
        result: Output from any challenge function.

    Returns:
        Formatted string for chat.
    """
    status = result.get("status", "unknown")

    if status == "no_targets":
        return result.get("message", "Nothing to challenge.")

    if status == "challenges_ready":
        assumptions = result.get("assumptions", [])
        lines = [f"Top {len(assumptions)} vulnerable assumption(s):"]
        lines.append("")
        for i, a in enumerate(assumptions, 1):
            lines.append(f"  {i}. [{a['risk_level'].upper()}] {a['claim'][:70]}")
            lines.append(f"     Age: {a['age_days']} days")
            lines.append(f"     Challenge: {a['challenge']}")
            lines.append("")
        return "\n".join(lines)

    if status == "section_challenged":
        section = result.get("section_id", "?")
        lines = [f"Section {section} challenge results:"]
        lines.append(f"  ASSUMPTION claims: {result.get('assumptions', 0)}")
        lines.append(f"  CONFIRMED claims: {result.get('confirmed', 0)}")
        lines.append(f"  Verdict: {result.get('verdict', 'unknown')}")
        challenges = result.get("challenges", [])
        if challenges:
            lines.append("")
            lines.append("  Challenges:")
            for c in challenges[:5]:
                lines.append(f"    - {c['challenge']}")
        return "\n".join(lines)

    if status == "claim_challenged":
        lines = [f"Challenge to: \"{result.get('claim', '?')[:60]}\""]
        lines.append(f"  Supporting evidence: {result.get('supporting_evidence', 0)}")
        lines.append(f"  Contradicting evidence: {result.get('contradicting_evidence', 0)}")
        lines.append(f"  Challenge: {result.get('challenge', '')}")
        lines.append(f"  Evidence gap: {result.get('evidence_gap', '')}")
        lines.append(f"  Risk: {result.get('risk_assessment', '')}")
        return "\n".join(lines)

    # get_vulnerability_list() (the "c" menu command) returns
    # {"count", "vulnerabilities": [...]} with no "status"/"message" key —
    # without this branch it fell through to `str(result)`, printing a raw
    # Python dict repr into the chat instead of a formatted list.
    if "vulnerabilities" in result:
        vulns = result.get("vulnerabilities", [])
        count = result.get("count", len(vulns))
        if count == 0:
            return "No vulnerabilities found — assumptions and data all look current."
        lines = [f"{count} vulnerability/vulnerabilities found, ranked by severity:"]
        lines.append("")
        for i, v in enumerate(vulns[:15], 1):
            lines.append(
                f"  {i}. [{v.get('severity', 'unknown').upper()}] "
                f"({v.get('type', '?')}) {v.get('content', '')[:70]}"
            )
            lines.append(f"     Age: {v.get('age_days', '?')} days")
        if count > 15:
            lines.append(f"\n  ...and {count - 15} more.")
        return "\n".join(lines)

    return result.get("message", str(result))


def _generate_challenge_text(claim: str) -> str:
    """Generate a challenge question for a claim."""
    return (
        f"What evidence exists that this is true? "
        f"Who validated it? When? "
        f"What would be different if this were wrong?"
    )


def _identify_evidence_gap(claim: str, related_chunks: list) -> str:
    """Identify what evidence is missing for a claim."""
    if not related_chunks:
        return "No supporting evidence found at all. This claim is entirely ungrounded."
    confirmed = sum(1 for c in related_chunks if c.epistemic_status == "CONFIRMED")
    if confirmed == 0:
        return "No CONFIRMED data supports this. All related data is also ASSUMPTION-level."
    return f"Partial support exists ({confirmed} confirmed sources) but gaps remain."


def _assess_claim_risk(supporting: list, contradicting: list) -> str:
    """Assess the risk level of a claim based on evidence."""
    if contradicting and not supporting:
        return "CRITICAL — contradicted with no support"
    elif contradicting and supporting:
        return "HIGH — conflicting evidence exists"
    elif not supporting:
        return "HIGH — no evidence either way"
    else:
        return "LOW — supported by confirmed data"


def _section_verdict(assumptions: int, confirmed: int) -> str:
    """Generate a verdict for a section based on its evidence mix."""
    total = assumptions + confirmed
    if total == 0:
        return "EMPTY — no data to challenge"
    ratio = confirmed / total if total else 0
    if ratio >= 0.7:
        return "SOLID — mostly confirmed data"
    elif ratio >= 0.4:
        return "MIXED — significant assumption load"
    else:
        return "FRAGILE — built on unvalidated assumptions"


def _overall_plan_verdict(section_risks: list[dict]) -> str:
    """Generate an overall plan verdict."""
    if not section_risks:
        return "INSUFFICIENT DATA — cannot assess"
    fragile = sum(1 for s in section_risks if "FRAGILE" in s.get("verdict", ""))
    solid = sum(1 for s in section_risks if "SOLID" in s.get("verdict", ""))
    total = len(section_risks)

    if fragile > total * 0.5:
        return "HIGH RISK — majority of plan is fragile"
    elif solid > total * 0.7:
        return "STRONG — majority confirmed"
    else:
        return "MODERATE RISK — mixed evidence quality"
