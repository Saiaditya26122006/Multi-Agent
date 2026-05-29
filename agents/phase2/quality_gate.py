"""
Quality gate logic extracted from Mother Agent.

Runs post-processing checks on child agent output before acceptance:
  - So-What filter (does this help the CEO decide?)
  - Hypothesis validation (funnel math, unit economics)
  - Evidence grading (are confidence labels honest?)
  - Routing decisions (DA review, Council review)

Importable independently — no SPADE dependency.
"""

import logging
from typing import Optional

from agents.phase2.intelligence_engine import IntelligenceEngine

logger = logging.getLogger(__name__)

__all__ = ["QualityGate"]


class QualityGate:
    """Runs quality gates on section outputs before final acceptance."""

    # Sections requiring Devil's Advocate review (non-summary sections)
    DA_EXCLUDED_SECTIONS: set[str] = {"executive_summary"}

    def __init__(self, council_gated_sections: Optional[set[str]] = None) -> None:
        """Initialize the quality gate.

        Args:
            council_gated_sections: Set of section numbers that require
                Council review instead of DA review. If None, defaults
                to an empty set.
        """
        self._council_gated_sections: set[str] = council_gated_sections or set()

    async def run_gates(
        self,
        output: dict,
        section: str,
        agent_role: str,
        ie: IntelligenceEngine,
    ) -> dict:
        """Run all quality gates on a section output.

        Applies:
          1. So-What filter — rejects generic filler
          2. Hypothesis validation — checks quantitative consistency
          3. Evidence grading — verifies assumption confidence labels

        Args:
            output: The section output dict from a child agent.
            section: The section number (e.g. "1", "8", "12").
            agent_role: Description of the agent's role for context.
            ie: An IntelligenceEngine instance for LLM-based checks.

        Returns:
            The output dict, potentially annotated with warnings:
              - _so_what_warning: str if section fails the filter
              - _hypothesis_warnings: list[str] if math checks fail
              - assumptions_used: list may be re-graded
        """
        if not isinstance(output, dict):
            logger.warning(
                "[QualityGate] Output for section %s is not a dict — skipping gates",
                section,
            )
            return output

        # Gate 1: So-What filter
        so_what_critique = await ie.apply_so_what_filter(output, agent_role)
        if so_what_critique:
            logger.warning(
                "[QualityGate] Section %s failed So-What filter: %s",
                section,
                so_what_critique[:120],
            )
            output["_so_what_warning"] = so_what_critique

        # Gate 2: Hypothesis validation (funnel math, unit economics)
        failed_hypotheses = await ie.validate_hypotheses(output, agent_role)
        if failed_hypotheses:
            logger.warning(
                "[QualityGate] Section %s has %d failed hypotheses",
                section,
                len(failed_hypotheses),
            )
            output["_hypothesis_warnings"] = [
                f"[{h.get('hypothesis', '')}] {h.get('explanation', '')}"
                for h in failed_hypotheses
            ]

        # Gate 3: Evidence grading
        assumptions = output.get(
            "assumptions_used", output.get("assumption_log", [])
        )
        if assumptions:
            available_evidence = output.get("ceo_provided_data", {})
            graded = await ie.grade_evidence(assumptions, available_evidence)
            if graded and graded != assumptions:
                output["assumptions_used"] = graded
                logger.info(
                    "[QualityGate] Section %s: assumptions re-graded", section
                )

        return output

    def _should_route_to_da(self, section: str, output: dict) -> bool:
        """Determine if a section output should be sent to Devil's Advocate.

        Routes to DA when:
          - Section is not in the exclusion list (e.g. executive_summary)
          - Section is not council-gated (those go through Council instead)
          - Output is a valid dict

        Args:
            section: The section number string.
            output: The section output dict.

        Returns:
            True if this section should be routed to Devil's Advocate.
        """
        if str(section) in self.DA_EXCLUDED_SECTIONS:
            logger.debug(
                "[QualityGate] Section %s excluded from DA review", section
            )
            return False

        if self._should_route_to_council(section):
            logger.debug(
                "[QualityGate] Section %s is council-gated — skipping DA",
                section,
            )
            return False

        if not isinstance(output, dict):
            logger.debug(
                "[QualityGate] Section %s output is not a dict — skipping DA",
                section,
            )
            return False

        return True

    def _should_route_to_council(self, section: str) -> bool:
        """Determine if a section requires Council review.

        Council-gated sections are high-stakes sections (e.g. financial
        model, marketing strategy) that require multi-persona review
        before acceptance.

        Args:
            section: The section number string.

        Returns:
            True if this section requires Council review.
        """
        return str(section) in self._council_gated_sections
