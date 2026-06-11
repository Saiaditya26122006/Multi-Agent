"""
Isolation test for new agents: Tech Stack (6.5) and Exit Strategy (14).

Tests each agent independently with mocked prior_outputs from existing
grounded eval to verify they produce valid output before running full pipeline.

Usage:
    python evaluation/test_new_agents.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.config import Config
from dotenv import load_dotenv

from agents.phase2.intelligence_engine import IntelligenceEngine
from ceo_data.loader import load_all_ceo_data, get_relevant_ceo_data
from evaluation.eval_runner import AGENT_CONFIGS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# Mock prior outputs from a successful grounded eval run
# These are simplified versions of what sections 4 and 12 would produce
MOCK_SECTION_4_OUTPUT = {
    "section_number": "4",
    "confidence_score": "medium",
    "capability_gaps": [
        {
            "gap": "No in-house AI/ML expertise for LLM integration",
            "severity": "high",
            "resolution": "Hire senior ML engineer by Month 3",
        },
        {
            "gap": "No academic domain expertise on team",
            "severity": "medium",
            "resolution": "Advisory board with 2-3 professors",
        },
    ],
    "roles_and_responsibilities": [
        {
            "title": "CEO/Co-founder",
            "responsibilities": ["Strategy", "Fundraising", "Key partnerships"],
            "required_skills": ["Business development", "Domain expertise", "Leadership"],
            "hire_timeline": "Month 0",
            "assigned_to": "Founder",
        },
        {
            "title": "CTO/Co-founder",
            "responsibilities": ["Product development", "Technical architecture", "Team building"],
            "required_skills": ["Full-stack development", "AI/ML", "System design"],
            "hire_timeline": "Month 0",
            "assigned_to": "Founder",
        },
        {
            "title": "ML Engineer",
            "responsibilities": ["LLM integration", "Model evaluation", "API development"],
            "required_skills": ["Python", "LangChain", "Prompt engineering"],
            "hire_timeline": "Month 3",
            "assigned_to": "To be hired",
        },
    ],
    "headcount_plan": {
        "year_1": {"count": 3, "cost": 180000},
        "year_2": {"count": 5, "cost": 350000},
        "year_3": {"count": 8, "cost": 600000},
    },
    "org_structure": "Flat startup — 2 co-founders, 1-2 engineers Year 1, expand to 8 by Year 3",
    "personnel_policy": "Equity-heavy compensation (10-15% ESOP), remote-first EU team, contractor-first then FTE conversions",
    "assumptions_used": [
        {
            "statement": "EU talent costs $60-80K/year fully loaded",
            "confidence": "medium",
            "source": "agent_inferred",
            "source_detail": "Industry benchmarks for startup salaries",
        }
    ],
    "uncertainties": [
        "Actual salary requirements may vary by location",
        "Hiring timeline assumes 2-3 months per role",
    ],
}

MOCK_SECTION_12_OUTPUT = {
    "section_number": "12",
    "confidence_score": "low",
    "risk_mitigation_actions": [
        "If CAC exceeds $500 by Month 6, pivot to lower-cost channels",
        "If <10 customers by Month 12, validate ICP assumptions",
        "Maintain 12-month runway minimum — raise seed by Month 9",
    ],
    "break_even_analysis": {
        "baseline_month": 18,
        "optimistic_month": 14,
        "pessimistic_month": 24,
        "units_required": 35,
    },
    "three_statement_model": {
        "pl_monthly_year1": [
            {"month": 1, "revenue": 0, "costs": 15000, "net": -15000},
            {"month": 6, "revenue": 5000, "costs": 18000, "net": -13000},
            {"month": 12, "revenue": 25000, "costs": 22000, "net": 3000},
        ],
        "pl_annual_years2_3": [
            {"year": 2, "revenue": 500000, "costs": 350000, "net": 150000, "headcount_cost": 350000},
            {"year": 3, "revenue": 1200000, "costs": 700000, "net": 500000, "headcount_cost": 600000},
        ],
        "balance_sheet": {
            "assets": 250000,
            "liabilities": 50000,
            "equity": 200000,
            "cash": 180000,
            "receivables": 40000,
            "payables": 30000,
        },
        "cash_flow": {
            "operating": -120000,
            "investing": -50000,
            "financing": 200000,
            "net_change": 30000,
        },
    },
    "dcf_valuation": None,
    "comps_table": None,
    "assumption_log": [
        {
            "name": "price_per_unit",
            "value": 5000,
            "label": "assumed",
            "source": "No WTP validation",
        },
        {
            "name": "volume_year1",
            "value": 30,
            "label": "assumed",
            "source": "No demand validation",
        },
    ],
    "uncertainties": [
        "Revenue assumes 30 customers Year 1 — no validation",
        "CAC estimate has no pilot data",
        "Break-even sensitive to pricing — $5K is ASSUMPTION",
    ],
}


async def test_tech_stack_agent():
    """Test section 6.5 (Tech Stack) with section 4 output."""
    logger.info("=" * 60)
    logger.info("TESTING TECH STACK AGENT (Section 6.5)")
    logger.info("=" * 60)

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0}),
    )
    haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

    config = AGENT_CONFIGS["6.5"]
    engine = IntelligenceEngine(bedrock, haiku_model)

    all_ceo_data = load_all_ceo_data()
    ceo_section_data = get_relevant_ceo_data("6.5", all_ceo_data)

    input_data = {
        "business_type": "b2b_saas",
        "product_description": "Academic manuscript validation SaaS with LLM-powered epistemic diagnostics",
        "team_capabilities": MOCK_SECTION_4_OUTPUT.get("roles_and_responsibilities", []),
        "delivery_model": "Cloud SaaS",
        "target_geography": "EU (Spain first, expand to other EU countries)",
        "compliance_requirements": ["GDPR", "EU AI Act"],
        "ceo_provided_data": ceo_section_data,
    }

    prior_outputs = {"4": MOCK_SECTION_4_OUTPUT}

    logger.info("Input data keys: %s", list(input_data.keys()))
    logger.info("Prior outputs: section 4 (org design)")

    try:
        parsed, reasoning_trace, token_usage = await engine.reason_and_produce(
            agent_role=config["role"],
            input_data=input_data,
            output_schema_prompt=config["schema_prompt"],
            cross_section_context=prior_outputs,
            reasoning_budget=3,
        )

        logger.info("✅ Tech Stack Agent completed successfully")
        logger.info("Input tokens: %d", token_usage.get("input_tokens", 0))
        logger.info("Output tokens: %d", token_usage.get("output_tokens", 0))

        if parsed:
            logger.info("\n--- OUTPUT VALIDATION ---")
            logger.info("✅ section_number: %s", parsed.get("section_number"))
            logger.info("✅ confidence_score: %s", parsed.get("confidence_score"))
            logger.info("✅ assumptions_used: %d items", len(parsed.get("assumptions_used", [])))
            logger.info("✅ uncertainties: %d items", len(parsed.get("uncertainties", [])))

            logger.info("\n--- KEY FIELDS ---")
            if "infrastructure" in parsed:
                infra = parsed["infrastructure"]
                logger.info("Infrastructure: cloud=%s, regions=%s, cost=%s/month",
                           infra.get("cloud_provider"), infra.get("regions"),
                           infra.get("estimated_monthly_cost"))
            if "data_privacy_compliance" in parsed:
                compliance = parsed["data_privacy_compliance"]
                logger.info("Compliance: regulations=%s, residency=%s",
                           compliance.get("regulations_covered"),
                           compliance.get("data_residency"))
            logger.info("Total tech cost: $%s/month, $%s/year",
                       parsed.get("total_tech_cost_monthly"),
                       parsed.get("total_tech_cost_annual"))

            logger.info("\n--- SAMPLE ASSUMPTIONS ---")
            for i, assumption in enumerate(parsed.get("assumptions_used", [])[:3], 1):
                if isinstance(assumption, dict):
                    logger.info("[%d] %s (confidence: %s, source: %s)",
                               i, assumption.get("statement", "")[:80],
                               assumption.get("confidence"), assumption.get("source"))
                else:
                    logger.info("[%d] %s (string format)", i, str(assumption)[:80])

            logger.info("\n--- FULL OUTPUT (JSON) ---")
            print(json.dumps(parsed, indent=2, default=str)[:2000] + "...")

            return parsed
        else:
            logger.error("❌ Tech Stack Agent returned None")
            return None

    except Exception as e:
        logger.error("❌ Tech Stack Agent failed: %s", e, exc_info=True)
        return None


async def test_exit_strategy_agent():
    """Test section 14 (Exit Strategy) with section 12 output."""
    logger.info("\n" + "=" * 60)
    logger.info("TESTING EXIT STRATEGY AGENT (Section 14)")
    logger.info("=" * 60)

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0}),
    )
    sonnet_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")

    config = AGENT_CONFIGS["14"]
    engine = IntelligenceEngine(bedrock, sonnet_model)

    all_ceo_data = load_all_ceo_data()
    ceo_section_data = get_relevant_ceo_data("14", all_ceo_data)

    # Extract financial data from section 12
    year_3_pl = MOCK_SECTION_12_OUTPUT["three_statement_model"]["pl_annual_years2_3"]
    year_3_revenue = year_3_pl[1]["revenue"] if len(year_3_pl) > 1 else 0

    input_data = {
        "business_type": "b2b_saas",
        "market_size_tam": 5000000000,  # $5B TAM (academic software)
        "year_3_revenue": year_3_revenue,
        "year_3_arr": year_3_revenue,  # SaaS: revenue ≈ ARR
        "break_even_year": 2,
        "profitability_year_3": 500000,
        "target_market": "EU business schools and research-intensive universities",
        "competitive_positioning": "Epistemic validation layer — no direct competitors",
        "industry_sector": "Education technology / Academic software",
        "geography": "EU (Spain/Germany/UK)",
        "founder_goals": "Strategic acquisition by academic software company in 5-7 years",
        "ceo_provided_data": ceo_section_data,
    }

    prior_outputs = {"12": MOCK_SECTION_12_OUTPUT}

    logger.info("Input data keys: %s", list(input_data.keys()))
    logger.info("Prior outputs: section 12 (financial)")
    logger.info("Year 3 revenue from section 12: $%s", year_3_revenue)

    try:
        parsed, reasoning_trace, token_usage = await engine.reason_and_produce(
            agent_role=config["role"],
            input_data=input_data,
            output_schema_prompt=config["schema_prompt"],
            cross_section_context=prior_outputs,
            reasoning_budget=3,
        )

        logger.info("✅ Exit Strategy Agent completed successfully")
        logger.info("Input tokens: %d", token_usage.get("input_tokens", 0))
        logger.info("Output tokens: %d", token_usage.get("output_tokens", 0))

        if parsed:
            logger.info("\n--- OUTPUT VALIDATION ---")
            logger.info("✅ section_number: %s", parsed.get("section_number"))
            logger.info("✅ confidence_score: %s", parsed.get("confidence_score"))
            logger.info("✅ assumptions_used: %d items", len(parsed.get("assumptions_used", [])))
            logger.info("✅ uncertainties: %d items", len(parsed.get("uncertainties", [])))

            logger.info("\n--- KEY FIELDS ---")
            if "exit_strategy" in parsed:
                exit_strat = parsed["exit_strategy"]
                logger.info("Exit path: %s", exit_strat.get("primary_exit_path"))
                logger.info("Exit timeline: %s", exit_strat.get("exit_timeline"))
                logger.info("Exit valuation: %s", exit_strat.get("exit_valuation"))
                if "acquisition_targets" in exit_strat:
                    logger.info("Acquisition targets: %d named", len(exit_strat.get("acquisition_targets", [])))
            if "investor_returns" in parsed:
                returns = parsed["investor_returns"]
                logger.info("Seed return: %s", returns.get("seed_return_multiple"))
                logger.info("Series A return: %s", returns.get("series_a_return_multiple"))

            logger.info("\n--- SAMPLE ASSUMPTIONS ---")
            for i, assumption in enumerate(parsed.get("assumptions_used", [])[:3], 1):
                if isinstance(assumption, dict):
                    logger.info("[%d] %s (confidence: %s, source: %s)",
                               i, assumption.get("statement", "")[:80],
                               assumption.get("confidence"), assumption.get("source"))
                else:
                    logger.info("[%d] %s (string format)", i, str(assumption)[:80])

            logger.info("\n--- FULL OUTPUT (JSON) ---")
            print(json.dumps(parsed, indent=2, default=str)[:2000] + "...")

            return parsed
        else:
            logger.error("❌ Exit Strategy Agent returned None")
            return None

    except Exception as e:
        logger.error("❌ Exit Strategy Agent failed: %s", e, exc_info=True)
        return None


async def main():
    """Run both isolation tests."""
    logger.info("Starting isolation tests for new agents...")
    logger.info("Models: Haiku for Tech Stack, Sonnet for Exit Strategy\n")

    tech_result = await test_tech_stack_agent()
    exit_result = await test_exit_strategy_agent()

    logger.info("\n" + "=" * 60)
    logger.info("ISOLATION TEST RESULTS")
    logger.info("=" * 60)
    logger.info("Tech Stack (6.5): %s", "✅ PASS" if tech_result else "❌ FAIL")
    logger.info("Exit Strategy (14): %s", "✅ PASS" if exit_result else "❌ FAIL")

    if tech_result and exit_result:
        logger.info("\n✅ Both agents ready for full pipeline integration")
        return 0
    else:
        logger.error("\n❌ At least one agent failed — fix before running full eval")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
