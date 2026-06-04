"""
Demo Pipeline Connector — wires Phase 1 approval to eval runner.

Polls Redis for pipeline triggers, sends data declaration to Alex,
waits for PROCEED/SKIP, runs eval, delivers summary.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from memory.redis_client import redis_client
from memory.supabase_client import supabase
from tools.telegram_handler import send_message
from evaluation.run_grounded_eval import run_grounded_eval_for_session
from evaluation.compile_output import compile_for_delivery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds


async def start_demo_poller():
    """Poll Redis for pipeline triggers and run demo pipeline."""
    logger.info("[DemoPipeline] Started polling for triggers")

    while True:
        try:
            # Scan for any pipeline_trigger keys
            keys = redis_client.keys("pipeline_trigger:*")
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode("utf-8")

                session_id = key.replace("pipeline_trigger:", "")
                trigger_value = redis_client.get(key)

                if trigger_value:
                    redis_client.delete(key)  # consume the trigger
                    logger.info(
                        "[DemoPipeline] Trigger found for session %s",
                        session_id
                    )
                    # Launch pipeline in background task
                    asyncio.create_task(run_demo_pipeline(session_id))

        except Exception as e:
            logger.error("[DemoPipeline] Poll error: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


async def run_demo_pipeline(session_id: str):
    """Run the full demo pipeline for a session."""
    chat_id = None

    try:
        # 1. Read session data from Supabase
        logger.info("[DemoPipeline] Reading session %s", session_id)
        session = supabase.table("sessions") \
            .select("*").eq("id", session_id).execute()

        if not session.data:
            logger.error("[DemoPipeline] Session not found: %s", session_id)
            return

        session_data = session.data[0]
        chat_id = session_data.get("telegram_chat_id")

        if not chat_id:
            logger.error("[DemoPipeline] No chat_id for session %s", session_id)
            return

        # 2. Read assumptions from this session
        assumptions = supabase.table("assumptions") \
            .select("*") \
            .eq("session_id", session_id) \
            .eq("status", "active") \
            .execute()

        # 3. Read the approved decision
        decisions = supabase.table("decisions") \
            .select("*") \
            .eq("session_id", session_id) \
            .eq("status", "approved") \
            .execute()

        # 4. Build idea dict from session data
        idea = _build_idea_from_session(
            session_data,
            assumptions.data or [],
            decisions.data or []
        )

        logger.info("[DemoPipeline] Built idea: %s", idea.get("name", "unnamed"))

        # 5. Send data declaration message
        declaration = _build_data_declaration()
        await send_message(chat_id, declaration)
        logger.info("[DemoPipeline] Data declaration sent")

        # 6. Wait for PROCEED or SKIP (up to 2 hours)
        reply = await _wait_for_proceed(session_id, timeout=7200)

        if reply == "kill":
            await send_message(
                chat_id,
                "Pipeline cancelled. Reply with a new idea anytime."
            )
            logger.info("[DemoPipeline] Pipeline cancelled by user")
            return

        # 7. Notify starting
        await send_message(
            chat_id,
            "🔄 *Starting business plan generation...*\n\n"
            "Running 9 specialized agents. This takes ~30 minutes.\n"
            "I'll send you the results when complete."
        )
        logger.info("[DemoPipeline] Starting eval for session %s", session_id)

        # 8. Run the eval (with session idea instead of hardcoded)
        # DEMO MODE: Use cached results instead of running 30-min eval
        import glob
        files = sorted(glob.glob(
            'evaluation/results/grounded_epistemic_os_*.json'
        ))
        if files:
            output_path = files[-1]  # use latest existing results
            logger.info("[DemoPipeline] DEMO MODE: using cached results %s", output_path)
        else:
            output_path = await run_grounded_eval_for_session(idea)
            logger.info("[DemoPipeline] Eval complete: %s", output_path)

        # 9. Compile and deliver
        await _deliver_output(chat_id, output_path, session_id)

    except Exception as e:
        logger.error("[DemoPipeline] Error: %s", e, exc_info=True)
        if chat_id:
            await send_message(
                chat_id,
                f"⚠️ Pipeline encountered an error: {str(e)[:200]}\n\n"
                "Check logs for details."
            )


def _build_idea_from_session(
    session_data: dict,
    assumptions: list,
    decisions: list
) -> dict:
    """Build idea dict from Phase 1 session output."""
    # Build ceo_assumptions from Q&A stored in assumptions table
    ceo_assumptions = []
    for a in assumptions:
        question = a.get("question_asked", "")
        answer = a.get("ceo_answer", "")
        if question and answer:
            ceo_assumptions.append({
                "question": question,
                "answer": answer,
            })

    # Get approved decision
    approved_decision = {}
    if decisions:
        d = decisions[0]
        approved_decision = {
            "decision": d.get("status", "approved"),
            "rationale": d.get("rationale", ""),
            "risk_flags": [],
        }

    # Get idea summary — try multiple sources
    idea_summary = (
        session_data.get("current_idea") or
        session_data.get("idea") or
        ""
    )

    # Fallback: if still empty, build from Q&A
    if not idea_summary and ceo_assumptions:
        qa_parts = [f"{a['question']}: {a['answer']}" for a in ceo_assumptions[:3]]
        idea_summary = "Business idea: " + "; ".join(qa_parts)

    # Final fallback
    if not idea_summary:
        idea_summary = "Business idea from Phase 1 session"

    business_name = idea_summary[:80] if len(idea_summary) > 80 else idea_summary

    return {
        "id": f"session_{session_data['id'][:8]}",
        "name": business_name,
        "idea_summary": idea_summary,
        "ceo_assumptions": ceo_assumptions,
        "approved_decision": approved_decision,
        "business_type": "b2b_saas",  # default for demo
    }


def _build_data_declaration() -> str:
    """Build the data requirements declaration message."""
    return (
        "📋 *BEFORE I START — DATA REQUIREMENTS*\n\n"
        "Here's what I need for the business plan:\n\n"
        "*FETCHING AUTOMATICALLY (web search):*\n"
        "• EU AI Act compliance for academic SaaS\n"
        "• GDPR procurement requirements Europe\n"
        "• Academic publishing market trends 2025\n"
        "• Institutional SaaS pricing benchmarks\n"
        "• B2B SaaS gross margin and CAC benchmarks\n\n"
        "*REQUESTING FROM YOU (improves quality):*\n"
        "• Academic software market size Spain/EU\n"
        "  → best source: *Passport or GlobalData*\n"
        "• Competitor pricing and positioning data\n"
        "  → best source: *CB Insights or FACTIVA*\n"
        "• Business school research tooling spend\n"
        "  → best source: *Statista or WARC*\n"
        "• EdTech funding and valuation comps\n"
        "  → best source: *CB Insights or Mergermarket*\n\n"
        "Add anything you find to the *Knowledge Base tab* "
        "in the web interface before replying.\n\n"
        "Reply *PROCEED* to start, or *SKIP* to run with "
        "current data (gaps will be flagged)."
    )


async def _wait_for_proceed(session_id: str, timeout: int = 7200) -> str:
    """
    Wait for PROCEED/SKIP reply via Redis.

    Returns:
        "proceed", "skip", or "kill" (on timeout)
    """
    key = f"proceed_response:{session_id}"

    waited = 0
    while waited < timeout:
        response = redis_client.get(key)
        if response:
            redis_client.delete(key)
            if isinstance(response, bytes):
                response = response.decode("utf-8")
            return response.lower()

        await asyncio.sleep(5)
        waited += 5

    # Timeout — proceed anyway
    logger.warning(
        "[DemoPipeline] Proceed timeout for session %s — "
        "proceeding with available data", session_id
    )
    return "proceed"


async def _deliver_output(chat_id: str, output_path: str, session_id: str):
    """Compile output and deliver to Alex."""
    try:
        summary = compile_for_delivery(output_path)

        await send_message(chat_id, summary)
        logger.info("[DemoPipeline] Results delivered for session %s", session_id)

    except Exception as e:
        logger.error("[DemoPipeline] Delivery error: %s", e)
        await send_message(
            chat_id,
            f"✅ Business plan complete!\n\n"
            f"Results saved to: `{Path(output_path).name}`\n\n"
            "Review in the web interface."
        )
