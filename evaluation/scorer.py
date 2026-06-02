"""
Scoring module for the evaluation harness.

Scores each section output on multiple dimensions:
  - Schema compliance (did it parse? are required fields present?)
  - Specificity (are numbers concrete or vague?)
  - Completeness (are all fields populated with real content?)
  - Confidence honesty (is the confidence label justified?)
  - Actionability (can Alex act on this?)

Each dimension is scored 0-10. Overall score is weighted average.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {
    "1": ["opportunity_description", "competitive_strategy", "objectives", "icp_hypothesis", "assumptions_used", "confidence_score"],
    "3": ["pest_analysis", "five_forces", "risks_opportunities", "market_context", "confidence_score"],
    "4": ["confidence_score", "capability_gaps", "roles_and_responsibilities", "headcount_plan", "assumptions_used", "uncertainties"],
    "5": ["swot_matrix", "strategic_implications", "confidence_score"],
    "8": ["target_market_analysis", "competitors", "competitive_advantages", "marketing_mix", "revenue_assumptions", "cac_assumptions", "market_entry_strategy", "confidence_score"],
    "10": ["confidence_score", "production_process", "cost_structure", "capacity_plan", "assumptions_used", "uncertainties"],
    "12": ["three_statement_model", "break_even_analysis", "assumption_log", "risk_mitigation_actions", "confidence_score"],
    "13": ["launch_programme", "prerequisite_conditions", "capital_plan", "contingency_scenarios", "confidence_score"],
    "executive_summary": ["executive_summary", "headline_metrics", "key_assumptions_flagged", "confidence_score"],
}

MIN_LENGTHS = {
    "opportunity_description": 50,
    "competitive_strategy": 30,
    "market_entry_strategy": 50,
    "strategic_implications": 50,
    "market_context": 50,
    "production_process": 50,
    "capacity_plan": 50,
    "executive_summary": 200,
    "org_structure": 30,
    "personnel_policy": 30,
}

LIST_MIN_COUNTS = {
    "competitors": 2,
    "competitive_advantages": 2,
    "objectives": 1,
    "assumptions_used": 1,
    "risk_mitigation_actions": 1,
    "launch_programme": 2,
    "contingency_scenarios": 1,
    "capability_gaps": 2,
    "roles_and_responsibilities": 2,
    "knowledge_gaps": 1,
    "uncertainties": 1,
    "key_assumptions_flagged": 1,
    "coherence_issues_resolved": 1,
}

VALID_SHORT_ENUMS = {"high", "medium", "low", "yes", "no", "none", "null"}


def score_section_output(section_num: str, output: Optional[dict]) -> dict:
    """Score a single section's output.

    Returns:
        {
            "schema_compliance": 0-10,
            "specificity": 0-10,
            "completeness": 0-10,
            "total": 0-10 (weighted average),
            "issues": [list of specific problems found],
        }
    """
    if output is None:
        return {
            "schema_compliance": 0,
            "specificity": 0,
            "completeness": 0,
            "total": 0,
            "issues": ["Output is None — agent failed to produce anything"],
        }

    issues = []

    # 1. Schema compliance (0-10)
    required = REQUIRED_FIELDS.get(section_num, [])
    present_count = sum(1 for f in required if f in output and output[f] is not None)
    schema_score = round((present_count / max(len(required), 1)) * 10, 1)

    for f in required:
        if f not in output or output[f] is None:
            issues.append(f"Missing required field: {f}")

    # 2. Specificity (0-10) — are values concrete or vague?
    specificity_points = 0
    specificity_checks = 0

    # Check minimum lengths
    for field, min_len in MIN_LENGTHS.items():
        if field in output:
            specificity_checks += 1
            value = output[field]
            if isinstance(value, str) and len(value) >= min_len:
                specificity_points += 1
            elif isinstance(value, str):
                issues.append(f"{field}: too short ({len(value)} chars, need {min_len})")

    # Check list minimum counts
    for field, min_count in LIST_MIN_COUNTS.items():
        if field in output:
            specificity_checks += 1
            value = output[field]
            if isinstance(value, list) and len(value) >= min_count:
                specificity_points += 1
            elif isinstance(value, list):
                issues.append(f"{field}: too few items ({len(value)}, need {min_count})")

    # Check for concrete numbers in revenue/financial fields
    rev = output.get("revenue_assumptions")
    if isinstance(rev, dict):
        specificity_checks += 1
        numeric_fields = ["price_per_unit", "volume_year1", "volume_year2", "volume_year3"]
        has_numbers = sum(
            1 for f in numeric_fields
            if f in rev and _has_numeric_content(rev[f])
        )
        if has_numbers >= 3:
            specificity_points += 1
        else:
            issues.append(f"revenue_assumptions: only {has_numbers}/4 fields with numeric content")

    be = output.get("break_even_analysis")
    if isinstance(be, dict):
        specificity_checks += 1
        month_value = be.get("baseline_month") or be.get("break_even_month")
        if month_value is not None and _has_numeric_content(month_value):
            specificity_points += 1
        else:
            issues.append("break_even_analysis: missing concrete break-even month number")

    # Check headline_metrics for exec summary
    hm = output.get("headline_metrics")
    if isinstance(hm, dict):
        specificity_checks += 1
        substantive_metrics = sum(
            1 for v in hm.values()
            if v is not None and str(v).strip()
        )
        if substantive_metrics >= 3:
            specificity_points += 1
        else:
            issues.append("headline_metrics: too few populated metrics")

    specificity_score = round((specificity_points / max(specificity_checks, 1)) * 10, 1)

    # 3. Completeness (0-10) — are fields populated with real content (not empty/placeholder)?
    completeness_points = 0
    completeness_checks = 0

    for key, value in output.items():
        if key.startswith("_") or key in ("input_tokens", "output_tokens", "model_used",
                                           "task_id", "section_number", "reasoning_trace"):
            continue
        completeness_checks += 1
        if _is_substantive(value):
            completeness_points += 1
        else:
            if not key.startswith("_"):
                issues.append(f"{key}: empty or placeholder value")

    completeness_score = round((completeness_points / max(completeness_checks, 1)) * 10, 1)

    # Weighted total
    total = round(
        schema_score * 0.3 + specificity_score * 0.4 + completeness_score * 0.3,
        1,
    )

    return {
        "schema_compliance": schema_score,
        "specificity": specificity_score,
        "completeness": completeness_score,
        "total": total,
        "issues": issues,
    }


def score_pipeline_run(run_result: dict) -> dict:
    """Score an entire pipeline run across all sections.

    Returns aggregate metrics + per-section scores.
    """
    sections = run_result.get("sections", {})
    section_scores = {}
    total_scores = []
    parse_successes = 0
    confidence_counts = {"high": 0, "medium": 0, "low": 0}

    for sec_num, sec_data in sections.items():
        output = sec_data.get("output")
        score = score_section_output(sec_num, output)
        section_scores[sec_num] = score
        total_scores.append(score["total"])

        if sec_data.get("parsed_successfully"):
            parse_successes += 1

        if output and isinstance(output, dict):
            conf = output.get("confidence_score", "medium")
            if isinstance(conf, str) and conf in confidence_counts:
                confidence_counts[conf] += 1

    total_sections = len(sections)
    schema_compliance_pct = round((parse_successes / max(total_sections, 1)) * 100, 1)
    overall_score = round(sum(total_scores) / max(len(total_scores), 1), 1)

    # Determine average confidence
    if confidence_counts["low"] > confidence_counts["high"]:
        avg_confidence = "low"
    elif confidence_counts["high"] > confidence_counts["medium"]:
        avg_confidence = "high"
    else:
        avg_confidence = "medium"

    return {
        "overall_score": overall_score,
        "schema_compliance": schema_compliance_pct,
        "avg_confidence": avg_confidence,
        "confidence_distribution": confidence_counts,
        "section_scores": section_scores,
        "total_sections": total_sections,
        "successful_parses": parse_successes,
    }


def _has_numeric_content(value) -> bool:
    """Check if a value contains meaningful numeric content.

    Accepts pure numbers, currency-prefixed strings, and qualified strings
    that contain at least one real number (e.g. '€15,000 pilot Year 1').
    Rejects empty strings, pure text with no numbers, and placeholders.
    """
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return False
        numbers_found = re.findall(r"[\d]+[,.]?[\d]*", cleaned)
        return len(numbers_found) > 0
    return False


def _is_numeric(value) -> bool:
    """Check if a value is numeric (int, float, or numeric string)."""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.replace(",", "").replace("$", "").replace("£", "").replace("€", ""))
            return True
        except ValueError:
            return False
    return False


def _is_substantive(value) -> bool:
    """Check if a value has real content (not empty, None, or placeholder).

    Recognizes valid short enum values (high/medium/low) as substantive.
    Still flags genuinely empty or placeholder short strings.
    """
    if value is None:
        return False
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in VALID_SHORT_ENUMS:
            return True
        return len(value.strip()) > 5
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return True
    return bool(value)
