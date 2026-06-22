"""
Multi-Agent AI System - Main Pipeline
Wires together L0, L1, and L3 agents with Telegram + Web integration.
"""

import os
import asyncio
import threading
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Import agents
from agents.phase1.l0_input_guard import validate_message
from agents.phase1.l1_clarity_agent import generate_clarifying_question
from agents.phase1.l3_feedback_agent import generate_feedback
from agents.phase1.memory_agent import (
    consolidate_session_memory,
    generate_welcome_back,
    should_send_welcome_back
)

# Import memory functions
from memory.supabase_client import (
    supabase,
    get_ceo_context,
    get_active_session,
    update_session_state,
    get_decisions_for_session,
    update_decision_status,
    update_business_plan_section_status,
    create_session,
)
from memory.redis_client import redis_client

# Import Telegram handler
from tools.telegram_handler import start_polling, create_decision_keyboard

# Import unified reply handler (replaces direct send_message calls)
from tools.reply_handler import send_reply

# Import router agent
from agents.phase1.router_agent import classify_message, handle_general_chat, handle_query


# ==========================================================================
# REDIS RESILIENCE HELPERS
# ==========================================================================

def safe_redis_get(key: str, fallback=None):
    """Redis GET with graceful fallback on connection errors."""
    try:
        return redis_client.get(key)
    except Exception as e:
        logger.warning(f"[REDIS] Unavailable on GET '{key}', falling back to DB state: {e}")
        return fallback


def safe_redis_set(key: str, value, ex: int = None) -> bool:
    """Redis SET with graceful fallback — returns False on failure."""
    try:
        if ex:
            redis_client.set(key, value, ex=ex)
        else:
            redis_client.set(key, value)
        return True
    except Exception as e:
        logger.warning(f"[REDIS] Unavailable on SET '{key}', skipping cache write: {e}")
        return False


def safe_redis_delete(key: str) -> bool:
    """Redis DELETE with graceful fallback — returns False on failure."""
    try:
        redis_client.delete(key)
        return True
    except Exception as e:
        logger.warning(f"[REDIS] Unavailable on DELETE '{key}', skipping: {e}")
        return False


def print_banner():
    """Print system startup banner"""
    print("\n" + "=" * 60)
    print("    MULTI-AGENT AI SYSTEM")
    print("    CEO Business Planning Assistant")
    print("=" * 60)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")


def verify_system():
    """Verify system configuration and database connection"""
    print("[STARTUP] Verifying environment variables...")

    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BEDROCK_REGION",
        "CLAUDE_HAIKU_MODEL",
        "CLAUDE_SONNET_MODEL",
        "TAVILY_API_KEY",
        "TELEGRAM_BOT_TOKEN",
    ]

    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
        else:
            print(f"  ✓ {var}")

    if missing:
        print(f"\n✗ Missing environment variables: {', '.join(missing)}")
        print("  Please check your .env file")
        return False

    print("\n[STARTUP] Verifying database connection...")
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ Failed to connect to Supabase or no CEO context found")
        return False

    print(f"  ✓ Connected to Supabase")
    print(f"  ✓ CEO: {ceo_context.get('name')} at {ceo_context.get('company')}")
    print(f"  ✓ Chat ID: {ceo_context.get('telegram_chat_id')}")

    print("\n[STARTUP] System status: ✅ READY")
    print("=" * 60 + "\n")
    return True


async def handle_telegram_message(message_data):
    """
    Main message handler - processes every incoming Telegram message through the pipeline.

    Pipeline flow:
    1. Drop unknown senders silently
    2. L0: Validate message (auth, duplicate check)
    3. L1: Generate clarifying question
    4. L3: Generate feedback and decision (when research is done)
    5. Handle CEO decision (Yes/Adjust/Kill)
    """

    message_id = message_data.get("message_id")
    chat_id = message_data.get("chat_id")
    text = message_data.get("text", "").strip()

    print("\n" + "=" * 60)
    print(f"[INCOMING] Message {message_id} from chat {chat_id}")
    print(f"[TEXT] {text}")
    print("=" * 60)

    # ========================================================================
    # STEP 0: SILENT DROP - Reject unknown chat IDs immediately
    # ========================================================================
    ceo_context = get_ceo_context()
    if not ceo_context:
        print("[GUARD] ✗ No CEO context configured - dropping message")
        return

    ceo_telegram_chat_id = ceo_context.get("telegram_chat_id")
    if ceo_telegram_chat_id != chat_id:
        print(f"[GUARD] ✗ Unknown sender {chat_id} - silently dropped")
        return

    # ========================================================================
    # STEP 1: L0 INPUT GUARD - Validate message
    # ========================================================================
    print("\n[L0] Validating message...")

    l0_result = validate_message(message_data)

    if not l0_result["valid"]:
        print(f"[L0] ✗ Message rejected: {l0_result['reason']}")
        await send_reply(
            chat_id,
            f"❌ {l0_result['reason']}"
        )
        print("[PIPELINE] Stopped at L0 (invalid message)\n")
        return

    print(f"[L0] ✓ Message valid")
    print(f"[L0] Session: {l0_result['session_id']}")
    print(f"[L0] New session: {l0_result['is_new_session']}")

    session_id = l0_result["session_id"]
    ceo_id = l0_result["ceo_id"]

    # Check for topic-change notification (set by get_active_session)
    topic_notify_key = f"topic_change_notify:{chat_id}"
    old_idea = safe_redis_get(topic_notify_key)
    if old_idea:
        old_idea_str = old_idea.decode("utf-8") if isinstance(old_idea, bytes) else old_idea
        await send_reply(
            chat_id,
            f"Previous idea ({old_idea_str}) saved and paused — starting fresh on this one.",
        )
        safe_redis_delete(topic_notify_key)
        logger.info("[L0] Topic change notification sent for '%s'", old_idea_str)

    # Get current session state
    session = get_active_session(chat_id)
    current_state = session.get("state") if session else "UNKNOWN"
    print(f"[SESSION] Current state: {current_state}")

    # ========================================================================
    # STEP 1.5: Drop Gate 2 commands if no active Gate 2 listener waiting
    # ========================================================================
    text_lower = text.lower().strip()

    # TASK 3: Check Redis for active Gate 2 listener before processing agree/kill
    if text_lower in ("agree", "kill"):
        gate2_active = safe_redis_get(f"gate2_active_group:{chat_id}")
        if gate2_active:
            # Gate 2 listener will handle this — drop silently
            print(f"[GUARD] ✓ Gate 2 command '{text_lower}' will be handled by listener — dropping from main pipeline")
            return
        elif current_state in (
            "PHASE2_RUNNING",
            "PHASE2_AWAITING_GATE2",
            "PHASE2_GATE2_PENDING",
            "COMPLETED",
        ):
            # No active listener but session is in Phase 2 state — drop silently
            print(f"[GUARD] ✓ Stale Gate 2 command '{text_lower}' with no active listener — dropping")
            return

    # ========================================================================
    # STEP 1.6: Handle PROCEED/SKIP response for demo pipeline
    # ========================================================================
    if text_lower in ["proceed", "skip"]:
        print(f"\n[PROCEED] Processing demo pipeline response: {text}")
        # Find the most recent completed session
        completed = supabase.table("sessions") \
            .select("id") \
            .eq("telegram_chat_id", chat_id) \
            .eq("state", "COMPLETED") \
            .order("started_at", desc=True) \
            .limit(1) \
            .execute()
        
        if completed.data:
            proceed_session_id = completed.data[0]["id"]
        else:
            proceed_session_id = session_id  # fallback
        
        safe_redis_set(
            f"proceed_response:{proceed_session_id}",
            text_lower,
            ex=7200,
        )
        await send_reply(
            chat_id,
            f"✓ Got it. {'Starting' if text_lower == 'proceed' else 'Running with available data'}..."
        )
        print(f"[PROCEED] ✓ Response stored for session {proceed_session_id}")
        return

    # ========================================================================
    # STEP 1.7: Route clarification responses back to Mother Agent
    # ========================================================================
    # TASK 6: Check if we're awaiting a clarification response from CEO
    clarification_key = f"awaiting_clarification:{chat_id}"
    clarification_data = safe_redis_get(clarification_key)
    if clarification_data:
        try:
            import json as _json
            clarification = _json.loads(clarification_data.decode("utf-8") if isinstance(clarification_data, bytes) else clarification_data)
            task_id = clarification.get("task_id")
            run_id = clarification.get("run_id")

            print(f"[CLARIFICATION] Routing CEO response to Mother Agent for task {task_id}")

            # Store the clarification answer in Redis for Mother Agent to pick up
            safe_redis_set(
                f"clarification_response:{task_id}",
                _json.dumps({"answer": text, "task_id": task_id, "run_id": run_id}),
                ex=3600,
            )

            # Clear the awaiting key
            safe_redis_delete(clarification_key)

            await send_reply(
                chat_id,
                "✓ Got it. Passing your response to the agent and continuing..."
            )
            print("[CLARIFICATION] ✓ Response stored for Mother Agent")
            return
        except Exception as e:
            print(f"[CLARIFICATION] Error routing response: {e}")
            # Fall through to normal processing if routing fails

    # ========================================================================
    # STEP 2: ROUTER AGENT - Classify message intent
    # ========================================================================

    # Get last question asked (for router context)
    last_q_key = f"last_question:{chat_id}"
    last_question_raw = safe_redis_get(last_q_key)
    last_question = None
    if last_question_raw:
        last_question = last_question_raw.decode("utf-8") if isinstance(last_question_raw, bytes) else last_question_raw

    print("\n[ROUTER] Classifying message intent...")
    intent = classify_message(text, session_state=current_state, last_question_asked=last_question)
    print(f"[ROUTER] ✓ Intent: {intent}")

    # --- Handle GENERAL chat (greetings, casual, off-topic) ---
    if intent == "general":
        print("\n[GENERAL] Handling casual conversation...")
        ceo_name = ceo_context.get("name", "there")
        try:
            reply = handle_general_chat(text, chat_id, ceo_name=ceo_name)
            await send_reply(chat_id, reply)
            print(f"[GENERAL] ✓ Replied: {reply[:60]}...")
        except Exception as e:
            print(f"[GENERAL] ✗ Error: {e}")
            await send_reply(chat_id, "Hey! What's on your mind?")
        return

    # --- Handle QUERY (status checks, history questions) ---
    if intent == "query":
        print("\n[QUERY] Handling status/history query...")
        try:
            reply = handle_query(text, ceo_id, chat_id)
            await send_reply(chat_id, reply)
            print(f"[QUERY] ✓ Replied: {reply[:60]}...")
        except Exception as e:
            print(f"[QUERY] ✗ Error: {e}")
            await send_reply(chat_id, "Couldn't fetch that info right now. Try again?")
        return

    # --- Handle COMMAND (explicit commands bypass router) ---
    # Commands: /reset, continue, something new, yes/adjust/kill

    is_continue_response = text_lower in ["continue", "continue from here", "keep going"]
    is_new_response = text_lower in ["something new", "new", "start fresh", "new topic"]

    if not is_continue_response and not is_new_response:
        is_mid_conversation = current_state in ["NEEDS_CLARIFICATION", "AWAITING_APPROVAL"]

        welcome_key = f"welcome_sent:{chat_id}"
        welcome_already_sent = safe_redis_get(welcome_key) is not None

        if (
            not is_mid_conversation
            and not welcome_already_sent
            and should_send_welcome_back(chat_id)
        ):
            print("\n[MEMORY] Generating welcome back message...")

            welcome_msg = generate_welcome_back(ceo_id, chat_id)

            if welcome_msg:
                await send_reply(chat_id, welcome_msg)
                safe_redis_set(welcome_key, "1", ex=7200)
                print(f"[MEMORY] ✓ Sent welcome back message (locked for 2h)")
                return

    # Handle "continue" response
    if is_continue_response:
        print("\n[MEMORY] CEO wants to continue from last session")
        active_session = get_active_session(chat_id)

        if active_session:
            await send_reply(
                chat_id,
                "✓ Continuing from where we left off. What's your next thought?"
            )
            print("[MEMORY] ✓ Resuming active session")
        else:
            await send_reply(
                chat_id,
                "No active session to continue. Let's start fresh! What would you like to work on?"
            )
            print("[MEMORY] No active session found - will start fresh")
        return

    # Handle "something new" response
    if is_new_response:
        print("\n[MEMORY] CEO wants to start something new")

        # Close any active session
        active_session = get_active_session(chat_id)
        if active_session:
            old_session_id = active_session.get('id')
            update_session_state(old_session_id, "COMPLETED")
            print(f"[MEMORY] ✓ Closed session {old_session_id}")

            # Consolidate memory from completed session
            print(f"[MEMORY] Consolidating memory from session {old_session_id}...")
            memories = consolidate_session_memory(old_session_id, ceo_id)
            print(f"[MEMORY] ✓ Created {len(memories)} memories")

        await send_reply(
            chat_id,
            "✓ Starting fresh! What would you like to work on?"
        )
        print("[MEMORY] ✓ Ready for new session")
        return

    # ========================================================================
    # STEP 3: Handle /reset command
    # ========================================================================

    if text_lower == "/reset":
        print("\n[RESET] Processing reset command...")

        # Close current session and deactivate its assumptions
        if session:
            from memory.supabase_client import clear_session_assumptions
            clear_session_assumptions(session_id)
            update_session_state(session_id, "COMPLETED")
            print("[RESET] ✓ Session closed, assumptions deactivated")

        # Clear last question cache
        last_q_key = f"last_question:{chat_id}"
        safe_redis_delete(last_q_key)

        await send_reply(
            chat_id,
            "🔄 Session reset. Starting fresh.\n\n"
            "Send a new message when you're ready."
        )
        print("[RESET] ✓ Reset complete\n")
        return

    # ========================================================================
    # STEP 4: Check if this is a decision response (Yes/Adjust/Kill)
    # ========================================================================

    if text_lower in ["yes", "adjust", "kill"] and current_state == "AWAITING_APPROVAL":
        print(f"\n[DECISION] Processing CEO response: {text}")

        # Get the pending decision for this session
        decisions = get_decisions_for_session(session_id)
        pending_decisions = [d for d in decisions if d.get("status") == "pending_approval"]

        if not pending_decisions:
            await send_reply(chat_id, "⚠️ No pending decision found.")
            print("[DECISION] ✗ No pending decision found\n")
            return

        latest_decision = pending_decisions[0]
        decision_id = latest_decision.get("decision_id")

        print(f"[DECISION] Found decision: {decision_id}")

        if text_lower == "yes":
            # Approve decision
            print("[DECISION] CEO approved - updating decision and BP section...")

            update_decision_status(decision_id, "approved")

            # Update business plan sections to in_progress
            sections_affected = latest_decision.get("sections_affected", [])
            for section_id in sections_affected:
                update_business_plan_section_status(section_id, "in_progress")

            # Complete the session
            update_session_state(session_id, "COMPLETED")

            # Consolidate memory from completed session
            print("[DECISION] Consolidating session memory...")
            memories = consolidate_session_memory(session_id, ceo_id)
            print(f"[DECISION] ✓ Created {len(memories)} memories")

            # Trigger Phase 2 pipeline via Redis
            print("[PHASE2] Triggering Phase 2 pipeline...")
            safe_redis_set(
                f"pipeline_trigger:{session_id}",
                "full_pipeline",
                ex=3600,
            )
            print(f"[PHASE2] ✓ Pipeline trigger set for session {session_id}")

            await send_reply(
                chat_id,
                "✅ Decision approved! Moving forward with the plan.\n\n"
                "Phase 2 pipeline starting — I'll build the full business plan now.\n"
                "You'll receive Group 1 tasks for review shortly."
            )
            print("[DECISION] ✓ Approved and Phase 2 triggered")

        elif text_lower == "adjust":
            # Request adjustment
            print("[DECISION] CEO wants adjustments...")

            # Supersede old decision, increment version
            update_decision_status(decision_id, "superseded")

            # Clear old assumptions so L1 gets a fresh 3-question budget
            from memory.supabase_client import clear_session_assumptions
            clear_session_assumptions(session_id)

            # Clear the last question cache
            last_q_key = f"last_question:{chat_id}"
            safe_redis_delete(last_q_key)

            # Reset session to needs clarification
            update_session_state(session_id, "NEEDS_CLARIFICATION")

            await send_reply(
                chat_id,
                "🔄 Got it. Let's adjust the approach.\n\n"
                "What would you like to change?"
            )
            print("[DECISION] ✓ Reset for adjustment (fresh question budget)")

        elif text_lower == "kill":
            # Reject decision
            print("[DECISION] CEO rejected - killing initiative...")

            update_decision_status(decision_id, "rejected")
            update_session_state(session_id, "COMPLETED")

            # Consolidate memory even when rejected (learn what NOT to do)
            print("[DECISION] Consolidating session memory...")
            memories = consolidate_session_memory(session_id, ceo_id)
            print(f"[DECISION] ✓ Created {len(memories)} memories")

            await send_reply(
                chat_id,
                "🛑 Initiative stopped as requested.\n\n"
                "Session completed. Send a new message when you're ready."
            )
            print("[DECISION] ✓ Rejected and session completed")

        print("[PIPELINE] Completed decision handling\n")
        return

    # ========================================================================
    # STEP 5: Handle new idea when session is AWAITING_APPROVAL
    # ========================================================================
    if current_state == "AWAITING_APPROVAL":
        print("\n[NEW IDEA] Session was awaiting approval, but CEO has a new idea")
        print("[NEW IDEA] Resetting session to NEEDS_CLARIFICATION...")

        # Reset session state to allow new conversation
        update_session_state(session_id, "NEEDS_CLARIFICATION")
        print("[NEW IDEA] ✓ Session reset to NEEDS_CLARIFICATION")

        # Continue to L1 processing below
        # (Don't return - let the message flow through to L1)

    # ========================================================================
    # STEP 6: L1 CLARITY AGENT - Generate clarifying question
    # ========================================================================
    print("\n[L1] Generating clarifying question...")

    try:
        l1_result = generate_clarifying_question(
            session_id=session_id,
            ceo_id=ceo_id,
            message_text=text
        )

        # Check if clarification is complete (3 questions already asked)
        if l1_result.get("clarification_complete"):
            print("[L1] ✓ Clarification complete (3/3 questions answered)")
            print("[L1] ✓ Triggering L3 Feedback Agent...")

            # Trigger L3 to generate feedback
            from agents.phase1.l3_feedback_agent import generate_feedback

            print("\n[L3] Generating feedback based on clarification...")

            l3_result = generate_feedback(
                session_id=session_id,
                research_brief=None  # No research brief yet, L3 will work with assumptions
            )

            telegram_message = l3_result.get("telegram_message")

            if telegram_message:
                # Send message with inline keyboard buttons
                keyboard = create_decision_keyboard()
                await send_reply(chat_id, telegram_message, reply_markup=keyboard)
                print("[L3] ✓ Feedback sent to CEO with inline keyboard")
                print(f"[L3] Decision ID: {l3_result.get('decision_id')}")
            else:
                await send_reply(
                    chat_id,
                    "⚠️ Error generating feedback. Please try again."
                )
                print("[L3] ✗ No telegram message generated")

            print("[PIPELINE] ✓ L1 → L3 transition complete")
            print("=" * 60 + "\n")
            return

        # Normal L1 flow - send question to CEO
        question = l1_result["question"]
        assumption_id = l1_result["assumption_id"]

        print(f"[L1] ✓ Question generated")
        print(f"[L1] Assumption: {assumption_id}")

        # Store last question so router knows what to expect next
        last_q_key = f"last_question:{chat_id}"
        safe_redis_set(last_q_key, question, ex=86400)

        # Send clarifying question to CEO
        await send_reply(chat_id, question)
        print(f"[L1] ✓ Sent to CEO: {question[:80]}...")

    except Exception as e:
        print(f"[L1] ✗ Error: {e}")
        await send_reply(
            chat_id,
            "⚠️ Error processing your message. Please try again."
        )
        print("[PIPELINE] Error at L1\n")
        return

    # ========================================================================
    # STEP 7: Check if we should trigger L3 (when research is complete)
    # ========================================================================
    # For now, L3 is triggered manually or by L2 Research Agent
    # This is where you would check if research_briefs exist and trigger L3

    print("[PIPELINE] ✓ Message processed successfully")
    print("=" * 60 + "\n")


async def handle_telegram_callback(callback_data):
    """
    Handle inline keyboard button callbacks (Yes/Adjust/Kill buttons).

    Args:
        callback_data: Dict with callback_id, chat_id, message_id, data, from_user
    """
    chat_id = callback_data.get("chat_id")
    data = callback_data.get("data", "")

    print("\n" + "=" * 60)
    print(f"[CALLBACK] Received: {data} from chat {chat_id}")
    print("=" * 60)

    # Get current session
    session = get_active_session(chat_id)
    if not session:
        await send_reply(chat_id, "⚠️ No active session found.")
        print("[CALLBACK] ✗ No active session\n")
        return

    session_id = session.get("id")
    current_state = session.get("state")

    print(f"[SESSION] Session: {session_id}")
    print(f"[SESSION] State: {current_state}")

    # Only process decision callbacks when in AWAITING_APPROVAL state
    if current_state != "AWAITING_APPROVAL":
        await send_reply(chat_id, "⚠️ No pending decision to respond to.")
        print("[CALLBACK] ✗ Not in AWAITING_APPROVAL state\n")
        return

    # Get the pending decision
    decisions = get_decisions_for_session(session_id)
    pending_decisions = [d for d in decisions if d.get("status") == "pending_approval"]

    if not pending_decisions:
        await send_reply(chat_id, "⚠️ No pending decision found.")
        print("[CALLBACK] ✗ No pending decision found\n")
        return

    latest_decision = pending_decisions[0]
    decision_id = latest_decision.get("decision_id")

    print(f"[DECISION] Found decision: {decision_id}")

    # Process the callback
    if data == "decision_yes":
        print("[DECISION] CEO approved - updating decision and BP section...")

        update_decision_status(decision_id, "approved")

        # Update business plan sections to in_progress
        sections_affected = latest_decision.get("sections_affected", [])
        for section_id in sections_affected:
            update_business_plan_section_status(section_id, "in_progress")

        # Complete the session
        update_session_state(session_id, "COMPLETED")

        # Trigger Phase 2 pipeline via Redis
        print("[PHASE2] Triggering Phase 2 pipeline...")
        safe_redis_set(
            f"pipeline_trigger:{session_id}",
            "full_pipeline",
            ex=3600,
        )
        print(f"[PHASE2] ✓ Pipeline trigger set for session {session_id}")

        await send_reply(
            chat_id,
            "✅ Decision approved! Moving forward with the plan.\n\n"
            "Phase 2 pipeline starting — I'll build the full business plan now.\n"
            "You'll receive Group 1 tasks for review shortly."
        )
        print("[DECISION] ✓ Approved and Phase 2 triggered")

    elif data == "decision_adjust":
        print("[DECISION] CEO wants adjustments...")

        # Supersede old decision
        update_decision_status(decision_id, "superseded")

        # Clear assumptions so L1 gets fresh question budget
        from memory.supabase_client import clear_session_assumptions
        clear_session_assumptions(session_id)

        # Clear last question cache
        last_q_key = f"last_question:{chat_id}"
        safe_redis_delete(last_q_key)

        # Reset session
        update_session_state(session_id, "NEEDS_CLARIFICATION")

        await send_reply(
            chat_id,
            "🔄 Got it. Let's adjust the approach.\n\n"
            "What would you like to change?"
        )
        print("[DECISION] ✓ Reset for adjustment (fresh question budget)")

    elif data == "decision_kill":
        print("[DECISION] CEO rejected - killing initiative...")

        update_decision_status(decision_id, "rejected")
        update_session_state(session_id, "COMPLETED")

        await send_reply(
            chat_id,
            "🛑 Initiative stopped as requested.\n\n"
            "Session completed. Send a new message when you're ready."
        )
        print("[DECISION] ✓ Rejected and session completed")

    print("[CALLBACK] ✓ Callback processed successfully")
    print("=" * 60 + "\n")


def start_web_server():
    """Start the FastAPI web server in a separate thread."""
    import uvicorn
    from web.server import app, set_pipeline_handler

    set_pipeline_handler(handle_telegram_message)

    web_port = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
    logger.info(f"[WEB] Starting web server on port {web_port}")
    uvicorn.run(app, host="0.0.0.0", port=web_port, log_level="info")


def main():
    """Main entry point"""
    print_banner()

    # Verify system on startup
    if not verify_system():
        print("✗ System verification failed. Exiting.")
        return

    # Start web server in background thread
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    # Start demo pipeline poller in background thread
    def start_demo_poller_thread():
        """Run demo poller in its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        from evaluation.demo_pipeline import start_demo_poller
        loop.run_until_complete(start_demo_poller())

    poller_thread = threading.Thread(target=start_demo_poller_thread, daemon=True)
    poller_thread.start()

    web_port = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
    print(f"[SYSTEM] Web chat running at http://localhost:{web_port}")
    print("[SYSTEM] Demo pipeline poller started")
    print("[SYSTEM] Starting Telegram polling...")
    print("[SYSTEM] Waiting for messages...\n")
    print("=" * 60)
    print("PIPELINE ACTIVE - Use Telegram or Web chat")
    print("=" * 60 + "\n")

    try:
        # Start polling with our handlers (message + callback)
        start_polling(handle_telegram_message, handle_callback=handle_telegram_callback)
    except KeyboardInterrupt:
        print("\n\n[SYSTEM] Shutting down...")
        print("=" * 60)
        print("Goodbye!")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
