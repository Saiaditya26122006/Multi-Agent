"""
Grounded evaluation run — feeds the REAL EpistemicOS CEO data through all 9 sections.

This is the first run where agents receive actual ceo_provided_data from the ingestion
layer instead of a hardcoded test idea. The point: does the system reason about
EpistemicOS specifically, and does it respect epistemic tags (ASSUMPTION vs CONFIRMED)?

Usage:
    python evaluation/run_grounded_eval.py
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.config import Config
from dotenv import load_dotenv

from agents.phase2.intelligence_engine import IntelligenceEngine
from ceo_data.loader import get_relevant_ceo_data, load_all_ceo_data
from evaluation.eval_runner import AGENT_CONFIGS
from evaluation.scorer import score_pipeline_run
from services.search_service import search_for_section

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "1": [
        "academic manuscript validation software market size 2025",
        "epistemic validation tools universities Europe market",
        "pre-submission research diagnostics SaaS competitors",
    ],
    "3": [
        "EU AI Act academic research software compliance 2025",
        "GDPR SaaS academic procurement requirements Europe",
        "European academic publishing market regulation 2025",
    ],
    "8": [
        "institutional SaaS pricing universities Europe 2025",
        "academic software procurement business schools budget",
        "research quality management tools university pricing",
    ],
    "12": [
        "B2B SaaS gross margin benchmarks institutional 2025",
        "academic software CAC payback period benchmarks",
        "university SaaS contract value annual recurring revenue",
    ],
}

EPISTEMIC_OS_IDEA = {
    "id": "grounded_epistemic_os",
    "name": "EpistemicOS — Pre-Submission Manuscript Diagnostics",
    "idea_summary": (
        "EpistemicOS is a B2B SaaS platform for epistemic validation and "
        "reviewer-readiness assessment of academic manuscripts. It operates as a "
        "pre-submission research verification layer that evaluates whether a paper's "
        "claims are supported by appropriate evidence, methodologically justified, "
        "inferentially coherent, traceable to sources, and aligned with reviewer "
        "expectations. Initial domain: management research and business schools. "
        "Initial geography: Spain/EU first."
    ),
    "ceo_assumptions": [
        {"question": "Who is your primary buyer?", "answer": "Research-intensive business schools and universities — research deans, doctoral program directors, research support offices. This is an ASSUMPTION not yet validated by buyer interviews."},
        {"question": "What is your pricing model?", "answer": "Institutional SaaS subscription (annual contracts, per-school/department/seat). This is an ASSUMPTION — no pricing validation or WTP evidence exists."},
        {"question": "What validates the product?", "answer": "Must demonstrate senior-reviewer-level reliability. No accuracy data, pilot data, or inter-rater studies exist yet. Evidence strength is weak external validation despite strong internal definition."},
        {"question": "What is the current product stage?", "answer": "Early concept / MVP-stage framing. No working product evidence, pilot data, or usage evidence. Stage status is CONTRADICTION — described as MVP in some contexts but no artifact exists."},
        {"question": "What is the main strategic risk?", "answer": "Whether epistemic validation is perceived as sufficiently valuable to become a repeated institutional workflow behavior rather than an interesting research-quality concept."},
    ],
    "approved_decision": {
        "decision": "approved_with_conditions",
        "rationale": "Strong conceptual definition and differentiation; however, zero external validation exists — no interviews, no WTP evidence, no pilots, no accuracy data.",
        "risk_flags": [
            "Zero buyer interviews conducted",
            "No WTP or procurement evidence",
            "Product stage is CONTRADICTION (described as MVP but no artifact exists)",
            "All buyer personas are ASSUMPTION status",
        ],
    },
    "business_type": "b2b_saas",
}


def _fetch_live_market_data(section_num: str) -> str:
    """Fetch live market data via search service for outward-facing sections."""
    queries = SEARCH_QUERIES.get(section_num, [])
    if not queries:
        return ""

    all_results = []
    for query in queries:
        results = search_for_section(section_num, query)
        all_results.extend(results)

    if not all_results:
        return "No live market data retrieved."

    lines = [f"Retrieved {datetime.utcnow().strftime('%Y-%m-%d')}:"]
    for i, r in enumerate(all_results[:8], 1):
        lines.append(
            f"[{i}] {r['title']} — {r['snippet'][:200]} "
            f"(Source: {r['url']}, Freshness: {r['freshness']})"
        )
    return "\n".join(lines)


async def run_grounded_eval():
    """Run all 9 sections with real CEO data injected."""
    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0}),
    )
    sonnet_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
    haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

    all_ceo_data = load_all_ceo_data()
    logger.info("CEO data loaded: %d topics", len(all_ceo_data))

    section_order = ["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"]
    prior_outputs = {}

    run_result = {
        "idea_id": EPISTEMIC_OS_IDEA["id"],
        "idea_name": EPISTEMIC_OS_IDEA["name"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "grounded": True,
        "ceo_data_topics": list(all_ceo_data.keys()),
        "sections": {},
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_latency_seconds": 0,
        "errors": [],
    }

    for section_num in section_order:
        config = AGENT_CONFIGS[section_num]
        model_env = config.get("model_env", "CLAUDE_SONNET_MODEL")
        model_id = haiku_model if "haiku" in model_env.lower() else sonnet_model

        engine = IntelligenceEngine(bedrock, model_id)

        input_data = _build_grounded_input(
            EPISTEMIC_OS_IDEA, section_num, prior_outputs, all_ceo_data
        )

        ceo_section_data = get_relevant_ceo_data(section_num, all_ceo_data)
        ceo_chars = len(json.dumps(ceo_section_data, indent=2, default=str))
        logger.info(
            "[Grounded] Section %s — injecting %d chars of CEO data, keys=%s",
            section_num, ceo_chars, list(ceo_section_data.keys()),
        )

        if section_num in ["1", "3", "8", "12"]:
            live_data = _fetch_live_market_data(section_num)
            input_data["live_market_data"] = live_data
            logger.info(
                "[Search] Section %s: injected %d chars of live market data",
                section_num, len(live_data),
            )

        start_time = time.time()
        try:
            parsed, reasoning_trace, token_usage = await engine.reason_and_produce(
                agent_role=config["role"],
                input_data=input_data,
                output_schema_prompt=config["schema_prompt"],
                cross_section_context=prior_outputs if prior_outputs else None,
                reasoning_budget=3,
            )
            latency = time.time() - start_time

            section_result = {
                "section_number": section_num,
                "agent_name": config["name"],
                "model": model_id,
                "latency_seconds": round(latency, 2),
                "input_tokens": token_usage.get("input_tokens", 0),
                "output_tokens": token_usage.get("output_tokens", 0),
                "parsed_successfully": parsed is not None,
                "output": parsed,
                "reasoning_trace": reasoning_trace,
                "ceo_data_injected_chars": ceo_chars,
                "ceo_data_keys": list(ceo_section_data.keys()),
            }

            if parsed:
                prior_outputs[section_num] = parsed
                conf = parsed.get("confidence_score", "?")
                logger.info(
                    "[Grounded] Section %s OK — %.1fs, confidence=%s",
                    section_num, latency, conf,
                )
            else:
                logger.warning("[Grounded] Section %s parsed=None", section_num)

            run_result["sections"][section_num] = section_result
            run_result["total_input_tokens"] += token_usage.get("input_tokens", 0)
            run_result["total_output_tokens"] += token_usage.get("output_tokens", 0)
            run_result["total_latency_seconds"] += latency

        except Exception as e:
            latency = time.time() - start_time
            logger.error("[Grounded] Section %s FAILED: %s", section_num, e)
            run_result["sections"][section_num] = {
                "section_number": section_num,
                "agent_name": config["name"],
                "model": model_id,
                "latency_seconds": round(latency, 2),
                "parsed_successfully": False,
                "error": str(e),
            }
            run_result["errors"].append(f"Section {section_num}: {e}")

    run_result["completed_at"] = datetime.now(timezone.utc).isoformat()
    run_result["scores"] = score_pipeline_run(run_result)

    output_dir = Path("evaluation/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = output_dir / f"grounded_epistemic_os_{timestamp}.json"

    with open(filepath, "w") as f:
        json.dump(run_result, f, indent=2, default=str)

    logger.info("Results saved: %s", filepath)
    _print_scorecard(run_result)
    return str(filepath)


def _build_grounded_input(
    idea: dict, section_num: str, prior_outputs: dict, all_ceo_data: dict
) -> dict:
    """Build input_data with real CEO data injected."""
    base = {
        "idea_summary": idea["idea_summary"],
        "ceo_assumptions": idea["ceo_assumptions"],
        "approved_decision": idea["approved_decision"],
        "business_type": idea.get("business_type", ""),
        "ceo_provided_data": get_relevant_ceo_data(section_num, all_ceo_data),
    }

    if section_num == "3":
        base["market_scope"] = idea["idea_summary"]
        if "1" in prior_outputs:
            base["icp_hypothesis"] = prior_outputs["1"].get("icp_hypothesis", {})

    elif section_num == "5":
        if "3" in prior_outputs:
            base["pest_analysis"] = prior_outputs["3"].get("pest_analysis", [])
            base["five_forces"] = prior_outputs["3"].get("five_forces", [])
            base["risks_opportunities"] = prior_outputs["3"].get("risks_opportunities", {})
        if "1" in prior_outputs:
            base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")

    elif section_num == "8":
        if "5" in prior_outputs:
            base["swot_matrix"] = prior_outputs["5"].get("swot_matrix", {})
            base["strategic_implications"] = prior_outputs["5"].get("strategic_implications", "")
        if "1" in prior_outputs:
            base["icp_hypothesis"] = prior_outputs["1"].get("icp_hypothesis", {})
            base["competitive_strategy"] = prior_outputs["1"].get("competitive_strategy", "")
        if "3" in prior_outputs:
            base["market_context"] = prior_outputs["3"].get("market_context", "")

    elif section_num == "4":
        if "1" in prior_outputs:
            base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")

    elif section_num == "10":
        if "1" in prior_outputs:
            base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")
        if "8" in prior_outputs:
            base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
        if "5" in prior_outputs:
            base["swot_matrix"] = prior_outputs["5"].get("swot_matrix", {})

    elif section_num == "12":
        if "8" in prior_outputs:
            base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
            base["cac_assumptions"] = prior_outputs["8"].get("cac_assumptions", {})
        if "1" in prior_outputs:
            base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")

    elif section_num == "13":
        if "8" in prior_outputs:
            base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
            base["market_entry_strategy"] = prior_outputs["8"].get("market_entry_strategy", "")
        if "12" in prior_outputs:
            base["break_even_analysis"] = prior_outputs["12"].get("break_even_analysis", {})

    elif section_num == "executive_summary":
        base["completed_sections"] = prior_outputs
        flagged = []
        for sec_output in prior_outputs.values():
            if isinstance(sec_output, dict):
                for assumption in sec_output.get("assumptions_used", []):
                    if isinstance(assumption, dict) and assumption.get("confidence") == "low":
                        flagged.append(assumption.get("statement", str(assumption)))
                    elif isinstance(assumption, dict) and assumption.get("source") == "assumed":
                        flagged.append(assumption.get("statement", str(assumption)))
        base["flagged_assumptions"] = flagged[:10]

    return base


def _print_scorecard(run_result: dict):
    """Print summary to stdout."""
    print("\n" + "=" * 80)
    print("GROUNDED EVALUATION — EpistemicOS")
    print("=" * 80)
    print(f"  Total tokens: {run_result['total_input_tokens'] + run_result['total_output_tokens']:,}")
    print(f"  Total latency: {run_result['total_latency_seconds']:.1f}s")
    print(f"  Errors: {len(run_result['errors'])}")

    scores = run_result.get("scores", {})
    print(f"  Overall Score: {scores.get('overall_score', 'N/A')}/10")

    print(f"\n  {'Section':<8} {'Agent':<22} {'Time':<8} {'Tokens':<10} {'Parse':<7} {'Confidence'}")
    print(f"  {'─' * 72}")

    for sec_num in ["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"]:
        sec_data = run_result["sections"].get(sec_num, {})
        if not sec_data:
            continue
        name = sec_data.get("agent_name", "?")[:20]
        latency = f"{sec_data.get('latency_seconds', 0):.1f}s"
        tokens = sec_data.get("input_tokens", 0) + sec_data.get("output_tokens", 0)
        parsed = "OK" if sec_data.get("parsed_successfully") else "FAIL"
        conf = "?"
        if sec_data.get("output"):
            conf = sec_data["output"].get("confidence_score", "?")
        print(f"  {sec_num:<8} {name:<22} {latency:<8} {tokens:<10} {parsed:<7} {conf}")

    print(f"\n{'=' * 80}\n")


if __name__ == "__main__":
    asyncio.run(run_grounded_eval())
