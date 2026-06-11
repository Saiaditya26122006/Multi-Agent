"""
Simple isolation test - just run the agents and dump raw JSON output.
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
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Mock prior outputs
MOCK_SECTION_4 = {
    "section_number": "4",
    "headcount_plan": {"year_1": {"count": 3, "cost": 180000}},
}

MOCK_SECTION_12 = {
    "section_number": "12",
    "three_statement_model": {
        "pl_annual_years2_3": [
            {"year": 2, "revenue": 500000},
            {"year": 3, "revenue": 1200000},
        ]
    },
}


async def test_tech_stack():
    print("=" * 60)
    print("TECH STACK AGENT (6.5)")
    print("=" * 60)

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, retries={"max_attempts": 0}),
    )
    model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
    engine = IntelligenceEngine(bedrock, model)
    config = AGENT_CONFIGS["6.5"]

    input_data = {
        "business_type": "b2b_saas",
        "product_description": "Academic manuscript validation SaaS",
        "target_geography": "EU",
        "compliance_requirements": ["GDPR"],
    }

    parsed, _, tokens = await engine.reason_and_produce(
        agent_role=config["role"],
        input_data=input_data,
        output_schema_prompt=config["schema_prompt"],
        cross_section_context={"4": MOCK_SECTION_4},
        reasoning_budget=3,
    )

    if parsed:
        print(f"\n✅ SUCCESS")
        print(f"Tokens: {tokens.get('input_tokens')}/{tokens.get('output_tokens')}")
        print(f"Section: {parsed.get('section_number')}")
        print(f"Confidence: {parsed.get('confidence_score')}")
        print(f"Assumptions: {len(parsed.get('assumptions_used', []))}")
        print(f"Uncertainties: {len(parsed.get('uncertainties', []))}")
        print(f"\nFull JSON (first 1500 chars):")
        print(json.dumps(parsed, indent=2, default=str)[:1500])
        return True
    else:
        print("❌ FAILED - returned None")
        return False


async def test_exit_strategy():
    print("\n" + "=" * 60)
    print("EXIT STRATEGY AGENT (14)")
    print("=" * 60)

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, retries={"max_attempts": 0}),
    )
    model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
    engine = IntelligenceEngine(bedrock, model)
    config = AGENT_CONFIGS["14"]

    input_data = {
        "business_type": "b2b_saas",
        "year_3_revenue": 1200000,
        "year_3_arr": 1200000,
        "break_even_year": 2,
        "target_market": "EU universities",
        "industry_sector": "Education technology",
    }

    parsed, _, tokens = await engine.reason_and_produce(
        agent_role=config["role"],
        input_data=input_data,
        output_schema_prompt=config["schema_prompt"],
        cross_section_context={"12": MOCK_SECTION_12},
        reasoning_budget=3,
    )

    if parsed:
        print(f"\n✅ SUCCESS")
        print(f"Tokens: {tokens.get('input_tokens')}/{tokens.get('output_tokens')}")
        print(f"Section: {parsed.get('section_number')}")
        print(f"Confidence: {parsed.get('confidence_score')}")
        print(f"Assumptions: {len(parsed.get('assumptions_used', []))}")
        print(f"Uncertainties: {len(parsed.get('uncertainties', []))}")
        if "exit_strategy" in parsed:
            print(f"Exit path: {parsed['exit_strategy'].get('primary_exit_path')}")
        print(f"\nFull JSON (first 1500 chars):")
        print(json.dumps(parsed, indent=2, default=str)[:1500])
        return True
    else:
        print("❌ FAILED - returned None")
        return False


async def main():
    tech_ok = await test_tech_stack()
    exit_ok = await test_exit_strategy()

    print("\n" + "=" * 60)
    print(f"Tech Stack: {'✅ PASS' if tech_ok else '❌ FAIL'}")
    print(f"Exit Strategy: {'✅ PASS' if exit_ok else '❌ FAIL'}")
    print("=" * 60)

    return 0 if (tech_ok and exit_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
