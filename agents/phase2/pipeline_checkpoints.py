"""
Adaptive Pipeline — early-kill checkpoints.

Checks after critical sections whether the pipeline should continue,
pivot, or terminate. Prevents wasting 8+ LLM calls on doomed ideas.

Checkpoints after sections: 1 (Opportunity), 3 (Environment), 12 (Financial)
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CheckpointResult:
    """Result of a kill checkpoint evaluation."""

    triggered: bool
    section: str
    message: str
    severity: str  # "warning" | "critical"
    evidence: dict


KILL_CHECKPOINTS: dict[str, dict] = {}


def _check_opportunity_viability(output: dict) -> CheckpointResult:
    """Check if section 1 output signals a doomed idea."""
    confidence = output.get("confidence_score", "medium")
    strategy = output.get("competitive_strategy", "")
    uncertainties = output.get("uncertainties", [])

    triggers = []
    if confidence == "low":
        triggers.append("confidence_low")
    if len(strategy) < 50:
        triggers.append("weak_strategy")

    generic_signals = [
        "unique value proposition", "first-mover", "differentiation through",
        "innovative approach",
    ]
    strategy_lower = strategy.lower()
    if any(g in strategy_lower for g in generic_signals):
        triggers.append("generic_strategy")

    if len(uncertainties) > 5:
        triggers.append("high_uncertainty_count")

    triggered = len(triggers) >= 2
    return CheckpointResult(
        triggered=triggered,
        section="1",
        message=KILL_CHECKPOINTS["1"]["message"],
        severity="critical" if triggered else "warning",
        evidence={"triggers": triggers, "confidence": confidence},
    )


def _check_market_hostility(output: dict) -> CheckpointResult:
    """Check if section 3 shows unwinnable market."""
    five_forces = output.get("five_forces", {})
    if not five_forces:
        five_forces = output.get("porters_five_forces", {})

    high_threat_count = 0
    force_details = {}
    for force_name, force_data in five_forces.items():
        if isinstance(force_data, dict):
            threat = force_data.get("threat_level", "").lower()
        elif isinstance(force_data, str):
            threat = force_data.lower()
        else:
            continue

        if threat in ("high", "very_high", "critical"):
            high_threat_count += 1
            force_details[force_name] = threat

    market_size = output.get("market_size", {})
    tam = 0
    if isinstance(market_size, dict):
        tam = market_size.get("tam", market_size.get("total_addressable_market", 0))
    if isinstance(tam, str):
        tam = 0

    triggers = []
    if high_threat_count >= 4:
        triggers.append(f"{high_threat_count}/5 forces high threat")
    if 0 < tam < 1_000_000:
        triggers.append(f"TAM too small: ${tam:,}")

    triggered = high_threat_count >= 4 or (high_threat_count >= 3 and tam < 5_000_000)
    return CheckpointResult(
        triggered=triggered,
        section="3",
        message=KILL_CHECKPOINTS["3"]["message"],
        severity="critical" if triggered else "warning",
        evidence={
            "high_threat_count": high_threat_count,
            "force_details": force_details,
            "tam": tam,
            "triggers": triggers,
        },
    )


def _check_financial_viability(output: dict) -> CheckpointResult:
    """Check if section 12 shows unviable financials."""
    break_even = output.get("break_even_analysis", {})
    baseline_month = 0
    if isinstance(break_even, dict):
        baseline_month = break_even.get("baseline_month", break_even.get("months_to_breakeven", 0))
    if isinstance(baseline_month, str):
        try:
            baseline_month = int(baseline_month)
        except ValueError:
            baseline_month = 0

    unit_economics = output.get("unit_economics", {})
    ltv_cac_ratio = 0
    if isinstance(unit_economics, dict):
        ltv = unit_economics.get("ltv", unit_economics.get("lifetime_value", 0))
        cac = unit_economics.get("cac", unit_economics.get("customer_acquisition_cost", 1))
        if isinstance(ltv, (int, float)) and isinstance(cac, (int, float)) and cac > 0:
            ltv_cac_ratio = ltv / cac

    triggers = []
    if baseline_month > 48:
        triggers.append(f"break-even at {baseline_month} months (>48)")
    if 0 < ltv_cac_ratio < 1.5:
        triggers.append(f"LTV/CAC ratio = {ltv_cac_ratio:.1f} (<1.5)")

    confidence = output.get("confidence_score", "medium")
    if confidence == "low":
        triggers.append("financial_confidence_low")

    triggered = baseline_month > 48 or ltv_cac_ratio < 1.0
    return CheckpointResult(
        triggered=triggered,
        section="12",
        message=KILL_CHECKPOINTS["12"]["message"],
        severity="critical" if triggered else "warning",
        evidence={
            "break_even_month": baseline_month,
            "ltv_cac_ratio": ltv_cac_ratio,
            "triggers": triggers,
        },
    )


KILL_CHECKPOINTS["1"] = {
    "name": "Opportunity viability",
    "condition": _check_opportunity_viability,
    "message": (
        "Opportunity analysis shows no clear differentiation or "
        "timing rationale. Continue building a full plan?"
    ),
    "severity": "critical",
}
KILL_CHECKPOINTS["3"] = {
    "name": "Market hostility",
    "condition": _check_market_hostility,
    "message": (
        "Environment analysis shows hostile market conditions. "
        "4+ competitive forces at high threat. Market may be unwinnable."
    ),
    "severity": "critical",
}
KILL_CHECKPOINTS["12"] = {
    "name": "Financial viability",
    "condition": _check_financial_viability,
    "message": (
        "Financial model shows break-even exceeds 48 months or "
        "negative unit economics. Business model may not be viable."
    ),
    "severity": "critical",
}


def evaluate_checkpoint(section: str, output: dict) -> Optional[CheckpointResult]:
    """Evaluate whether a section output triggers a kill checkpoint."""
    checkpoint = KILL_CHECKPOINTS.get(str(section))
    if not checkpoint:
        return None

    condition_fn = checkpoint["condition"]
    result = condition_fn(output)

    if result.triggered:
        logger.warning(
            "[Checkpoint] Section %s triggered kill checkpoint: %s",
            section, result.evidence,
        )

    return result


def should_continue_pipeline(
    section: str, output: dict, prior_outputs: dict
) -> tuple[bool, Optional[CheckpointResult]]:
    """Decide if pipeline should continue after a section completes.

    Returns (should_continue, checkpoint_result).
    """
    result = evaluate_checkpoint(section, output)
    if result is None:
        return True, None

    if not result.triggered:
        return True, result

    # Check if prior sections also had warnings — compound risk
    prior_warnings = 0
    for sec, prior_output in prior_outputs.items():
        if isinstance(prior_output, dict):
            if prior_output.get("confidence_score") == "low":
                prior_warnings += 1
            if prior_output.get("_unresolved_challenges"):
                prior_warnings += 1

    if prior_warnings >= 2 and result.severity == "critical":
        logger.warning(
            "[Checkpoint] Compound risk: section %s critical + %d prior warnings",
            section, prior_warnings,
        )

    return False, result
