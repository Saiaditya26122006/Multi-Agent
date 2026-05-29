"""
Contradiction resolution logic extracted from Mother Agent.

Attempts bounded negotiation between conflicting agents before
escalating to the CEO via Telegram. Only deadlocks escalate.

Importable independently — no SPADE dependency.
"""

import logging
from typing import Optional

from agents.phase2.negotiation import (
    NegotiationManager,
    NegotiationResult,
    should_negotiate,
)

logger = logging.getLogger(__name__)

__all__ = ["ConflictResolver"]


class ConflictResolver:
    """Resolves cross-section contradictions through negotiation or escalation.

    Flow:
      1. Prioritize contradictions by severity
      2. For each: attempt bounded negotiation (max 3 rounds)
      3. On consensus/compromise: apply the agreed value
      4. On deadlock: format an escalation message for Telegram
    """

    SEVERITY_ORDER: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

    async def resolve(
        self,
        contradictions: list[dict],
        negotiation_manager: NegotiationManager,
        bedrock_client: object,
        model_id: str,
    ) -> list[dict]:
        """Attempt to resolve a list of contradictions.

        For each contradiction, tries negotiation first. If negotiation
        reaches consensus or compromise, records the resolution. If it
        deadlocks, formats an escalation message.

        Args:
            contradictions: List of contradiction dicts, each with keys:
                - type: str (e.g. "revenue_mismatch", "timeline_conflict")
                - description: str
                - severity: "high" | "medium" | "low"
                - sections_involved: list[str] (section numbers)
                - initiator_evidence: dict (optional)
                - responder_evidence: dict (optional)
            negotiation_manager: A NegotiationManager instance for running
                bounded negotiations.
            bedrock_client: AWS Bedrock client (unused directly but available
                for future extension).
            model_id: The LLM model ID (unused directly but available for
                future extension).

        Returns:
            List of resolution dicts, each with keys:
                - contradiction: the original contradiction dict
                - outcome: "resolved" | "compromised" | "escalated"
                - resolution: dict with details (agreed_value, rounds, etc.)
                - escalation_message: str or None (populated on deadlock)
        """
        if not contradictions:
            logger.info("[ConflictResolver] No contradictions to resolve")
            return []

        prioritized = self._prioritize_contradictions(contradictions)
        results: list[dict] = []

        for contradiction in prioritized:
            sections = contradiction.get("sections_involved", [])
            claim = contradiction.get("description", "Unknown contradiction")
            severity = contradiction.get("severity", "medium")

            logger.info(
                "[ConflictResolver] Processing %s-severity contradiction: %s",
                severity,
                claim[:100],
            )

            # Check if negotiation is worthwhile
            if not should_negotiate(contradiction):
                logger.info(
                    "[ConflictResolver] Contradiction does not warrant negotiation — escalating directly"
                )
                escalation_msg = self._format_escalation(contradiction, None)
                results.append({
                    "contradiction": contradiction,
                    "outcome": "escalated",
                    "resolution": {"reason": "below_negotiation_threshold"},
                    "escalation_message": escalation_msg,
                })
                continue

            # Determine initiator and responder from sections
            initiator = f"section_{sections[0]}" if sections else "unknown_agent"
            responder = f"section_{sections[1]}" if len(sections) > 1 else "unknown_agent"

            evidence = contradiction.get("initiator_evidence", {})
            if not evidence:
                # Build minimal evidence from the contradiction itself
                evidence = {
                    "claim": claim,
                    "type": contradiction.get("type", ""),
                    "sections": sections,
                }

            # Run negotiation
            try:
                negotiation_result: NegotiationResult = await negotiation_manager.negotiate(
                    initiator=initiator,
                    responder=responder,
                    claim=claim,
                    evidence=evidence,
                    max_rounds=3,
                )

                if negotiation_result.outcome == "consensus":
                    logger.info(
                        "[ConflictResolver] Consensus reached for: %s",
                        claim[:80],
                    )
                    results.append({
                        "contradiction": contradiction,
                        "outcome": "resolved",
                        "resolution": {
                            "agreed_value": negotiation_result.agreed_value,
                            "rounds": negotiation_result.rounds,
                            "method": "consensus",
                        },
                        "escalation_message": None,
                    })

                elif negotiation_result.outcome == "compromise":
                    logger.info(
                        "[ConflictResolver] Compromise reached for: %s",
                        claim[:80],
                    )
                    results.append({
                        "contradiction": contradiction,
                        "outcome": "compromised",
                        "resolution": {
                            "agreed_value": negotiation_result.agreed_value,
                            "rounds": negotiation_result.rounds,
                            "method": "compromise",
                        },
                        "escalation_message": None,
                    })

                else:
                    # Deadlock — must escalate
                    logger.warning(
                        "[ConflictResolver] Deadlock on: %s — escalating",
                        claim[:80],
                    )
                    escalation_msg = self._format_escalation(
                        contradiction, negotiation_result
                    )
                    results.append({
                        "contradiction": contradiction,
                        "outcome": "escalated",
                        "resolution": {
                            "rounds": negotiation_result.rounds,
                            "method": "deadlock",
                            "history_summary": self._summarize_history(
                                negotiation_result.history
                            ),
                        },
                        "escalation_message": escalation_msg,
                    })

            except Exception as e:
                logger.error(
                    "[ConflictResolver] Negotiation failed for '%s': %s",
                    claim[:80],
                    e,
                )
                escalation_msg = self._format_escalation(contradiction, None)
                results.append({
                    "contradiction": contradiction,
                    "outcome": "escalated",
                    "resolution": {"reason": f"negotiation_error: {e}"},
                    "escalation_message": escalation_msg,
                })

        resolved_count = sum(1 for r in results if r["outcome"] in ("resolved", "compromised"))
        escalated_count = sum(1 for r in results if r["outcome"] == "escalated")
        logger.info(
            "[ConflictResolver] Done: %d resolved, %d escalated out of %d total",
            resolved_count,
            escalated_count,
            len(results),
        )

        return results

    def _prioritize_contradictions(self, contradictions: list) -> list:
        """Sort contradictions by severity (high first), then by type.

        Args:
            contradictions: Unsorted list of contradiction dicts.

        Returns:
            Sorted list with high-severity first.
        """
        return sorted(
            contradictions,
            key=lambda c: (
                -self.SEVERITY_ORDER.get(c.get("severity", "low"), 0),
                c.get("type", ""),
            ),
        )

    def _format_escalation(
        self,
        contradiction: dict,
        negotiation_result: Optional[NegotiationResult],
    ) -> str:
        """Format a contradiction into a human-readable escalation message for Telegram.

        Args:
            contradiction: The contradiction dict with type, description, severity.
            negotiation_result: The NegotiationResult if negotiation was attempted,
                or None if escalation is direct.

        Returns:
            Formatted string suitable for Telegram notification.
        """
        sections = contradiction.get("sections_involved", [])
        sections_str = " vs ".join(f"Section {s}" for s in sections) if sections else "Unknown sections"
        severity = contradiction.get("severity", "unknown")
        description = contradiction.get("description", "No description")
        contradiction_type = contradiction.get("type", "unknown")

        lines = [
            f"Conflict ({severity} severity):",
            f"Type: {contradiction_type}",
            f"Sections: {sections_str}",
            f"Issue: {description}",
        ]

        if negotiation_result is not None:
            lines.append("")
            lines.append(
                f"Agents attempted {negotiation_result.rounds} rounds of "
                f"negotiation but reached a {negotiation_result.outcome}."
            )

            # Include key positions from history
            if negotiation_result.history:
                last_positions = negotiation_result.history[-2:]
                for entry in last_positions:
                    agent = entry.get("agent", "?")
                    content = entry.get("content", {})
                    rationale = content.get("rationale", content.get("reasoning", ""))
                    if rationale:
                        lines.append(f"  {agent}: {str(rationale)[:150]}")
        else:
            lines.append("")
            lines.append("No negotiation attempted — requires your decision.")

        lines.append("")
        lines.append("Please advise which position to adopt.")

        return "\n".join(lines)

    def _summarize_history(self, history: list[dict]) -> str:
        """Produce a short summary of negotiation history for logging.

        Args:
            history: List of negotiation round entries.

        Returns:
            Brief summary string.
        """
        if not history:
            return "No history"

        rounds = set()
        agents = set()
        for entry in history:
            rounds.add(entry.get("round", 0))
            agents.add(entry.get("agent", "?"))

        return (
            f"{len(rounds)} round(s) between {', '.join(agents)}; "
            f"last phase: {history[-1].get('phase', '?')}"
        )
