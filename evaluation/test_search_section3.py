"""
Smoke test — run ONLY section 3 with search to verify search integration.
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
from services.search_service import search_for_section

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "3": [
        "EU AI Act academic research software compliance 2025",
        "GDPR SaaS academic procurement requirements Europe",
        "European academic publishing market regulation 2025",
    ],
}

EPISTEMIC_OS_IDEA = {
    "id": "grounded_epistemic_os",
    "name": "EpistemicOS — Pre-Submission Manuscript Diagnostics",
    "idea_summary": (
        "EpistemicOS is a B2B SaaS platform for epistemic validation and "
        "reviewer-readiness assessment of academic manuscripts."
    ),
    "business_type": "b2b_saas",
}


def _fetch_live_market_data(section_num: str) -> str:
    """Fetch live market data via search service for outward-facing sections."""
    queries = SEARCH_QUERIES.get(section_num, [])
    if not queries:
        return ""

    all_results = []
    for query in queries:
        logger.info("[Search] Querying: %s", query)
        results = search_for_section(section_num, query)
        logger.info("[Search] Got %d results for query", len(results))
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


async def test_section3():
    """Run section 3 only with search."""
    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0}),
    )
    haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

    all_ceo_data = load_all_ceo_data()
    logger.info("CEO data loaded: %d topics", len(all_ceo_data))

    section_num = "3"
    config = AGENT_CONFIGS[section_num]
    engine = IntelligenceEngine(bedrock, haiku_model)

    input_data = {
        "idea": EPISTEMIC_OS_IDEA,
        "ceo_provided_data": all_ceo_data,
    }

    ceo_section_data = get_relevant_ceo_data(section_num, all_ceo_data)
    ceo_chars = len(json.dumps(ceo_section_data, indent=2, default=str))
    logger.info(
        "[Grounded] Section %s — injecting %d chars of CEO data, keys=%s",
        section_num, ceo_chars, list(ceo_section_data.keys()),
    )

    logger.info("[Search] Starting search for section 3...")
    live_data = _fetch_live_market_data(section_num)
    input_data["live_market_data"] = live_data
    logger.info(
        "[Search] Section %s: injected %d chars of live market data",
        section_num, len(live_data),
    )

    logger.info("[Search] First 500 chars of live data:")
    logger.info(live_data[:500])

    start_time = time.time()
    try:
        parsed, reasoning_trace, token_usage = await engine.reason_and_produce(
            agent_role=config["role"],
            input_data=input_data,
            output_schema_prompt=config["schema_prompt"],
            cross_section_context=None,
            reasoning_budget=3,
        )
        latency = time.time() - start_time

        if parsed:
            logger.info(
                "[Test] Section 3 OK — %.1fs, confidence=%s",
                latency, parsed.get("confidence_score", "?"),
            )
            logger.info("[Test] Output keys: %s", list(parsed.keys()))
            logger.info("[Test] PEST factors: %d", len(parsed.get("pest_analysis", [])))
        else:
            logger.error("[Test] Section 3 FAILED to parse")

    except Exception as e:
        logger.error("[Test] Section 3 ERROR: %s", e)


if __name__ == "__main__":
    asyncio.run(test_section3())
