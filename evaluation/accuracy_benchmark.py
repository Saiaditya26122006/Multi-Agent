"""
Comprehensive accuracy benchmark — measures end-to-end classification accuracy.

Tests content type detection + node placement across all 11 domains.
Target: 95% overall accuracy.
"""

import json
import logging
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# Comprehensive test cases spanning all domains and content types.
# Format: (fact_text, acceptable_node_ids, document_context, expected_content_type)
TEST_CASES = [
    # === BP.1 — Product, Workflow, and Scope ===
    (
        "The platform performs claim-evidence alignment analysis on uploaded manuscripts",
        ["BP.1.1", "BP.1.1.1", "BP.1.1.2", "BP.1.1.5", "BP.1.3"],
        "Product scope definition for EpistemicOS",
        "fact",
    ),
    (
        "Geographic scope is Spain and EU initially, expanding to UK in Year 2",
        ["BP.1.6", "BP.1.6.1", "BP.1.6.2", "BP.1.6.3"],
        "Product scope definition for EpistemicOS",
        "fact",
    ),

    # === BP.2 — Problem, Urgency, Hypothesis ===
    (
        "Researchers have no way to self-assess whether their claims are inferentially supported before submission",
        ["BP.2.1", "BP.2.1.1", "BP.2.1.2"],
        "Problem definition for EpistemicOS",
        "fact",
    ),
    (
        "I assume that ANECA accreditation pressure creates recurring institutional demand for quality audit",
        ["BP.2.3", "BP.2.5", "BP.2.5.1"],
        "Hypothesis framing for EpistemicOS",
        "assumption",
    ),

    # === BP.3 — Evidence Base and Source Governance ===
    (
        "According to RELX Annual Report 2023, STM revenues were GBP 2.7 billion",
        ["BP.3", "BP.3.1", "BP.3.2", "BP.3.3"],
        "Evidence sourcing for market analysis",
        "fact",
    ),

    # === BP.4 — Market Boundaries, Sizing, ICP ===
    (
        "There are approximately 500 AACSB/EQUIS-accredited business schools in Europe",
        ["BP.4", "BP.4.1", "BP.4.2", "BP.4.3", "BP.4.4"],
        "Market sizing analysis",
        "metric",
    ),
    (
        "Target market: research-active business schools with active doctoral programs in Spain and EU",
        ["BP.4", "BP.4.1", "BP.4.2", "BP.4.3", "BP.4.4", "BP.4.5"],
        "Market segmentation for EpistemicOS",
        "fact",
    ),

    # === BP.5 — Users, Buyers, Procurement ===
    (
        "Primary buyer persona: Research Dean or Vice-Dean of Research at business schools",
        ["BP.5", "BP.5.1", "BP.5.1.1", "BP.5.1.2"],
        "Buyer analysis for EpistemicOS",
        "fact",
    ),
    (
        "Willingness to pay is estimated at EUR 8,000-15,000 per institutional seat per year",
        ["BP.5", "BP.5.2", "BP.5.2.1", "BP.5.3", "BP.9.1", "BP.9.1.1", "BP.9.2", "BP.9.2.1", "BP.9.2.2", "BP.9.2.3"],
        "Pricing analysis for EpistemicOS",
        "assumption",
    ),

    # === BP.6 — Customer Discovery, Adoption ===
    (
        "We have 3 confirmed paying pilots at 8000 euros each — IESE, ESADE, and IE Business School",
        ["BP.6", "BP.6.1", "BP.6.2", "BP.6.3", "BP.10.3", "BP.10.3.4"],
        "Customer discovery progress",
        "fact",
    ),
    (
        "Need to schedule 15 discovery interviews with research deans by Month 3",
        ["BP.6", "BP.6.1", "BP.6.2"],
        "Customer discovery planning",
        "task",
    ),

    # === BP.7 — Legal, Regulatory, Deployability ===
    (
        "GDPR compliance is mandatory — all manuscript data must stay within EU servers",
        ["BP.7", "BP.7.1", "BP.7.2", "BP.7.3"],
        "Regulatory constraints for EpistemicOS",
        "constraint",
    ),

    # === BP.8 — Competitive Landscape ===
    (
        "Turnitin only detects copied text, it does not assess claim-evidence validity",
        ["BP.8", "BP.8.1", "BP.8.2", "BP.8.3", "BP.8.4"],
        "Competitive analysis for EpistemicOS",
        "fact",
    ),
    (
        "The biggest risk is that Elsevier builds this functionality into ScholarOne",
        ["BP.8", "BP.8.5", "BP.8.6", "BP.8.7"],
        "Competitive risk analysis",
        "risk",
    ),

    # === BP.9 — Business Model, Revenue, GTM ===
    (
        "Revenue model is annual institutional subscription with per-seat licensing",
        ["BP.9", "BP.9.1", "BP.9.1.1", "BP.9.2", "BP.9.5"],
        "Business model for EpistemicOS",
        "fact",
    ),
    (
        "We decided to go with per-seat institutional licensing over per-manuscript pricing",
        ["BP.9", "BP.9.1", "BP.9.1.1", "BP.9.2"],
        "Pricing decision for EpistemicOS",
        "decision",
    ),
    (
        "GTM strategy: direct outreach to AACSB deans via conference circuit + LinkedIn",
        ["BP.9", "BP.9.3", "BP.9.4", "BP.9.5"],
        "Go-to-market planning",
        "fact",
    ),

    # === BP.10 — Validation, PMF ===
    (
        "PMF is not: researchers like it or send positive feedback",
        ["BP.10", "BP.10.3", "BP.10.3.8"],
        "PMF options analysis for EpistemicOS",
        "fact",
    ),
    (
        "Validation target: 70% Cohen's kappa between EpistemicOS output and senior reviewer judgment",
        ["BP.10", "BP.10.1", "BP.10.2", "BP.10.3"],
        "Validation criteria for EpistemicOS",
        "metric",
    ),

    # === BP.11 — Investor Narrative, Finance / BP.9.5 Unit Economics ===
    (
        "Target: EUR 30,000 ARR by end of Year 1 from 3-4 institutional accounts",
        ["BP.9.5", "BP.9.5.1", "BP.9.5.2", "BP.9.5.3", "BP.11", "BP.11.1", "BP.11.2"],
        "Financial projections for EpistemicOS",
        "metric",
    ),
    (
        "Unit economics: CAC estimated at EUR 3,000 per institutional account via conference outreach",
        ["BP.9.5", "BP.9.5.1", "BP.9.5.2", "BP.9.5.3", "BP.11", "BP.11.1", "BP.11.2"],
        "Financial analysis for EpistemicOS",
        "assumption",
    ),

    # === Cross-domain / tricky cases ===
    (
        "Still unclear whether procurement at public universities requires a 12-month tender process",
        ["BP.5.4", "BP.5.4.1", "BP.5.4.2", "BP.5.4.3", "BP.7"],
        "Buyer process analysis",
        "open_question",
    ),
    (
        "Constraint: budget is capped at EUR 120,000 for first 12 months including salaries",
        ["BP.9.5", "BP.9.5.1", "BP.9.5.2", "BP.9.5.3", "BP.5.3", "BP.5.3.6", "BP.11"],
        "Financial constraints",
        "constraint",
    ),
]


def run_benchmark():
    """Run the full benchmark and report accuracy."""
    from web.handlers.feed_handler import classify_and_match_node, classify_content_type

    results = []
    correct_node = 0
    correct_type = 0
    total = len(TEST_CASES)
    start = time.time()

    logger.info(f"\n{'='*70}")
    logger.info(f"CLASSIFICATION ACCURACY BENCHMARK — {total} test cases")
    logger.info(f"{'='*70}\n")

    for i, (fact_text, acceptable_nodes, context, expected_type) in enumerate(TEST_CASES):
        case_start = time.time()

        # Content type
        type_result = classify_content_type(fact_text)
        actual_type = type_result["content_type"]
        type_correct = actual_type == expected_type

        # Node classification
        node_result = classify_and_match_node(
            fact_text,
            session_id=None,
            document_context=context,
            use_fast_model=False,
        )
        actual_node = node_result.get("node_id")
        confidence = node_result.get("confidence", "?")
        none_fit = node_result.get("none_fit", False)

        # Check if the node is under any acceptable prefix
        node_correct = False
        if actual_node:
            for acceptable in acceptable_nodes:
                if actual_node == acceptable or actual_node.startswith(acceptable + "."):
                    node_correct = True
                    break

        if type_correct:
            correct_type += 1
        if node_correct:
            correct_node += 1

        elapsed = time.time() - case_start
        status = "✓" if node_correct else "✗"
        type_status = "✓" if type_correct else "✗"

        logger.info(
            f"  [{i+1:2d}/{total}] {status} Node: {actual_node or 'NONE'} ({confidence}) | "
            f"{type_status} Type: {actual_type} | {elapsed:.1f}s"
        )
        if not node_correct:
            logger.info(
                f"         Expected one of: {acceptable_nodes}"
            )
            logger.info(
                f"         Reasoning: {node_result.get('reasoning', 'n/a')[:100]}"
            )

        results.append({
            "fact": fact_text[:80],
            "expected_nodes": acceptable_nodes,
            "actual_node": actual_node,
            "node_correct": node_correct,
            "expected_type": expected_type,
            "actual_type": actual_type,
            "type_correct": type_correct,
            "confidence": confidence,
            "none_fit": none_fit,
            "reasoning": node_result.get("reasoning", ""),
            "latency_s": round(elapsed, 2),
        })

    total_time = time.time() - start
    node_accuracy = (correct_node / total) * 100
    type_accuracy = (correct_type / total) * 100
    combined = sum(1 for r in results if r["node_correct"] and r["type_correct"])
    combined_accuracy = (combined / total) * 100

    logger.info(f"\n{'='*70}")
    logger.info(f"RESULTS")
    logger.info(f"{'='*70}")
    logger.info(f"  Node placement accuracy:  {correct_node}/{total} = {node_accuracy:.1f}%")
    logger.info(f"  Content type accuracy:    {correct_type}/{total} = {type_accuracy:.1f}%")
    logger.info(f"  Combined (both correct):  {combined}/{total} = {combined_accuracy:.1f}%")
    logger.info(f"  Total time:               {total_time:.1f}s")
    logger.info(f"  Avg per case:             {total_time/total:.1f}s")
    logger.info(f"{'='*70}\n")

    # Write results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": total,
        "node_accuracy": round(node_accuracy, 1),
        "type_accuracy": round(type_accuracy, 1),
        "combined_accuracy": round(combined_accuracy, 1),
        "total_time_s": round(total_time, 1),
        "results": results,
    }

    output_path = "evaluation/results/accuracy_benchmark_latest.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    logger.info(f"Results written to {output_path}")

    return output


if __name__ == "__main__":
    run_benchmark()
