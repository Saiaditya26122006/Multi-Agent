"""
Comprehensive Benchmark for Multi-Agent System Intelligence & Communication.

Measures the 10 critique dimensions:
1. Reasoning depth (template filler vs actual thinking)
2. IE enforcement (do steps reference each other?)
3. Communication efficiency (overhead, message count, routing)
4. Cross-section consistency (do numbers match across sections?)
5. Learning effectiveness (improvement across runs)
6. Fallback quality (how bad is degraded output?)
7. Negotiation capability (conflict resolution vs escalation)
8. Agent autonomy (belief challenges, initiative)
9. Mother Agent coupling (what breaks without Mother?)
10. Adaptive pipeline (early kill, pivot detection)

Usage:
    python evaluation/benchmark.py --run-all
    python evaluation/benchmark.py --dimension reasoning_depth
    python evaluation/benchmark.py --compare baseline_v1.json post_fix.json
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

GENERIC_PHRASES = [
    "unique value proposition",
    "first-mover advantage",
    "differentiation through",
    "innovative approach",
    "cutting-edge technology",
    "best-in-class",
    "world-class",
    "leveraging synergies",
    "holistic approach",
    "comprehensive solution",
    "industry-leading",
    "next-generation",
    "paradigm shift",
    "scalable platform",
    "end-to-end solution",
    "strategic partnerships",
    "customer-centric",
    "data-driven",
    "agile methodology",
    "robust framework",
]

FILLER_PATTERNS = [
    r"analysis pending",
    r"to be determined",
    r"further research needed",
    r"based on industry standards",
    r"typical for this sector",
    r"as per market norms",
    r"generally accepted",
]


@dataclass
class DimensionScore:
    """Score for a single benchmark dimension."""

    name: str
    score: float  # 0.0 - 10.0
    max_score: float = 10.0
    details: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)


@dataclass
class BenchmarkResult:
    """Full benchmark result across all dimensions."""

    run_id: str
    timestamp: str
    test_idea: str
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)
    overall_score: float = 0.0
    overall_grade: str = ""
    raw_outputs: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def compute_overall(self):
        """Weighted average across dimensions."""
        weights = {
            "reasoning_depth": 0.20,
            "ie_enforcement": 0.10,
            "communication_efficiency": 0.05,
            "cross_section_consistency": 0.15,
            "learning_effectiveness": 0.10,
            "fallback_quality": 0.05,
            "negotiation_capability": 0.10,
            "agent_autonomy": 0.10,
            "mother_coupling": 0.05,
            "adaptive_pipeline": 0.10,
        }

        total_weight = 0.0
        weighted_sum = 0.0
        for dim_name, dim_score in self.dimensions.items():
            weight = weights.get(dim_name, 0.05)
            weighted_sum += dim_score.score * weight
            total_weight += weight

        self.overall_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        self.overall_grade = self._grade(self.overall_score)

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 9.0:
            return "A+"
        elif score >= 8.0:
            return "A"
        elif score >= 7.0:
            return "B"
        elif score >= 6.0:
            return "C"
        elif score >= 5.0:
            return "D"
        else:
            return "F"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "test_idea": self.test_idea,
            "overall_score": round(self.overall_score, 2),
            "overall_grade": self.overall_grade,
            "dimensions": {
                name: {
                    "score": round(d.score, 2),
                    "max_score": d.max_score,
                    "details": d.details,
                    "evidence": d.evidence[:5],
                    "recommendations": d.recommendations[:3],
                }
                for name, d in self.dimensions.items()
            },
            "metadata": self.metadata,
        }


class IntelligenceBenchmark:
    """Benchmarks the actual intelligence of the multi-agent system."""

    def __init__(self, pipeline_outputs: dict, reasoning_traces: dict,
                 message_log: list, learning_data: dict,
                 fallback_events: list, negotiation_log: list):
        self.outputs = pipeline_outputs
        self.traces = reasoning_traces
        self.messages = message_log
        self.learning = learning_data
        self.fallbacks = fallback_events
        self.negotiations = negotiation_log

    def run_all(self, test_idea: str) -> BenchmarkResult:
        """Run all 10 benchmark dimensions."""
        result = BenchmarkResult(
            run_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            test_idea=test_idea,
            raw_outputs=self.outputs,
        )

        result.dimensions["reasoning_depth"] = self.measure_reasoning_depth()
        result.dimensions["ie_enforcement"] = self.measure_ie_enforcement()
        result.dimensions["communication_efficiency"] = (
            self.measure_communication_efficiency()
        )
        result.dimensions["cross_section_consistency"] = (
            self.measure_cross_section_consistency()
        )
        result.dimensions["learning_effectiveness"] = (
            self.measure_learning_effectiveness()
        )
        result.dimensions["fallback_quality"] = self.measure_fallback_quality()
        result.dimensions["negotiation_capability"] = (
            self.measure_negotiation_capability()
        )
        result.dimensions["agent_autonomy"] = self.measure_agent_autonomy()
        result.dimensions["mother_coupling"] = self.measure_mother_coupling()
        result.dimensions["adaptive_pipeline"] = self.measure_adaptive_pipeline()

        result.compute_overall()
        return result

    # ------------------------------------------------------------------
    # DIMENSION 1: Reasoning Depth
    # ------------------------------------------------------------------

    def measure_reasoning_depth(self) -> DimensionScore:
        """Does the agent actually reason, or just fill templates?

        Measures:
        - Generic phrase density (lower is better)
        - Idea-specific references (higher is better)
        - Causal chain presence ("because", "therefore", "since")
        - Numeric justification (numbers traced to sources)
        - Kill-test presence (fatal flaw identification)
        """
        scores_per_section = {}
        evidence = []

        for section, output in self.outputs.items():
            if not isinstance(output, dict):
                continue

            text = json.dumps(output, default=str)
            text_lower = text.lower()

            # Sub-metric 1: Generic phrase density (0-10, lower density = higher score)
            generic_count = sum(
                1 for phrase in GENERIC_PHRASES if phrase in text_lower
            )
            generic_density = generic_count / max(len(text.split()), 1) * 1000
            generic_score = max(0, 10 - generic_density * 5)

            # Sub-metric 2: Causal reasoning markers
            causal_markers = [
                "because", "therefore", "since", "this means",
                "which implies", "as a result", "consequently",
                "driven by", "caused by", "leads to",
            ]
            causal_count = sum(
                text_lower.count(marker) for marker in causal_markers
            )
            causal_score = min(10, causal_count * 2)

            # Sub-metric 3: Specificity — concrete numbers with context
            numbers = re.findall(r'\$[\d,]+|\d+%|\d+\s*months?|\d+\s*years?', text)
            justified_numbers = re.findall(
                r'(?:because|based on|from|per|assuming).*?(\$[\d,]+|\d+%)',
                text_lower,
            )
            if numbers:
                justification_ratio = len(justified_numbers) / len(numbers)
            else:
                justification_ratio = 0
            specificity_score = justification_ratio * 10

            # Sub-metric 4: Filler detection (deduct points for filler)
            filler_count = sum(
                1 for pattern in FILLER_PATTERNS
                if re.search(pattern, text_lower)
            )
            filler_penalty = min(5, filler_count * 2)

            # Sub-metric 5: Idea-specific references
            # Check if output references the actual business idea details
            idea_keywords = self._extract_idea_keywords()
            keyword_hits = sum(
                1 for kw in idea_keywords if kw.lower() in text_lower
            )
            idea_specificity = min(10, keyword_hits * 2)

            section_score = (
                generic_score * 0.2
                + causal_score * 0.25
                + specificity_score * 0.25
                + idea_specificity * 0.2
                - filler_penalty * 0.1
            )
            section_score = max(0, min(10, section_score))
            scores_per_section[section] = section_score

            if generic_count > 3:
                evidence.append(
                    f"Section {section}: {generic_count} generic phrases detected"
                )
            if causal_count < 2:
                evidence.append(
                    f"Section {section}: Only {causal_count} causal reasoning markers"
                )

        avg_score = (
            sum(scores_per_section.values()) / len(scores_per_section)
            if scores_per_section
            else 0
        )

        recommendations = []
        if avg_score < 5:
            recommendations.append(
                "Rewrite SYSTEM_PROMPTs with domain-specific reasoning protocols"
            )
        if any(s < 3 for s in scores_per_section.values()):
            recommendations.append(
                "Worst sections need 'kill conditions' and anti-generic-phrase rules"
            )

        return DimensionScore(
            name="reasoning_depth",
            score=avg_score,
            details={
                "per_section": scores_per_section,
                "avg_generic_phrases": sum(
                    1
                    for s, o in self.outputs.items()
                    if isinstance(o, dict)
                    for p in GENERIC_PHRASES
                    if p in json.dumps(o).lower()
                ) / max(len(self.outputs), 1),
            },
            evidence=evidence,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # DIMENSION 2: IE Enforcement
    # ------------------------------------------------------------------

    def measure_ie_enforcement(self) -> DimensionScore:
        """Does the Intelligence Engine actually enforce its reasoning chain?

        Measures:
        - Does PRODUCE reference DECOMPOSE judgments?
        - Does REVISE address CHALLENGE findings?
        - Are confidence downgrades applied when challenges unresolved?
        - Is there iteration (>1 revision attempt)?
        """
        scores = []
        evidence = []

        for section, trace in self.traces.items():
            if not isinstance(trace, dict):
                continue

            decomposition = trace.get("decomposition", "")
            draft = trace.get("draft", "")
            challenge = trace.get("challenge", "")
            revision = trace.get("revision", "")
            final_output = self.outputs.get(section, {})

            section_scores = {}

            # Check 1: Does draft reference decomposition?
            if decomposition and draft:
                decomp_keywords = self._extract_key_terms(decomposition)
                draft_references = sum(
                    1 for kw in decomp_keywords if kw.lower() in draft.lower()
                )
                coverage = draft_references / max(len(decomp_keywords), 1)
                section_scores["decomp_coverage"] = min(10, coverage * 10)
            else:
                section_scores["decomp_coverage"] = 0

            # Check 2: Does revision address challenges?
            if challenge and revision:
                challenge_points = self._extract_challenge_items(challenge)
                addressed = sum(
                    1 for cp in challenge_points
                    if any(
                        word in revision.lower()
                        for word in cp.lower().split()[:3]
                    )
                )
                resolution_rate = addressed / max(len(challenge_points), 1)
                section_scores["challenge_resolution"] = resolution_rate * 10
            else:
                section_scores["challenge_resolution"] = 0

            # Check 3: Confidence appropriateness
            confidence = final_output.get("confidence_score", "medium")
            has_unresolved = bool(final_output.get("_unresolved_challenges"))
            was_capped = bool(final_output.get("_confidence_capped"))

            if has_unresolved and confidence == "high":
                section_scores["confidence_calibration"] = 0
                evidence.append(
                    f"Section {section}: High confidence despite unresolved challenges"
                )
            elif was_capped:
                section_scores["confidence_calibration"] = 8
            else:
                section_scores["confidence_calibration"] = 5

            # Check 4: Iteration depth
            revision_count = trace.get("revision_count", 0)
            if revision_count >= 2:
                section_scores["iteration_depth"] = 10
            elif revision_count == 1:
                section_scores["iteration_depth"] = 5
            else:
                section_scores["iteration_depth"] = 0

            avg = (
                sum(section_scores.values()) / len(section_scores)
                if section_scores
                else 0
            )
            scores.append(avg)

        overall = sum(scores) / len(scores) if scores else 0

        recommendations = []
        if overall < 5:
            recommendations.append(
                "Add programmatic constraint propagation between IE steps"
            )
            recommendations.append(
                "Parse CHALLENGE output into checklist, verify REVISE addresses each item"
            )

        return DimensionScore(
            name="ie_enforcement",
            score=overall,
            details={"per_section_traces": len(self.traces)},
            evidence=evidence,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # DIMENSION 3: Communication Efficiency
    # ------------------------------------------------------------------

    def measure_communication_efficiency(self) -> DimensionScore:
        """Is the communication layer efficient or wasteful?

        Measures:
        - Messages per section (lower = more efficient)
        - Routing overhead (Mother relay vs direct)
        - Payload bloat (how much of message is metadata vs content)
        - Failed/retried messages
        - Total communication latency
        """
        if not self.messages:
            return DimensionScore(
                name="communication_efficiency",
                score=5.0,
                details={"message_count": 0},
                evidence=["No message log available"],
                recommendations=["Instrument message bus for tracking"],
            )

        total_messages = len(self.messages)
        sections_produced = len(self.outputs)
        messages_per_section = total_messages / max(sections_produced, 1)

        # Count routing hops (messages that are just relays)
        relay_messages = sum(
            1 for m in self.messages
            if m.get("performative") == "propose"
            and m.get("sender") == "mother_agent"
        )
        relay_ratio = relay_messages / max(total_messages, 1)

        # Payload analysis
        total_payload_bytes = sum(
            len(json.dumps(m.get("content", {}))) for m in self.messages
        )
        total_metadata_bytes = sum(
            len(json.dumps({
                k: v for k, v in m.items() if k != "content"
            }))
            for m in self.messages
        )
        content_ratio = total_payload_bytes / max(
            total_payload_bytes + total_metadata_bytes, 1
        )

        # Failed messages
        failed = sum(
            1 for m in self.messages
            if m.get("status") == "failed" or m.get("performative") == "refuse"
        )
        failure_rate = failed / max(total_messages, 1)

        # Score: fewer messages per section = better
        # Optimal: 2 per section (request + inform)
        msg_efficiency = max(0, 10 - (messages_per_section - 2) * 2)
        relay_penalty = relay_ratio * 3
        failure_penalty = failure_rate * 5

        score = max(0, min(10, msg_efficiency - relay_penalty - failure_penalty))

        evidence = []
        if messages_per_section > 5:
            evidence.append(
                f"{messages_per_section:.1f} messages per section (optimal: 2)"
            )
        if relay_ratio > 0.3:
            evidence.append(
                f"{relay_ratio*100:.0f}% of messages are relays through Mother"
            )

        return DimensionScore(
            name="communication_efficiency",
            score=score,
            details={
                "total_messages": total_messages,
                "messages_per_section": round(messages_per_section, 1),
                "relay_ratio": round(relay_ratio, 2),
                "content_ratio": round(content_ratio, 2),
                "failure_rate": round(failure_rate, 2),
            },
            evidence=evidence,
            recommendations=(
                ["Replace SPADE with direct async calls"]
                if messages_per_section > 4
                else []
            ),
        )

    # ------------------------------------------------------------------
    # DIMENSION 4: Cross-Section Consistency
    # ------------------------------------------------------------------

    def measure_cross_section_consistency(self) -> DimensionScore:
        """Do sections contradict each other?

        Checks:
        - Revenue assumptions match between Marketing (8) and Financial (12)
        - ICP consistent between Opportunity (1) and Marketing (8)
        - Headcount matches between Org (4) and Financial (12)
        - Timelines align between Launch (13) and Financial (12)
        - SWOT threats addressed by strategies in Marketing (8)
        """
        contradictions = []
        checks_passed = 0
        total_checks = 0

        # Check 1: Revenue consistency (Section 8 vs 12)
        marketing = self.outputs.get("8", self.outputs.get(8, {}))
        financial = self.outputs.get("12", self.outputs.get(12, {}))

        if marketing and financial:
            total_checks += 1
            mkt_revenue = marketing.get("revenue_assumptions", {})
            fin_revenue = financial.get("three_statement_model", {}).get(
                "year1_revenue",
                financial.get("revenue_assumptions", {}),
            )

            mkt_price = mkt_revenue.get("price_per_unit")
            mkt_vol = mkt_revenue.get("volume_year1")

            if mkt_price and mkt_vol and fin_revenue:
                try:
                    mkt_calc = float(str(mkt_price).replace("$", "").replace(",", "")) * float(str(mkt_vol).replace(",", ""))
                    fin_val = float(str(fin_revenue).replace("$", "").replace(",", ""))
                    if fin_val > 0:
                        ratio = mkt_calc / fin_val
                        if 0.8 <= ratio <= 1.2:
                            checks_passed += 1
                        else:
                            contradictions.append(
                                f"Revenue mismatch: Marketing={mkt_calc:.0f}, "
                                f"Financial={fin_val:.0f} (ratio={ratio:.2f})"
                            )
                    else:
                        checks_passed += 1
                except (ValueError, TypeError):
                    pass

        # Check 2: ICP consistency (Section 1 vs 8)
        opportunity = self.outputs.get("1", self.outputs.get(1, {}))

        if opportunity and marketing:
            total_checks += 1
            opp_icp = opportunity.get("icp_hypothesis", {})
            mkt_target = marketing.get("target_market_analysis", {})

            opp_buyer = str(opp_icp.get("buyer_role", "")).lower()
            mkt_buyer = str(mkt_target.get("primary_segment", "")).lower()

            if opp_buyer and mkt_buyer:
                if opp_buyer in mkt_buyer or mkt_buyer in opp_buyer:
                    checks_passed += 1
                else:
                    contradictions.append(
                        f"ICP drift: Opportunity targets '{opp_buyer}', "
                        f"Marketing targets '{mkt_buyer}'"
                    )
            else:
                checks_passed += 1

        # Check 3: Timeline consistency (Section 13 vs 12)
        launch = self.outputs.get("13", self.outputs.get(13, {}))

        if launch and financial:
            total_checks += 1
            launch_timeline = launch.get("launch_programme", [])
            fin_breakeven = financial.get("break_even_analysis", {}).get(
                "baseline_month", 0
            )

            if launch_timeline and fin_breakeven:
                last_milestone_month = 0
                for milestone in launch_timeline:
                    if isinstance(milestone, dict):
                        month = milestone.get("month", milestone.get("timeframe", 0))
                        try:
                            last_milestone_month = max(
                                last_milestone_month, int(str(month).split()[0])
                            )
                        except (ValueError, TypeError):
                            pass

                if last_milestone_month > 0 and fin_breakeven > 0:
                    if last_milestone_month > fin_breakeven + 6:
                        contradictions.append(
                            f"Timeline conflict: Launch milestones extend to month "
                            f"{last_milestone_month}, but break-even is month "
                            f"{fin_breakeven}"
                        )
                    else:
                        checks_passed += 1
                else:
                    checks_passed += 1
            else:
                checks_passed += 1

        # Check 4: Confidence coherence (no high downstream of low upstream)
        total_checks += 1
        section_order = ["1", "3", "5", "8", "12", "13"]
        confidence_chain = []
        for sec in section_order:
            output = self.outputs.get(sec, self.outputs.get(int(sec) if sec.isdigit() else sec, {}))
            if isinstance(output, dict):
                confidence_chain.append(
                    (sec, output.get("confidence_score", "medium"))
                )

        conf_rank = {"high": 3, "medium": 2, "low": 1}
        confidence_violation = False
        for i in range(1, len(confidence_chain)):
            prev_sec, prev_conf = confidence_chain[i - 1]
            curr_sec, curr_conf = confidence_chain[i]
            if conf_rank.get(curr_conf, 2) > conf_rank.get(prev_conf, 2):
                contradictions.append(
                    f"Confidence violation: Section {curr_sec} is '{curr_conf}' "
                    f"but depends on Section {prev_sec} which is '{prev_conf}'"
                )
                confidence_violation = True
                break

        if not confidence_violation:
            checks_passed += 1

        # Score
        consistency_rate = checks_passed / max(total_checks, 1)
        score = consistency_rate * 10

        return DimensionScore(
            name="cross_section_consistency",
            score=score,
            details={
                "checks_passed": checks_passed,
                "total_checks": total_checks,
                "contradictions_found": len(contradictions),
            },
            evidence=contradictions[:5],
            recommendations=(
                [
                    "Add pre-production consistency check in each agent",
                    "Implement post-production self-audit before returning output",
                ]
                if contradictions
                else []
            ),
        )

    # ------------------------------------------------------------------
    # DIMENSION 5: Learning Effectiveness
    # ------------------------------------------------------------------

    def measure_learning_effectiveness(self) -> DimensionScore:
        """Does the system actually learn from past failures?

        Measures:
        - Are past failure patterns injected into prompts?
        - Do the SAME errors recur across runs?
        - Is learning context specific or generic?
        - Does output quality improve run-over-run?
        """
        if not self.learning:
            return DimensionScore(
                name="learning_effectiveness",
                score=0.0,
                details={"learning_data_available": False},
                evidence=["No learning data captured — system is not learning"],
                recommendations=[
                    "Implement pattern extraction on rejections",
                    "Track recurring failure root causes",
                ],
            )

        patterns = self.learning.get("patterns", [])
        recurring_errors = self.learning.get("recurring_errors", [])
        context_injections = self.learning.get("context_injections", [])
        run_scores = self.learning.get("run_scores_over_time", [])

        # Sub-metric 1: Pattern specificity
        specific_patterns = sum(
            1 for p in patterns
            if p.get("root_cause") and p.get("anti_pattern")
        )
        pattern_quality = (
            specific_patterns / max(len(patterns), 1) * 10 if patterns else 0
        )

        # Sub-metric 2: Error recurrence (lower = better learning)
        if recurring_errors:
            recurrence_rate = len(recurring_errors) / max(len(patterns), 1)
            recurrence_score = max(0, 10 - recurrence_rate * 10)
        else:
            recurrence_score = 5  # Neutral if no data

        # Sub-metric 3: Quality improvement over time
        if len(run_scores) >= 2:
            first_half = run_scores[: len(run_scores) // 2]
            second_half = run_scores[len(run_scores) // 2:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            improvement = (avg_second - avg_first) / max(avg_first, 0.1) * 10
            improvement_score = max(0, min(10, 5 + improvement))
        else:
            improvement_score = 5

        # Sub-metric 4: Context injection quality
        generic_injections = sum(
            1 for ci in context_injections
            if "was rejected" in ci.lower() and len(ci) < 50
        )
        specific_injections = sum(
            1 for ci in context_injections if len(ci) > 100
        )
        injection_quality = (
            specific_injections / max(len(context_injections), 1) * 10
            if context_injections
            else 0
        )

        score = (
            pattern_quality * 0.3
            + recurrence_score * 0.3
            + improvement_score * 0.2
            + injection_quality * 0.2
        )

        evidence = []
        if recurring_errors:
            evidence.append(
                f"{len(recurring_errors)} errors recurring across runs"
            )
        if pattern_quality < 5:
            evidence.append("Patterns lack root cause analysis")

        return DimensionScore(
            name="learning_effectiveness",
            score=score,
            details={
                "total_patterns": len(patterns),
                "specific_patterns": specific_patterns,
                "recurring_errors": len(recurring_errors),
                "runs_tracked": len(run_scores),
            },
            evidence=evidence,
            recommendations=[
                "Extract root causes from rejections, not just event types",
                "Track if same root cause repeats — if so, modify SYSTEM_PROMPT",
            ],
        )

    # ------------------------------------------------------------------
    # DIMENSION 6: Fallback Quality
    # ------------------------------------------------------------------

    def measure_fallback_quality(self) -> DimensionScore:
        """When fallback kicks in, how harmful is the output?

        Measures:
        - Are fallback outputs clearly marked?
        - Do downstream agents consume fallback as real data?
        - Is fallback content generic/harmful or safely minimal?
        - Does fallback propagate false confidence?
        """
        if not self.fallbacks:
            return DimensionScore(
                name="fallback_quality",
                score=7.0,
                details={"fallback_events": 0},
                evidence=["No fallback events — all sections produced normally"],
                recommendations=[],
            )

        scores = []
        evidence = []

        for event in self.fallbacks:
            section = event.get("section")
            fallback_output = event.get("output", {})
            downstream_consumed = event.get("consumed_by_downstream", [])

            event_score = 10.0

            # Check 1: Is fallback clearly marked?
            if not fallback_output.get("_generation_mode"):
                event_score -= 3
                evidence.append(
                    f"Section {section}: Fallback not marked with _generation_mode"
                )

            # Check 2: Confidence set to low?
            if fallback_output.get("confidence_score") != "low":
                event_score -= 4
                evidence.append(
                    f"Section {section}: Fallback has confidence="
                    f"'{fallback_output.get('confidence_score')}' (should be 'low')"
                )

            # Check 3: Generic content present?
            text = json.dumps(fallback_output).lower()
            generic_count = sum(
                1 for p in GENERIC_PHRASES if p in text
            )
            if generic_count > 2:
                event_score -= 2
                evidence.append(
                    f"Section {section}: Fallback contains {generic_count} "
                    f"generic phrases"
                )

            # Check 4: Was it consumed downstream without flagging?
            if downstream_consumed:
                event_score -= 3
                evidence.append(
                    f"Section {section}: Fallback consumed by "
                    f"{downstream_consumed} without re-validation"
                )

            scores.append(max(0, event_score))

        avg_score = sum(scores) / len(scores) if scores else 7.0

        return DimensionScore(
            name="fallback_quality",
            score=avg_score,
            details={
                "fallback_events": len(self.fallbacks),
                "avg_fallback_score": round(avg_score, 2),
            },
            evidence=evidence[:5],
            recommendations=[
                "Replace template fallbacks with structured failure modes",
                "Never pass fallback output downstream without explicit flag",
                "Allow agents to REFUSE rather than produce garbage",
            ],
        )

    # ------------------------------------------------------------------
    # DIMENSION 7: Negotiation Capability
    # ------------------------------------------------------------------

    def measure_negotiation_capability(self) -> DimensionScore:
        """Can agents resolve contradictions without escalating to human?

        Measures:
        - Proposals initiated (higher = more autonomous)
        - Proposals resolved agent-to-agent vs escalated to CEO
        - Negotiation rounds before resolution
        - Quality of resolution (did it actually fix the contradiction?)
        """
        if not self.negotiations:
            return DimensionScore(
                name="negotiation_capability",
                score=2.0,
                details={"negotiations_attempted": 0},
                evidence=[
                    "No negotiations attempted — all contradictions escalated or ignored"
                ],
                recommendations=[
                    "Implement bounded negotiation protocol (3 rounds max)",
                    "Agents should try to resolve before escalating to Alex",
                ],
            )

        proposals_initiated = len(self.negotiations)
        resolved_by_agents = sum(
            1 for n in self.negotiations if n.get("outcome") == "consensus"
        )
        escalated = sum(
            1 for n in self.negotiations if n.get("outcome") == "escalated"
        )
        deadlocked = sum(
            1 for n in self.negotiations if n.get("outcome") == "deadlock"
        )

        resolution_rate = resolved_by_agents / max(proposals_initiated, 1)

        avg_rounds = 0
        if self.negotiations:
            avg_rounds = sum(
                n.get("rounds", 1) for n in self.negotiations
            ) / len(self.negotiations)

        # Score based on autonomous resolution
        score = resolution_rate * 8 + min(2, proposals_initiated * 0.5)

        evidence = []
        if resolution_rate < 0.5:
            evidence.append(
                f"Only {resolution_rate*100:.0f}% of contradictions resolved "
                f"without human intervention"
            )
        if escalated > resolved_by_agents:
            evidence.append(
                f"More escalations ({escalated}) than agent resolutions "
                f"({resolved_by_agents})"
            )

        return DimensionScore(
            name="negotiation_capability",
            score=score,
            details={
                "proposals_initiated": proposals_initiated,
                "resolved_by_agents": resolved_by_agents,
                "escalated_to_ceo": escalated,
                "deadlocked": deadlocked,
                "avg_rounds": round(avg_rounds, 1),
            },
            evidence=evidence,
            recommendations=[
                "Implement NegotiationRound with 3-round max",
                "Only escalate after agents fail to reach consensus",
            ],
        )

    # ------------------------------------------------------------------
    # DIMENSION 8: Agent Autonomy
    # ------------------------------------------------------------------

    def measure_agent_autonomy(self) -> DimensionScore:
        """Do agents have beliefs, initiative, and ability to challenge?

        Measures:
        - Did any agent challenge incoming data?
        - Did any agent refuse a task based on judgment?
        - Did any agent initiate a proposal without being asked?
        - Do agents maintain beliefs across tasks?
        """
        autonomy_signals = {
            "challenges_initiated": 0,
            "tasks_refused_on_judgment": 0,
            "unsolicited_proposals": 0,
            "belief_updates": 0,
            "data_corrections": 0,
        }

        for msg in self.messages:
            perf = msg.get("performative", "")
            content = msg.get("content", {})
            sender = msg.get("sender", "")

            if sender == "mother_agent":
                continue

            if perf == "propose" and not content.get("solicited"):
                autonomy_signals["unsolicited_proposals"] += 1

            if perf == "refuse" and content.get("reason_type") == "judgment":
                autonomy_signals["tasks_refused_on_judgment"] += 1

            if content.get("challenges_incoming_data"):
                autonomy_signals["challenges_initiated"] += 1

            if content.get("belief_updated"):
                autonomy_signals["belief_updates"] += 1

            if content.get("corrects_upstream"):
                autonomy_signals["data_corrections"] += 1

        total_signals = sum(autonomy_signals.values())

        # Score: more autonomous signals = higher score
        # Optimal: at least 1 signal per section produced
        sections_count = max(len(self.outputs), 1)
        autonomy_ratio = total_signals / sections_count
        score = min(10, autonomy_ratio * 5)

        evidence = []
        if total_signals == 0:
            evidence.append(
                "Zero autonomy signals — agents never challenge, refuse, or propose"
            )
        if autonomy_signals["challenges_initiated"] == 0:
            evidence.append("No agent ever challenged incoming data from another agent")

        return DimensionScore(
            name="agent_autonomy",
            score=score,
            details=autonomy_signals,
            evidence=evidence,
            recommendations=[
                "Implement BDI belief store per agent",
                "Allow agents to challenge incoming data when it contradicts beliefs",
                "Agents should initiate proposals when they detect issues",
            ],
        )

    # ------------------------------------------------------------------
    # DIMENSION 9: Mother Agent Coupling
    # ------------------------------------------------------------------

    def measure_mother_coupling(self) -> DimensionScore:
        """How dependent is the system on Mother Agent?

        Measures:
        - What % of logic lives in Mother vs distributed?
        - Can any agent function without Mother?
        - Is Mother a bottleneck (sequential processing)?
        - How many Mother methods are called per section?
        """
        mother_messages = sum(
            1 for m in self.messages
            if m.get("sender") == "mother_agent"
            or m.get("receiver") == "mother_agent"
        )
        total_messages = max(len(self.messages), 1)
        mother_ratio = mother_messages / total_messages

        # Check for direct agent-to-agent messages (bypassing Mother)
        direct_messages = sum(
            1 for m in self.messages
            if m.get("sender") != "mother_agent"
            and m.get("receiver") != "mother_agent"
        )
        direct_ratio = direct_messages / total_messages

        # Bottleneck detection: sequential Mother processing
        mother_sequential_blocks = 0
        prev_sender = None
        for msg in self.messages:
            if msg.get("sender") == "mother_agent":
                if prev_sender == "mother_agent":
                    mother_sequential_blocks += 1
            prev_sender = msg.get("sender")

        # Score: lower Mother dependency = better
        # Heavily coupled (>90% through Mother) = low score
        decoupling_score = max(0, 10 - mother_ratio * 10)
        direct_bonus = direct_ratio * 5

        score = min(10, decoupling_score + direct_bonus)

        evidence = []
        if mother_ratio > 0.8:
            evidence.append(
                f"{mother_ratio*100:.0f}% of all messages involve Mother Agent"
            )
        if direct_ratio < 0.1:
            evidence.append(
                "Less than 10% of messages are direct agent-to-agent"
            )

        return DimensionScore(
            name="mother_coupling",
            score=score,
            details={
                "mother_message_ratio": round(mother_ratio, 2),
                "direct_agent_ratio": round(direct_ratio, 2),
                "sequential_blocks": mother_sequential_blocks,
            },
            evidence=evidence,
            recommendations=[
                "Enable direct agent-to-agent queries for clarification",
                "Move quality gate logic to independent QualityGate class",
                "Allow parallel agent communication without Mother relay",
            ],
        )

    # ------------------------------------------------------------------
    # DIMENSION 10: Adaptive Pipeline
    # ------------------------------------------------------------------

    def measure_adaptive_pipeline(self) -> DimensionScore:
        """Does the pipeline adapt based on findings, or blindly execute?

        Measures:
        - Were there early signals that the idea was weak?
        - Did the pipeline continue despite fatal signals?
        - Were sections skipped when not applicable?
        - Did any checkpoint trigger a pause/pivot/kill?
        """
        evidence = []
        score = 5.0  # Neutral baseline

        # Check 1: Did environment research find fatal signals?
        env_output = self.outputs.get("3", self.outputs.get(3, {}))
        if isinstance(env_output, dict):
            five_forces = env_output.get("five_forces", {})
            high_threats = sum(
                1 for force_data in five_forces.values()
                if isinstance(force_data, dict)
                and force_data.get("threat_level") == "high"
            )

            if high_threats >= 4:
                # Fatal signal — pipeline should have paused
                remaining_sections = sum(
                    1 for sec in ["5", "8", "10", "12", "13"]
                    if sec in self.outputs or int(sec) in self.outputs
                )
                if remaining_sections >= 4:
                    score -= 4
                    evidence.append(
                        f"4/5 forces = high threat but pipeline ran "
                        f"{remaining_sections} more sections without pause"
                    )
                else:
                    score += 2
                    evidence.append("Pipeline correctly paused on fatal market signal")

        # Check 2: Did financial model find unsustainable economics?
        fin_output = self.outputs.get("12", self.outputs.get(12, {}))
        if isinstance(fin_output, dict):
            breakeven = fin_output.get("break_even_analysis", {}).get(
                "baseline_month", 0
            )
            if breakeven and int(str(breakeven).split(".")[0]) > 48:
                # 4+ year break-even — should trigger checkpoint
                summary_exists = bool(
                    self.outputs.get("executive_summary")
                    or self.outputs.get("summary")
                )
                if summary_exists:
                    score -= 3
                    evidence.append(
                        f"Break-even at month {breakeven} (>48) but pipeline "
                        f"continued to summary without CEO checkpoint"
                    )

        # Check 3: Were conditional sections correctly skipped?
        metadata = self.metadata if hasattr(self, "metadata") else {}
        skipped = metadata.get("sections_skipped", [])
        should_skip = metadata.get("sections_not_applicable", [])

        if should_skip:
            correctly_skipped = set(skipped) & set(should_skip)
            skip_accuracy = len(correctly_skipped) / max(len(should_skip), 1)
            score += skip_accuracy * 2
        else:
            score += 1  # Neutral if no conditional sections

        # Check 4: Low confidence early section → downstream caution?
        opp_output = self.outputs.get("1", self.outputs.get(1, {}))
        if isinstance(opp_output, dict):
            opp_confidence = opp_output.get("confidence_score", "medium")
            if opp_confidence == "low":
                downstream_high = sum(
                    1
                    for sec in ["5", "8", "12"]
                    if self.outputs.get(sec, self.outputs.get(int(sec) if sec.isdigit() else sec, {})).get(
                        "confidence_score"
                    )
                    == "high"
                )
                if downstream_high > 0:
                    score -= 2
                    evidence.append(
                        f"Section 1 confidence='low' but {downstream_high} "
                        f"downstream sections claim 'high'"
                    )

        score = max(0, min(10, score))

        recommendations = []
        if score < 5:
            recommendations.append(
                "Add kill checkpoints after sections 1, 3, and 12"
            )
            recommendations.append(
                "Pipeline should pause and ask CEO when fatal signals detected"
            )

        return DimensionScore(
            name="adaptive_pipeline",
            score=score,
            details={
                "early_kill_triggered": score > 7,
                "sections_after_fatal_signal": evidence,
            },
            evidence=evidence,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def _extract_idea_keywords(self) -> list[str]:
        """Extract keywords from the test idea for specificity checking."""
        idea_data = self.outputs.get("_idea_input", {})
        if isinstance(idea_data, dict):
            text = json.dumps(idea_data)
        elif isinstance(idea_data, str):
            text = idea_data
        else:
            text = ""

        words = re.findall(r'\b[A-Za-z]{4,}\b', text)
        stopwords = {
            "this", "that", "with", "from", "they", "will", "have",
            "been", "would", "could", "should", "about", "which",
            "their", "there", "what", "when", "where", "more", "also",
        }
        return [w for w in words if w.lower() not in stopwords][:20]

    def _extract_key_terms(self, text: str) -> list[str]:
        """Extract key terms from decomposition for coverage check."""
        sentences = text.split(".")
        terms = []
        for sentence in sentences:
            words = re.findall(r'\b[A-Za-z]{5,}\b', sentence)
            if words:
                terms.extend(words[:2])
        return terms[:10]

    def _extract_challenge_items(self, challenge_text: str) -> list[str]:
        """Extract individual challenge points from challenge output."""
        lines = challenge_text.split("\n")
        items = []
        for line in lines:
            line = line.strip()
            if line and (
                line.startswith("-")
                or line.startswith("*")
                or re.match(r'^\d+[\.\)]', line)
            ):
                items.append(line.lstrip("-*0123456789.) "))
        return items if items else [challenge_text[:100]]


# ------------------------------------------------------------------
# Comparison Tool
# ------------------------------------------------------------------


def compare_benchmarks(before_path: str, after_path: str) -> dict:
    """Compare two benchmark results and produce delta report."""
    with open(before_path) as f:
        before = json.load(f)
    with open(after_path) as f:
        after = json.load(f)

    report = {
        "before_run": before.get("run_id"),
        "after_run": after.get("run_id"),
        "overall_delta": round(
            after.get("overall_score", 0) - before.get("overall_score", 0), 2
        ),
        "grade_change": f"{before.get('overall_grade')} -> {after.get('overall_grade')}",
        "dimension_deltas": {},
    }

    before_dims = before.get("dimensions", {})
    after_dims = after.get("dimensions", {})

    for dim_name in set(list(before_dims.keys()) + list(after_dims.keys())):
        b_score = before_dims.get(dim_name, {}).get("score", 0)
        a_score = after_dims.get(dim_name, {}).get("score", 0)
        delta = round(a_score - b_score, 2)

        if delta > 0:
            arrow = "^"
        elif delta < 0:
            arrow = "v"
        else:
            arrow = "="

        report["dimension_deltas"][dim_name] = {
            "before": b_score,
            "after": a_score,
            "delta": delta,
            "direction": arrow,
        }

    # Identify biggest improvements and regressions
    deltas = report["dimension_deltas"]
    sorted_by_delta = sorted(deltas.items(), key=lambda x: x[1]["delta"])
    report["biggest_improvement"] = sorted_by_delta[-1][0] if sorted_by_delta else None
    report["biggest_regression"] = (
        sorted_by_delta[0][0] if sorted_by_delta and sorted_by_delta[0][1]["delta"] < 0 else None
    )

    return report


def format_comparison_report(comparison: dict) -> str:
    """Format comparison as readable text."""
    lines = [
        "=" * 60,
        "BENCHMARK COMPARISON REPORT",
        "=" * 60,
        f"Overall: {comparison['overall_delta']:+.2f} ({comparison['grade_change']})",
        "",
        f"{'Dimension':<30} {'Before':>6} {'After':>6} {'Delta':>7}",
        "-" * 55,
    ]

    for dim, data in sorted(
        comparison["dimension_deltas"].items(),
        key=lambda x: x[1]["delta"],
        reverse=True,
    ):
        arrow = data["direction"]
        lines.append(
            f"{dim:<30} {data['before']:>6.1f} {data['after']:>6.1f} "
            f"{data['delta']:>+6.2f} {arrow}"
        )

    lines.append("-" * 55)

    if comparison.get("biggest_improvement"):
        lines.append(f"Best improvement: {comparison['biggest_improvement']}")
    if comparison.get("biggest_regression"):
        lines.append(f"Biggest regression: {comparison['biggest_regression']}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------


def run_benchmark_from_eval_results(eval_result_path: str) -> BenchmarkResult:
    """Run benchmark from an existing eval_runner output file."""
    with open(eval_result_path) as f:
        data = json.load(f)

    pipeline_outputs = data.get("section_outputs", data.get("outputs", {}))
    reasoning_traces = data.get("reasoning_traces", {})
    message_log = data.get("message_log", [])
    learning_data = data.get("learning_data", {})
    fallback_events = data.get("fallback_events", [])
    negotiation_log = data.get("negotiation_log", [])

    benchmark = IntelligenceBenchmark(
        pipeline_outputs=pipeline_outputs,
        reasoning_traces=reasoning_traces,
        message_log=message_log,
        learning_data=learning_data,
        fallback_events=fallback_events,
        negotiation_log=negotiation_log,
    )

    test_idea = data.get("test_idea", data.get("idea_name", "unknown"))
    result = benchmark.run_all(test_idea)
    result.metadata = {
        "source_file": eval_result_path,
        "eval_score": data.get("overall_score"),
        "eval_latency": data.get("total_latency_s"),
    }

    return result


def save_benchmark(result: BenchmarkResult, filename: Optional[str] = None):
    """Save benchmark result to JSON."""
    if filename is None:
        filename = f"benchmark_{result.run_id[:8]}.json"

    path = RESULTS_DIR / filename
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)

    logger.info("Benchmark saved to %s", path)
    return path


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Multi-Agent Intelligence Benchmark")
    parser.add_argument("--run", type=str, help="Path to eval result JSON to benchmark")
    parser.add_argument(
        "--compare", nargs=2, metavar=("BEFORE", "AFTER"),
        help="Compare two benchmark results",
    )
    parser.add_argument(
        "--dimension", type=str,
        help="Run only a specific dimension",
    )
    parser.add_argument(
        "--run-all", action="store_true",
        help="Run benchmark on all eval results in results/",
    )

    args = parser.parse_args()

    if args.compare:
        comparison = compare_benchmarks(args.compare[0], args.compare[1])
        print(format_comparison_report(comparison))

    elif args.run:
        result = run_benchmark_from_eval_results(args.run)
        path = save_benchmark(result)
        print(f"\nBenchmark complete: {result.overall_grade} ({result.overall_score:.1f}/10)")
        print(f"Saved to: {path}\n")
        for dim_name, dim_score in sorted(
            result.dimensions.items(), key=lambda x: x[1].score
        ):
            print(f"  {dim_name:<30} {dim_score.score:>5.1f}/10")
            for e in dim_score.evidence[:2]:
                print(f"    - {e}")

    elif args.run_all:
        results_files = list(RESULTS_DIR.glob("eval_run_*.json"))
        if not results_files:
            results_files = list(RESULTS_DIR.glob("baseline_*.json"))

        if not results_files:
            print("No eval results found in evaluation/results/")
        else:
            for rf in results_files:
                print(f"\nBenchmarking: {rf.name}")
                result = run_benchmark_from_eval_results(str(rf))
                save_benchmark(result, f"benchmark_{rf.stem}.json")
                print(f"  Grade: {result.overall_grade} ({result.overall_score:.1f}/10)")

    else:
        parser.print_help()
