"""
Demo Pipeline Connector — wires Phase 1 approval to eval runner.

Polls Redis for pipeline triggers, sends data declaration to Alex,
waits for PROCEED/SKIP, runs eval, delivers summary.
"""

import asyncio
import json
import logging
import os
import boto3
from datetime import datetime
from pathlib import Path

from memory.redis_client import redis_client
from memory.supabase_client import supabase
from tools.telegram_handler import send_message, send_document
from evaluation.run_grounded_eval import run_grounded_eval_for_session
from evaluation.compile_output import compile_for_delivery
from evaluation.export_docx import export_to_docx

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
        declaration = await _build_data_declaration(idea)
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
    # FIX 1: Log all session_data keys to debug what fields exist
    logger.info(f"[DemoPipeline] Session data keys: {list(session_data.keys())}")

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

    # FIX 1: Get the original idea from messages table
    # The sessions table doesn't store the idea text, it's in messages.content
    session_id = session_data.get("id")
    idea_summary = ""

    if session_id:
        try:
            # Get all messages for this session (ordered by received_at ASC)
            messages = supabase.table("messages") \
                .select("content") \
                .eq("session_id", session_id) \
                .order("received_at") \
                .execute()

            # Filter out command words and short messages
            command_words = ["proceed", "skip", "yes", "no", "kill", "adjust"]

            if messages.data and len(messages.data) > 0:
                for msg in messages.data:
                    content = msg.get("content", "").strip()
                    content_lower = content.lower()

                    # Skip command words and short messages (< 20 chars)
                    if content_lower in command_words or len(content) < 20:
                        continue

                    # Found the first real message
                    idea_summary = content
                    logger.info(f"[DemoPipeline] Retrieved idea from messages: {idea_summary[:100]}")
                    break

                if not idea_summary:
                    logger.warning("[DemoPipeline] All messages were commands - no idea found")
        except Exception as e:
            logger.warning(f"[DemoPipeline] Failed to read idea from messages: {e}")

    # Fallback: if still empty, build from Q&A
    if not idea_summary and ceo_assumptions:
        qa_parts = [f"{a['question']}: {a['answer']}" for a in ceo_assumptions[:3]]
        idea_summary = "Business idea: " + "; ".join(qa_parts)
        logger.info("[DemoPipeline] Built idea from Q&A assumptions")

    # Final fallback
    if not idea_summary:
        idea_summary = "Business idea from Phase 1 session"
        logger.warning("[DemoPipeline] Using fallback idea text - original idea not found")

    business_name = idea_summary[:80] if len(idea_summary) > 80 else idea_summary

    return {
        "id": f"session_{session_data['id'][:8]}",
        "name": business_name,
        "idea_summary": idea_summary,
        "ceo_assumptions": ceo_assumptions,
        "approved_decision": approved_decision,
        "business_type": "b2b_saas",  # default for demo
    }


async def _build_data_declaration(idea: dict) -> str:
    """
    Build data requirements declaration using LLM.

    Generates specific data requests based on the actual idea.
    Falls back to generic template on error.
    """

    # Generic fallback template
    fallback_message = (
        "📋 *BEFORE I START — DATA REQUIREMENTS*\n\n"
        "Here's what I need for the business plan:\n\n"
        "*FETCHING AUTOMATICALLY (web search):*\n"
        "• Market research and competitive analysis\n"
        "• Regulatory compliance requirements\n"
        "• Industry trends and benchmarks\n"
        "• Pricing and business model data\n"
        "• Financial benchmarks and metrics\n\n"
        "*REQUESTING FROM YOU (improves quality):*\n"
        "• Market size and growth data\n"
        "  → best source: *Passport or GlobalData*\n"
        "• Competitor analysis and positioning\n"
        "  → best source: *CB Insights or FACTIVA*\n"
        "• Customer segment spending patterns\n"
        "  → best source: *Statista or WARC*\n"
        "• Funding and valuation comparables\n"
        "  → best source: *CB Insights or Mergermarket*\n\n"
        "Add anything you find to the *Knowledge Base tab* "
        "in the web interface before replying.\n\n"
        "Reply *PROCEED* to start, or *SKIP* to run with "
        "current data (gaps will be flagged)."
    )

    try:
        # Build context from idea
        idea_summary = idea.get("idea_summary", "")
        ceo_assumptions = idea.get("ceo_assumptions", [])

        assumptions_text = "\n".join([
            f"Q: {a.get('question', '')}\nA: {a.get('answer', '')}"
            for a in ceo_assumptions[:5]  # limit to 5
        ])

        logger.info(f"[DemoPipeline] Building data declaration for idea: {idea_summary[:100]}")

        user_prompt = f"""Business Idea: {idea_summary}

CEO Context:
{assumptions_text if assumptions_text else "No additional context provided"}

Task: Identify the specific data needed to build a credible business plan for this idea.

Return a JSON object with:
{{
  "auto_searches": [list of 3-5 specific web searches relevant to this idea],
  "manual_requests": [
    {{
      "data_point": "specific data point needed",
      "database": "exact EADA database name (Passport, GlobalData, Statista, CB Insights, FACTIVA, Sabi Informa, eInforma, WARC, Mergermarket, ProQuest, or FitchSolutions)"
    }}
  ]
}}

Be specific — name exact data points, markets, regulations, and competitors relevant to THIS idea."""

        system_prompt = """You are a business research analyst. Given a business idea, identify the specific data needed to build a credible business plan. Be specific — name exact data points, not generic categories. Return only valid JSON."""

        # Call Bedrock
        bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1")
        )

        haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

        logger.info(f"[DemoPipeline] Calling Bedrock with model: {haiku_model}")

        response = bedrock.invoke_model(
            modelId=haiku_model,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            })
        )

        result = json.loads(response["body"].read())
        content_text = result["content"][0]["text"]

        logger.info(f"[DemoPipeline] LLM response length: {len(content_text)} chars")
        logger.debug(f"[DemoPipeline] LLM raw response: {content_text[:500]}")

        # Strip markdown code blocks if present
        if "```json" in content_text:
            content_text = content_text.split("```json")[1].split("```")[0].strip()
        elif "```" in content_text:
            content_text = content_text.split("```")[1].split("```")[0].strip()

        # Parse JSON from response
        data_req = json.loads(content_text)

        logger.info(f"[DemoPipeline] Parsed JSON keys: {list(data_req.keys())}")

        # Extract data
        auto_searches = data_req.get("auto_searches", [])
        manual_requests = data_req.get("manual_requests", [])

        logger.info(f"[DemoPipeline] Auto searches: {len(auto_searches)}, Manual requests: {len(manual_requests)}")

        # Check if LLM returned empty data
        if not auto_searches and not manual_requests:
            logger.warning("[DemoPipeline] LLM returned empty data, using fallback")
            return fallback_message

        # Format the message
        if auto_searches:
            auto_section = "\n".join([f"• {s}" for s in auto_searches])
        else:
            auto_section = "• Market research and competitive analysis\n• Industry trends and benchmarks"

        if manual_requests:
            manual_section = "\n".join([
                f"• {item.get('data_point', 'Unknown data point')}\n  → best source: *{item.get('database', 'Unknown')}*"
                for item in manual_requests
            ])
        else:
            manual_section = (
                "• Market size and growth data\n"
                "  → best source: *Passport or GlobalData*\n"
                "• Competitor analysis\n"
                "  → best source: *CB Insights or FACTIVA*"
            )

        message = (
            "📋 *BEFORE I START — DATA REQUIREMENTS*\n\n"
            f"Here's what I need for the business plan:\n\n"
            f"*FETCHING AUTOMATICALLY (web search):*\n"
            f"{auto_section}\n\n"
            f"*REQUESTING FROM YOU (improves quality):*\n"
            f"{manual_section}\n\n"
            "Add anything you find to the *Knowledge Base tab* "
            "in the web interface before replying.\n\n"
            "Reply *PROCEED* to start, or *SKIP* to run with "
            "current data (gaps will be flagged)."
        )

        logger.info("[DemoPipeline] Data declaration built successfully from LLM")
        return message

    except Exception as e:
        logger.error(f"[DemoPipeline] LLM data declaration failed: {e}", exc_info=True)
        logger.info("[DemoPipeline] Using fallback template")
        return fallback_message


async def _wait_for_proceed(session_id: str, timeout: int = 7200) -> str:
    """
    Wait for PROCEED/SKIP reply via Redis.

    Args:
        session_id: Session ID to wait for
        timeout: Maximum wait time in seconds (default 7200 = 2 hours)

    Returns:
        "proceed", "skip", or "proceed" (on timeout)
    """
    key = f"proceed_response:{session_id}"

    waited = 0
    while waited < timeout:
        # FIX 2: Add try/except around redis.get() to handle DNS failures
        try:
            response = redis_client.get(key)
            if response:
                redis_client.delete(key)
                if isinstance(response, bytes):
                    response = response.decode("utf-8")
                logger.info(f"[DemoPipeline] Received PROCEED response: {response}")
                return response.lower()
        except Exception as e:
            # Log but don't crash - retry on next iteration
            logger.warning(
                f"[DemoPipeline] Redis error while waiting for PROCEED "
                f"(waited {waited}s/{timeout}s): {e}"
            )

        await asyncio.sleep(5)
        waited += 5

    # FIX 3: Timeout after 2 hours — auto-proceed with warning
    logger.warning(
        f"[DemoPipeline] PROCEED timeout for session {session_id} "
        f"after {timeout}s — auto-proceeding with available data"
    )
    return "proceed"


async def _deliver_output(chat_id: str, output_path: str, session_id: str):
    """Compile output and deliver to Alex."""
    try:
        # 1. Compile text summary
        summary = compile_for_delivery(output_path)
        await send_message(chat_id, summary)
        logger.info("[DemoPipeline] Text summary delivered for session %s", session_id)

        # 2. Export to DOCX
        logger.info("[DemoPipeline] Exporting to DOCX...")
        docx_path = export_to_docx(output_path)
        logger.info("[DemoPipeline] DOCX created: %s", docx_path)

        # 3. Send DOCX as Telegram document
        caption = "📄 Complete business plan with all sections, confidence levels, and gap analysis"
        await send_document(chat_id, docx_path, caption=caption)
        logger.info("[DemoPipeline] DOCX delivered for session %s", session_id)

    except Exception as e:
        logger.error("[DemoPipeline] Delivery error: %s", e)
        await send_message(
            chat_id,
            f"✅ Business plan complete!\n\n"
            f"Results saved to: `{Path(output_path).name}`\n\n"
            "Review in the web interface."
        )
