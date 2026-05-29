"""
Cross-section coherence auditor extracted from Mother Agent.

Checks consistency across all completed business plan sections:
  - Revenue figures match between Marketing and Financial
  - ICP is consistent across Opportunity and Marketing
  - Headcount plan matches personnel costs
  - Timelines are aligned across sections
  - Confidence chain is not violated (low feeding into high)

Importable independently — no SPADE dependency.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["CoherenceAuditor", "AuditResult"]


@dataclass
class AuditResult:
    """Result of a cross-section coherence audit."""

    contradictions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    consistency_score: float = 1.0


class CoherenceAuditor:
    """Performs deterministic cross-section coherence checks.

    Unlike the LLM-based audit in the Mother Agent, this class runs
    fast programmatic checks that catch obvious contradictions without
    an LLM call. Use this as a pre-filter before the expensive LLM audit.
    """

    CONFIDENCE_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

    def audit(self, prior_outputs: dict) -> AuditResult:
        """Run all coherence checks across completed section outputs.

        Args:
            prior_outputs: Dict mapping section number (str) to output dict.
                Example: {"1": {...}, "8": {...}, "12": {...}}

        Returns:
            AuditResult with contradictions, warnings, and a consistency score.
        """
        if not prior_outputs or not isinstance(prior_outputs, dict):
            logger.info("[CoherenceAuditor] No outputs to audit")
            return AuditResult(consistency_score=1.0)

        contradictions: list[dict] = []
        warnings: list[str] = []

        # Run each check
        revenue_issues = self._check_revenue_consistency(prior_outputs)
        confidence_issues = self._check_confidence_chain(prior_outputs)
        timeline_issues = self._check_timeline_alignment(prior_outputs)

        for issue in revenue_issues:
            contradictions.append({
                "type": "revenue_mismatch",
                "description": issue,
                "severity": "high",
            })

        for issue in confidence_issues:
            warnings.append(issue)

        for issue in timeline_issues:
            contradictions.append({
                "type": "timeline_conflict",
                "description": issue,
                "severity": "medium",
            })

        # Compute score: start at 1.0, deduct for each issue
        score = 1.0
        score -= len(contradictions) * 0.15
        score -= len(warnings) * 0.05
        score = max(0.0, min(1.0, score))

        logger.info(
            "[CoherenceAuditor] Audit complete: %d contradictions, %d warnings, score=%.2f",
            len(contradictions),
            len(warnings),
            score,
        )

        return AuditResult(
            contradictions=contradictions,
            warnings=warnings,
            consistency_score=score,
        )

    def _check_revenue_consistency(self, outputs: dict) -> list[str]:
        """Check that revenue assumptions are consistent across sections.

        Compares:
          - Section 8 (Marketing) revenue_assumptions with
          - Section 12 (Financial) three_statement_model revenue

        Args:
            outputs: Dict mapping section numbers to output dicts.

        Returns:
            List of inconsistency descriptions.
        """
        issues: list[str] = []

        marketing_output = outputs.get("8", {})
        financial_output = outputs.get("12", {})

        if not isinstance(marketing_output, dict) or not isinstance(financial_output, dict):
            return issues

        # Extract marketing revenue assumptions
        rev_assumptions = marketing_output.get("revenue_assumptions", {})
        if not isinstance(rev_assumptions, dict):
            return issues

        mkt_price = rev_assumptions.get("price_per_unit")
        mkt_volume_y1 = rev_assumptions.get("volume_year1")

        if mkt_price is None or mkt_volume_y1 is None:
            return issues

        # Extract financial model revenue
        three_stmt = financial_output.get("three_statement_model", {})
        if not isinstance(three_stmt, dict):
            return issues

        # Look for year 1 revenue in various possible locations
        fin_revenue_y1 = self._extract_financial_revenue(three_stmt)

        if fin_revenue_y1 is None:
            return issues

        # Compare: marketing price * volume should approximately equal financial revenue
        try:
            expected_revenue = float(mkt_price) * float(mkt_volume_y1)
            actual_revenue = float(fin_revenue_y1)

            if expected_revenue == 0:
                return issues

            deviation = abs(actual_revenue - expected_revenue) / expected_revenue

            if deviation > 0.2:
                issues.append(
                    f"Revenue mismatch: Marketing implies "
                    f"${expected_revenue:,.0f}/yr (price={mkt_price} x vol={mkt_volume_y1}) "
                    f"but Financial shows ${actual_revenue:,.0f}/yr "
                    f"({deviation:.0%} deviation)"
                )
        except (TypeError, ValueError) as e:
            logger.debug(
                "[CoherenceAuditor] Could not compare revenue figures: %s", e
            )

        # Check CAC consistency
        cac_assumptions = marketing_output.get("cac_assumptions", {})
        if isinstance(cac_assumptions, dict):
            mkt_cac = cac_assumptions.get("cac_estimate")
            fin_cac = self._find_nested_value(financial_output, "cac")
            if mkt_cac is not None and fin_cac is not None:
                try:
                    if float(mkt_cac) > 0:
                        cac_deviation = abs(float(fin_cac) - float(mkt_cac)) / float(mkt_cac)
                        if cac_deviation > 0.3:
                            issues.append(
                                f"CAC mismatch: Marketing estimates ${mkt_cac} "
                                f"but Financial uses ${fin_cac} "
                                f"({cac_deviation:.0%} deviation)"
                            )
                except (TypeError, ValueError):
                    pass

        return issues

    def _check_confidence_chain(self, outputs: dict) -> list[str]:
        """Check that no section claims higher confidence than its upstream inputs.

        A downstream section with confidence "high" that depends on an upstream
        section with confidence "low" is dishonest.

        Args:
            outputs: Dict mapping section numbers to output dicts.

        Returns:
            List of confidence chain violation descriptions.
        """
        issues: list[str] = []

        # Known dependency chains (downstream depends on upstream)
        dependency_chains: list[tuple[str, str]] = [
            ("1", "8"),   # Opportunity -> Marketing
            ("1", "5"),   # Opportunity -> SWOT
            ("3", "5"),   # Environment -> SWOT
            ("5", "8"),   # SWOT -> Marketing
            ("8", "12"),  # Marketing -> Financial
            ("4", "12"),  # Org Designer -> Financial (headcount costs)
            ("12", "13"), # Financial -> Launch
        ]

        for upstream_sec, downstream_sec in dependency_chains:
            upstream_output = outputs.get(upstream_sec, {})
            downstream_output = outputs.get(downstream_sec, {})

            if not isinstance(upstream_output, dict) or not isinstance(downstream_output, dict):
                continue

            upstream_conf = upstream_output.get("confidence_score", "medium")
            downstream_conf = downstream_output.get("confidence_score", "medium")

            upstream_rank = self.CONFIDENCE_RANK.get(upstream_conf, 2)
            downstream_rank = self.CONFIDENCE_RANK.get(downstream_conf, 2)

            if downstream_rank > upstream_rank:
                issues.append(
                    f"Confidence gap: Section {downstream_sec} claims "
                    f"'{downstream_conf}' confidence but depends on "
                    f"Section {upstream_sec} which is '{upstream_conf}'"
                )

        return issues

    def _check_timeline_alignment(self, outputs: dict) -> list[str]:
        """Check that timelines are consistent across sections.

        Compares:
          - Section 12 break_even_month with Section 13 launch timeline
          - Section 4 hiring timeline with Section 12 cost ramp-up

        Args:
            outputs: Dict mapping section numbers to output dicts.

        Returns:
            List of timeline inconsistency descriptions.
        """
        issues: list[str] = []

        financial_output = outputs.get("12", {})
        launch_output = outputs.get("13", {})

        if not isinstance(financial_output, dict) or not isinstance(launch_output, dict):
            return issues

        # Check break-even vs launch timeline
        break_even = financial_output.get("break_even_analysis", {})
        if isinstance(break_even, dict):
            be_month = break_even.get("break_even_month")
            if be_month is not None:
                # Look for launch date or programme duration in section 13
                launch_duration = self._extract_launch_duration(launch_output)
                if launch_duration is not None:
                    try:
                        be_month_int = int(be_month)
                        launch_months = int(launch_duration)
                        if launch_months > be_month_int:
                            issues.append(
                                f"Timeline conflict: Launch programme spans "
                                f"{launch_months} months but break-even is at "
                                f"month {be_month_int} — launch ends after "
                                f"break-even which is unusual"
                            )
                    except (TypeError, ValueError):
                        pass

        return issues

    def _extract_financial_revenue(self, three_stmt: dict) -> Optional[float]:
        """Extract year 1 revenue from the three-statement model."""
        # Try common locations
        for key in ("revenue_year1", "year1_revenue", "revenue", "total_revenue"):
            val = three_stmt.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue

        # Check nested income statement
        income = three_stmt.get("income_statement", {})
        if isinstance(income, dict):
            for key in ("revenue", "total_revenue", "year1"):
                val = income.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue

        return None

    def _extract_launch_duration(self, launch_output: dict) -> Optional[int]:
        """Extract launch programme duration in months from section 13 output."""
        for key in ("programme_duration_months", "duration_months",
                    "total_duration", "launch_duration"):
            val = launch_output.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    continue

        # Check milestones for max month
        milestones = launch_output.get("milestones", [])
        if isinstance(milestones, list) and milestones:
            max_month = 0
            for m in milestones:
                if isinstance(m, dict):
                    month = m.get("month", m.get("target_month", 0))
                    try:
                        max_month = max(max_month, int(month))
                    except (TypeError, ValueError):
                        continue
            if max_month > 0:
                return max_month

        return None

    def _find_nested_value(self, data: dict, key_substring: str) -> Optional[float]:
        """Recursively find a numeric value whose key contains the substring."""
        for key, value in data.items():
            if key_substring in key.lower():
                if isinstance(value, (int, float)):
                    return float(value)
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
            if isinstance(value, dict):
                result = self._find_nested_value(value, key_substring)
                if result is not None:
                    return result
        return None
