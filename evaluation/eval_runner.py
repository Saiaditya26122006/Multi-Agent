"""
Evaluation Harness for the Multi-Agent Business Plan System.

Feeds test business ideas through the Intelligence Engine (bypassing SPADE)
and scores output quality per agent. Produces a scorecard for each run.

Usage:
    python evaluation/eval_runner.py                    # Run all test ideas
    python evaluation/eval_runner.py --idea eval_saas_crm  # Run one idea
    python evaluation/eval_runner.py --section 8       # Run one section only
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.config import Config
from dotenv import load_dotenv

from agents.phase2.intelligence_engine import IntelligenceEngine
from evaluation.test_ideas import TEST_IDEAS
from evaluation.scorer import score_section_output, score_pipeline_run

load_dotenv()
logger = logging.getLogger(__name__)

AGENT_CONFIGS = {
    "1": {
        "name": "Opportunity Analyst",
        "role": "Opportunity Analyst — assess business ideas, define competitive strategy, set Year 1 objectives, hypothesize ICP",
        "model_env": "CLAUDE_SONNET_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these fields: section_number, opportunity_description (min 50 chars), "
            "competitive_strategy (min 30 chars), objectives (list of quantified objectives), "
            "icp_hypothesis (with buyer_role, budget_process, decision_timeline, pain_points), "
            "assumptions_used (list with statement, confidence, source), uncertainties (list), "
            "confidence_score (high|medium|low), input_tokens (0), output_tokens (0)"
        ),
    },
    "3": {
        "name": "Environment Research",
        "role": "Environment Research — conduct PEST analysis, Porter's Five Forces, identify external risks and opportunities",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these fields: section_number, pest_analysis (list of factors), "
            "five_forces (list of forces with rating), risks_opportunities (object with risks and opportunities lists), "
            "market_context (summary string min 50 chars), assumptions_used, uncertainties, "
            "confidence_score (high|medium|low), input_tokens (0), output_tokens (0)"
        ),
    },
    "5": {
        "name": "SWOT Synthesizer",
        "role": "SWOT Synthesizer — combine PEST, Five Forces, and org capabilities into coherent SWOT matrix with strategic implications",
        "model_env": "CLAUDE_SONNET_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these fields: section_number, swot_matrix (with strengths, weaknesses, "
            "opportunities, threats — each a list of items), strategic_implications (min 50 chars), "
            "priority_issues (list), assumptions_used, uncertainties, confidence_score, input_tokens (0), output_tokens (0)"
        ),
    },
    "6.5": {
        "name": "Tech Stack & Data Privacy",
        "role": "Tech Stack & Data Privacy — design technical architecture, estimate infrastructure costs, ensure GDPR/CCPA/DPDP compliance",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys: section_number, infrastructure, "
            "ai_ml_stack, database, third_party_apis, authentication, data_privacy_compliance, "
            "total_tech_cost_monthly, total_tech_cost_annual, tech_risk_assessment, "
            "assumptions_used, uncertainties, confidence_score, input_tokens (0), output_tokens (0)"
        ),
    },
    "8": {
        "name": "Marketing Strategy",
        "role": "Marketing Strategy — build full marketing plan including target market, positioning, marketing mix, revenue and CAC assumptions",
        "model_env": "CLAUDE_SONNET_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these fields: section_number, target_market_analysis, "
            "competitors (min 2 entries), competitive_advantages (min 2 entries), marketing_mix, "
            "customer_relations, revenue_assumptions (with price_per_unit, volume_year1, volume_year2, volume_year3, sales_cycle_months), "
            "cac_assumptions (with cac_estimate, cac_source, confidence), market_entry_strategy (min 50 chars), "
            "assumptions_used, uncertainties, confidence_score, input_tokens (0), output_tokens (0)"
        ),
    },
    "12": {
        "name": "Financial Modelling",
        "role": "Financial Modelling — build 3-statement models, break-even analysis, DCF valuation, integrate Monte Carlo simulation",
        "model_env": "CLAUDE_SONNET_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys IN THIS ORDER: section_number, "
            "confidence_score (REQUIRED: high|medium|low), "
            "risk_mitigation_actions (REQUIRED: 2-5 items, each max 150 chars), "
            "break_even_analysis (baseline_month, optimistic_month, pessimistic_month, units_required), "
            "three_statement_model (pl_monthly_year1: MAX 12 rows each {month, revenue, costs, net}; "
            "pl_annual_years2_3: 2 rows; balance_sheet: max 6 fields; cash_flow: max 4 fields), "
            "dcf_valuation (dict|null, max 5 fields), comps_table (dict|null, max 3 companies), "
            "assumption_log (MAX 8 items with name, value, label, source), "
            "uncertainties (MAX 5 items, each max 150 chars), input_tokens (0), output_tokens (0). "
            "CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs."
        ),
    },
    "13": {
        "name": "Launch & Contingency",
        "role": "Launch & Contingency — build start-up programme with milestones, capital plan, critical path, contingency scenarios",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these fields: section_number, launch_programme (MAX 6 milestones with dates), "
            "prerequisite_conditions (MAX 4 items), capital_plan (2-3 sentences on funding needs), "
            "critical_path_item (1-2 sentences), contingency_scenarios (MAX 3 scenarios), "
            "exit_conditions (2-3 sentences with quantitative triggers), "
            "assumptions_used (MAX 5), uncertainties (MAX 4), confidence_score, input_tokens (0), output_tokens (0). "
            "Keep all string values under 200 characters. Be concise — brevity over exhaustiveness."
        ),
    },
    "14": {
        "name": "Exit Strategy & Contingency Plan",
        "role": "Exit Strategy & Contingency Plan — design exit path, model cap table, calculate investor returns, define contingency triggers",
        "model_env": "CLAUDE_SONNET_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys: section_number, exit_strategy, cap_table, "
            "funding_strategy, investor_returns, dilution_analysis, exit_risks, "
            "assumptions_used, uncertainties, confidence_score, input_tokens (0), output_tokens (0)"
        ),
    },
    "4": {
        "name": "Organisation Designer",
        "role": "Organisation Designer — design company structure, roles, capability gaps, headcount plan, personnel policy tied to revenue milestones",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys IN THIS ORDER: section_number, "
            "confidence_score (REQUIRED: high|medium|low), "
            "capability_gaps (REQUIRED: 2-5 items, each {gap (max 100 chars), severity, resolution}), "
            "roles_and_responsibilities (MAX 6 roles, each {title, responsibilities (max 3), required_skills (max 3), hire_timeline, assigned_to}), "
            "headcount_plan ({year_1: {count, cost}, year_2: {count, cost}, year_3: {count, cost}}), "
            "org_structure (max 200 chars), personnel_policy (50-300 chars), "
            "knowledge_gaps (MAX 4 items, each max 100 chars), "
            "assumptions_used (MAX 6 items with statement (max 120 chars), confidence, source, source_detail), "
            "uncertainties (MAX 4 items, each max 120 chars), input_tokens (0), output_tokens (0). "
            "CONSTRAINTS: Total output under 4000 tokens. Short phrases, not paragraphs."
        ),
    },
    "10": {
        "name": "Operations",
        "role": "Operations — define production/delivery process, cost structure with dollar amounts, capacity plan with bottleneck analysis, supplier strategy",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys IN THIS ORDER: section_number, "
            "confidence_score (REQUIRED: high|medium|low), "
            "production_process (REQUIRED: 50-400 chars, concise delivery steps), "
            "cost_structure ({fixed_costs: MAX 6 items, variable_costs: MAX 4 items, cogs_per_unit: float, source_labels}), "
            "capacity_plan (max 300 chars — 2x and 5x bottlenecks), "
            "supplier_strategy (max 200 chars|null), rd_plan (max 200 chars|null), ip_analysis (max 200 chars|null), "
            "assumptions_used (MAX 6 items with statement (max 120 chars), confidence, source, source_detail), "
            "uncertainties (MAX 4 items, each max 120 chars), input_tokens (0), output_tokens (0). "
            "CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs."
        ),
    },
    "executive_summary": {
        "name": "Executive Summary",
        "role": "Executive Summary — synthesize all section outputs into a one-page CEO brief with conflicts, primary risk, recommendation, and key assumptions to validate",
        "model_env": "CLAUDE_HAIKU_MODEL",
        "schema_prompt": (
            "Return ONLY valid JSON with these keys IN THIS ORDER: section_number, "
            "executive_summary (REQUIRED: 200-1500 chars, cover opportunity/advantage/financials/recommendation for CEO), "
            "headline_metrics (REQUIRED: {year1_revenue_range, break_even_month, primary_risk (max 100 chars), team_size_year1}), "
            "key_assumptions_flagged (MAX 5 items, each max 150 chars — only decision-critical), "
            "sections_included ([str] section numbers), sections_skipped ([str] section numbers), "
            "coherence_issues_resolved (MAX 3 items, each max 150 chars), "
            "input_tokens (0), output_tokens (0). "
            "CONSTRAINTS: Total output under 3000 tokens. Concise, numbers-first, no filler."
        ),
    },
}


class EvalRunner:
    """Runs test ideas through the Intelligence Engine and collects metrics."""

    def __init__(self):
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
            config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0}),
        )
        self.sonnet_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.results = []

    def _get_model(self, config: dict) -> str:
        env_key = config.get("model_env", "CLAUDE_SONNET_MODEL")
        if "haiku" in env_key.lower():
            return self.haiku_model
        return self.sonnet_model

    async def run_idea(self, idea: dict, sections: Optional[list] = None) -> dict:
        """Run a single test idea through all (or specified) sections."""
        idea_id = idea["id"]
        idea_name = idea["name"]
        logger.info("[EvalRunner] Starting: %s (%s)", idea_name, idea_id)

        run_result = {
            "idea_id": idea_id,
            "idea_name": idea_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "sections": {},
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_latency_seconds": 0,
            "errors": [],
        }

        default_order = ["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"]
        target_sections = sections or [s for s in default_order if s in AGENT_CONFIGS]
        prior_outputs = {}

        for section_num in target_sections:
            if section_num not in AGENT_CONFIGS:
                continue

            config = AGENT_CONFIGS[section_num]
            model_id = self._get_model(config)
            engine = IntelligenceEngine(self.bedrock, model_id)

            input_data = self._build_input_data(idea, section_num, prior_outputs)

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
                }

                if parsed:
                    prior_outputs[section_num] = parsed

                run_result["sections"][section_num] = section_result
                run_result["total_input_tokens"] += token_usage.get("input_tokens", 0)
                run_result["total_output_tokens"] += token_usage.get("output_tokens", 0)
                run_result["total_latency_seconds"] += latency

                logger.info(
                    "[EvalRunner] Section %s done — %s, %.1fs, %d tokens",
                    section_num, "OK" if parsed else "FAIL", latency,
                    token_usage.get("input_tokens", 0) + token_usage.get("output_tokens", 0),
                )

            except Exception as e:
                latency = time.time() - start_time
                logger.error("[EvalRunner] Section %s failed: %s", section_num, e)
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

        # Score the run
        run_result["scores"] = score_pipeline_run(run_result)

        self.results.append(run_result)
        return run_result

    def _build_input_data(self, idea: dict, section_num: str, prior_outputs: dict) -> dict:
        """Build the input data for a section, simulating what Mother Agent would assemble."""
        base = {
            "idea_summary": idea["idea_summary"],
            "ceo_assumptions": idea["ceo_assumptions"],
            "approved_decision": idea["approved_decision"],
            "business_type": idea.get("business_type", ""),
        }

        if section_num == "1":
            return base

        if section_num == "3":
            base["market_scope"] = idea["idea_summary"]
            if "1" in prior_outputs:
                base["icp_hypothesis"] = prior_outputs["1"].get("icp_hypothesis", {})
            return base

        if section_num == "5":
            if "3" in prior_outputs:
                base["pest_analysis"] = prior_outputs["3"].get("pest_analysis", [])
                base["five_forces"] = prior_outputs["3"].get("five_forces", [])
                base["risks_opportunities"] = prior_outputs["3"].get("risks_opportunities", {})
            if "1" in prior_outputs:
                base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")
            return base

        if section_num == "8":
            if "5" in prior_outputs:
                base["swot_matrix"] = prior_outputs["5"].get("swot_matrix", {})
                base["strategic_implications"] = prior_outputs["5"].get("strategic_implications", "")
            if "1" in prior_outputs:
                base["icp_hypothesis"] = prior_outputs["1"].get("icp_hypothesis", {})
                base["competitive_strategy"] = prior_outputs["1"].get("competitive_strategy", "")
            if "3" in prior_outputs:
                base["market_context"] = prior_outputs["3"].get("market_context", "")
            return base

        if section_num == "12":
            if "8" in prior_outputs:
                base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
                base["cac_assumptions"] = prior_outputs["8"].get("cac_assumptions", {})
            if "1" in prior_outputs:
                base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")
            return base

        if section_num == "13":
            if "8" in prior_outputs:
                base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
                base["market_entry_strategy"] = prior_outputs["8"].get("market_entry_strategy", "")
            if "12" in prior_outputs:
                base["break_even_analysis"] = prior_outputs["12"].get("break_even_analysis", {})
                base["probability_distribution"] = prior_outputs["12"].get("probability_distribution", [])
            return base

        if section_num == "4":
            if "1" in prior_outputs:
                base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")
            return base

        if section_num == "10":
            if "1" in prior_outputs:
                base["opportunity_description"] = prior_outputs["1"].get("opportunity_description", "")
            if "8" in prior_outputs:
                base["revenue_assumptions"] = prior_outputs["8"].get("revenue_assumptions", {})
            if "5" in prior_outputs:
                base["swot_matrix"] = prior_outputs["5"].get("swot_matrix", {})
            return base

        if section_num == "executive_summary":
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

        return base

    async def run_all(self, sections: Optional[list] = None) -> list:
        """Run all test ideas sequentially."""
        for idea in TEST_IDEAS:
            await self.run_idea(idea, sections=sections)
        return self.results

    def save_results(self, output_path: str = "evaluation/results"):
        """Save results to JSON file."""
        os.makedirs(output_path, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(output_path, f"eval_run_{timestamp}.json")

        with open(filepath, "w") as f:
            json.dump(self.results, f, indent=2, default=str)

        logger.info("[EvalRunner] Results saved to %s", filepath)
        return filepath

    def print_scorecard(self):
        """Print a summary scorecard to stdout."""
        if not self.results:
            print("No results to display.")
            return

        print("\n" + "=" * 80)
        print("EVALUATION SCORECARD")
        print("=" * 80)

        for run in self.results:
            print(f"\n{'─' * 80}")
            print(f"  Idea: {run['idea_name']} ({run['idea_id']})")
            print(f"  Total tokens: {run['total_input_tokens'] + run['total_output_tokens']:,}")
            print(f"  Total latency: {run['total_latency_seconds']:.1f}s")
            print(f"  Errors: {len(run['errors'])}")
            print(f"{'─' * 80}")

            scores = run.get("scores", {})
            print(f"  Overall Score: {scores.get('overall_score', 'N/A')}/10")
            print(f"  Schema Compliance: {scores.get('schema_compliance', 'N/A')}%")
            print(f"  Avg Confidence: {scores.get('avg_confidence', 'N/A')}")

            print(f"\n  {'Section':<8} {'Agent':<22} {'Time':<8} {'Tokens':<10} {'Parse':<7} {'Score'}")
            print(f"  {'─' * 70}")

            for sec_num, sec_data in run["sections"].items():
                name = sec_data.get("agent_name", "?")[:20]
                latency = f"{sec_data.get('latency_seconds', 0):.1f}s"
                tokens = sec_data.get("input_tokens", 0) + sec_data.get("output_tokens", 0)
                parsed = "OK" if sec_data.get("parsed_successfully") else "FAIL"
                sec_score = scores.get("section_scores", {}).get(sec_num, {}).get("total", "?")
                print(f"  {sec_num:<8} {name:<22} {latency:<8} {tokens:<10} {parsed:<7} {sec_score}/10")

        print(f"\n{'=' * 80}\n")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run evaluation harness")
    parser.add_argument("--idea", type=str, help="Run a specific test idea by ID")
    parser.add_argument("--section", type=str, help="Run only specific section(s), comma-separated")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args()

    log_level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(message)s")

    runner = EvalRunner()

    sections = args.section.split(",") if args.section else None

    if args.idea:
        idea = next((i for i in TEST_IDEAS if i["id"] == args.idea), None)
        if not idea:
            print(f"Unknown idea: {args.idea}")
            print(f"Available: {[i['id'] for i in TEST_IDEAS]}")
            return
        await runner.run_idea(idea, sections=sections)
    else:
        await runner.run_all(sections=sections)

    runner.print_scorecard()
    filepath = runner.save_results()
    print(f"Results saved: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
