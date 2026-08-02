"""
Multi-Agent AI System - Main Pipeline
Wires together L0, L1, and L3 agents with the Web interface (web-only).
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
    _has_explicit_topic_change_trigger,
)

# Import reply handler (web interface)
from tools.reply_handler import send_reply, create_decision_keyboard, create_task_preview_keyboard

# Import router agent
from agents.phase1.router_agent import classify_message, handle_general_chat, handle_query

# Import message bus (replaces Redis)
from services.message_bus import message_bus

# Phase 2 task preview (for EPI-35 transparency at approval time)
import yaml

DEPENDENCY_MAP = yaml.safe_load(
    open(Path(__file__).parent / "config/phase2/dependency_map.yaml")
)
AGENT_ROSTER = yaml.safe_load(
    open(Path(__file__).parent / "config/phase2/agent_roster.yaml")
)


# ==========================================================================
# SESSION STATE HELPERS (using Supabase instead of Redis)
# ==========================================================================

def get_session_flag(session_id: str, flag_name: str, default=False):
    """Get a boolean flag from session state."""
    from memory.supabase_client import get_session_flag as db_get_flag
    value = db_get_flag(session_id, flag_name)
    return value if value is not None else default


def set_session_flag(session_id: str, flag_name: str, value: bool) -> bool:
    """Set a boolean flag on session state."""
    from memory.supabase_client import set_session_flag as db_set_flag
    return db_set_flag(session_id, flag_name, value)


def get_session_data(session_id: str, data_key: str, default=None):
    """Get data from session state."""
    from memory.supabase_client import get_session_data as db_get_data
    value = db_get_data(session_id, data_key)
    return value if value is not None else default


def set_session_data(session_id: str, data_key: str, value) -> bool:
    """Set data on session state."""
    from memory.supabase_client import set_session_data as db_set_data
    return db_set_data(session_id, data_key, value)


def clear_session_flags(session_id: str) -> bool:
    """Clear all temporary flags on session."""
    from memory.supabase_client import clear_session_flags as db_clear_flags
    return db_clear_flags(session_id)


def generate_preview_tasks(session_id: str) -> list:
    """
    Generate a preview of Group 1 tasks for Alex to see at approval time.
    Uses the same logic as MotherAgent._generate_group_tasks() but runs
    synchronously in the main handler before the async pipeline starts.
    """
    try:
        # Read phase1 data the same way MotherAgent does
        session_resp = supabase.table("sessions").select("*").eq("id", session_id).execute()
        if not session_resp.data:
            return []

        messages_resp = supabase.table("messages").select("content").eq(
            "session_id", session_id
        ).order("received_at", desc=False).execute()

        assumptions_resp = supabase.table("assumptions").select("*").eq(
            "session_id", session_id
        ).execute()

        # Build minimal phase1_data for task generation
        idea_summary = ""
        if messages_resp.data:
            for msg in messages_resp.data:
                content = msg.get("content", "").strip()
                if content.lower() not in {"agree", "kill", "edit", "add"} and len(content) > 10:
                    idea_summary = content
                    break

        ceo_assumptions = []
        for a in (assumptions_resp.data or []):
            if a.get("ceo_answer"):
                ceo_assumptions.append({
                    "question": a.get("question_asked", ""),
                    "answer": a.get("ceo_answer", ""),
                })

        phase1_data = {
            "idea_summary": idea_summary,
            "ceo_assumptions": ceo_assumptions,
            "business_type": session_resp.data[0].get("business_type", "saas"),
            "market_scope": idea_summary,
        }

        # Generate Group 1 tasks using dependency map + roster
        group_config = AGENT_ROSTER.get("execution_groups", {}).get(1, {})
        group_agents = group_config.get("agents", [])
        # For preview, include all sections owned by Group 1 agents
        group1_owned_sections = set()
        for ag in group_agents:
            ac = AGENT_ROSTER.get("agents", {}).get(ag, {})
            for s in ac.get("sections_owned", []):
                group1_owned_sections.add(str(s))
        applicable_sections = list(group1_owned_sections)

        tasks = []
        for agent_name in group_agents:
            agent_config = AGENT_ROSTER.get("agents", {}).get(agent_name, {})
            for section_num in agent_config.get("sections_owned", []):
                if str(section_num) not in applicable_sections:
                    continue
                section_config = DEPENDENCY_MAP.get("sections", {}).get(str(section_num), {})
                required_inputs = section_config.get("required_inputs", [])

                # Classify execution type
                sources = {inp.get("source", "") for inp in required_inputs}
                if "ceo_answer" in sources:
                    exec_type = "human_interview"
                elif "external_data" in sources or "web_search" in sources:
                    exec_type = "data_retrieval"
                else:
                    exec_type = "agent_executable"

                # Extract data source
                data_source = None
                for inp in required_inputs:
                    if inp.get("database"):
                        data_source = inp["database"]
                        break

                depends_on = section_config.get("depends_on", [])

                tasks.append({
                    "task_id": f"S{section_num}-G1",
                    "task_name": f"Build section {section_num}: {section_config.get('name', '')}",
                    "bp_section": str(section_num),
                    "execution_type": exec_type,
                    "data_source": data_source,
                    "depends_on": depends_on,
                    "dependency_reasoning": section_config.get("dependency_reasoning"),
                    "human_brief": section_config.get("human_brief"),
                    "required_inputs": required_inputs,
                    "idea_summary": phase1_data.get("idea_summary", ""),
                })

        return tasks
    except Exception as e:
        logger.error("[PREVIEW] Failed to generate task preview: %s", e)
        return []


def format_task_preview(tasks: list) -> str:
    """Format preview tasks into a readable web message."""
    if not tasks:
        return ""

    idea_summary = tasks[0].get("idea_summary", "") if tasks else ""
    if idea_summary:
        truncated = idea_summary[:120] + ("..." if len(idea_summary) > 120 else "")
        lines = [
            f"Building business plan for: {truncated}",
            "",
            "━━━ GROUP 1: Foundation ━━━",
            "",
        ]
    else:
        lines = [
            "━━━ GROUP 1: Foundation ━━━",
            "",
        ]
    for task in tasks:
        task_id = task.get("task_id", "?")
        bp_section = task.get("bp_section", "?")
        exec_type = task.get("execution_type", "agent_executable")
        data_source = task.get("data_source")
        depends_on = task.get("depends_on", [])
        dep_reasoning = task.get("dependency_reasoning")
        req_inputs = task.get("required_inputs", [])

        lines.append(f"[{task_id}] {task['task_name']}")
        lines.append(f"  BP Section: §{bp_section}")
        lines.append(f"  Type: {exec_type}")

        if req_inputs:
            data_fields = [inp.get("field", "") for inp in req_inputs[:3]]
            source_label = data_source if data_source else "prior sections / Phase 1 memory"
            lines.append(f"  Data needed: {', '.join(data_fields)}")
            lines.append(f"  Best source: {source_label}")

        human_brief = task.get("human_brief")
        if exec_type == "human_interview" and human_brief:
            lines.append(f"  How to collect: {human_brief}")

        if depends_on:
            dep_labels = [f"§{d}" for d in depends_on]
            lines.append(f"  Blocked by: {', '.join(dep_labels)}")
            if dep_reasoning:
                lines.append(f"  Why: {dep_reasoning}")
        else:
            lines.append("  Blocked by: nothing (root task)")

        lines.append("")

    # Plain-text fallback for web chat (no inline keyboard support)
    lines.append(
        "Actions (use the buttons, or reply with a command):\n"
        "  approve all / kill all\n"
        "  approve [task_id] / adjust [task_id] / "
        "kill [task_id] / challenge [task_id]"
    )
    return "\n".join(lines)


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

    # Optional flags are echoed rather than required. USE_HYBRID_RETRIEVAL is
    # read straight from the classifier so a misspelled variable shows up here
    # as `false` instead of hiding as a silent dense-only run.
    from services.feed_classifier_v3 import hybrid_enabled

    print(f"\n[STARTUP] USE_HYBRID_RETRIEVAL = {str(hybrid_enabled()).lower()}")

    print("\n[STARTUP] Verifying database connection...")
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("✗ Failed to connect to Supabase or no CEO context found")
        return False

    print(f"  ✓ Connected to Supabase")
    print(f"  ✓ CEO: {ceo_context.get('name')} at {ceo_context.get('company')}")
    print(f"  ✓ Chat ID: {ceo_context.get('chat_id')}")

    print("\n[STARTUP] System status: ✅ READY")
    print("=" * 60 + "\n")
    return True


async def handle_message(message_data):
    """
    Main message handler - processes every incoming web message through the pipeline.

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

    ceo_chat_id = ceo_context.get("chat_id")
    if ceo_chat_id != chat_id:
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

    # Topic change is now detected via session state machine, not Redis
    # get_active_session() already handles topic-change detection internally
    # No notification needed here

    # ========================================================================
    # STEP 1.4: Handle pending task challenge/adjust responses
    # ========================================================================
    challenge_task_id = get_session_data(session_id, "challenge_pending_task_id")
    if challenge_task_id:
        set_session_data(session_id, "challenge_pending_task_id", None)
        logger.info(
            "[TASK] Challenge received for %s: %s", challenge_task_id, text
        )
        await send_reply(
            chat_id,
            f"⚡ Challenge noted for task {challenge_task_id}.\n\n"
            f"Your reasoning: \"{text}\"\n\n"
            "I'll flag this dependency for review before execution."
        )
        return

    adjust_task_id = get_session_data(session_id, "adjust_pending_task_id")
    if adjust_task_id:
        set_session_data(session_id, "adjust_pending_task_id", None)
        logger.info(
            "[TASK] Adjustment received for %s: %s", adjust_task_id, text
        )
        await send_reply(
            chat_id,
            f"🔧 Adjustment noted for task {adjust_task_id}.\n\n"
            f"Your change: \"{text}\"\n\n"
            "I'll apply this before executing the task."
        )
        return

    # Get current session state
    session = get_active_session(chat_id)
    current_state = session.get("state") if session else "UNKNOWN"
    print(f"[SESSION] Current state: {current_state}")

    # ========================================================================
    # STEP 1.5: Drop Gate 2 commands if no active Gate 2 listener waiting
    # ========================================================================
    text_lower = text.lower().strip()

    # Check session state for active Gate 2 listener before processing agree/kill
    if text_lower in ("agree", "kill"):
        gate2_active = get_session_flag(session_id, "gate2_active", False)
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
    # STEP 1.55: Handle DELIVER/CANCEL response for final delivery gate
    # ========================================================================
    if text_lower in ("deliver", "cancel"):
        # Check if there's a pending delivery in this session
        pending_run_id = get_session_data(session_id, "final_delivery_pending")
        if pending_run_id:
            set_session_data(session_id, f"final_delivery_response_{pending_run_id}", text_lower)
            ack = (
                "Delivering the final plan now."
                if text_lower == "deliver"
                else "Plan delivery held. You can review sections and say 'deliver' when ready."
            )
            await send_reply(chat_id, ack)
            print(f"[DELIVERY_GATE] ✓ Response '{text_lower}' stored for run {run_id}")
            return

    # ========================================================================
    # STEP 1.6: Handle PROCEED/SKIP response for demo pipeline
    # ========================================================================
    if text_lower in ["proceed", "skip"]:
        print(f"\n[PROCEED] Processing demo pipeline response: {text}")
        # Find the most recent completed session
        completed = supabase.table("sessions") \
            .select("id") \
            .eq("chat_id", chat_id) \
            .eq("state", "COMPLETED") \
            .order("started_at", desc=True) \
            .limit(1) \
            .execute()

        if completed.data:
            proceed_session_id = completed.data[0]["id"]
        else:
            proceed_session_id = session_id  # fallback

        set_session_data(proceed_session_id, "proceed_response", text_lower)
        await send_reply(
            chat_id,
            f"✓ Got it. {'Starting' if text_lower == 'proceed' else 'Running with available data'}..."
        )
        print(f"[PROCEED] ✓ Response stored for session {proceed_session_id}")
        return

    # ========================================================================
    # STEP 1.7: Route clarification responses back to Mother Agent
    # ========================================================================
    clarification_data = get_session_data(session_id, "clarification_data")
    if clarification_data:
        try:
            task_id = clarification_data.get("task_id")
            run_id = clarification_data.get("run_id")

            print(f"[CLARIFICATION] Routing CEO response to Mother Agent for task {task_id}")

            # Store clarification answer for Mother Agent
            set_session_data(
                session_id,
                "clarification_response",
                {"answer": text, "task_id": task_id, "run_id": run_id}
            )

            # Clear the awaiting data
            set_session_data(session_id, "clarification_data", None)

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

    # Session-state gate: during active L1 Q&A, skip the router entirely.
    # All messages are answers to L1 UNLESS the CEO explicitly triggers a topic change.
    skip_router = (
        current_state == "NEEDS_CLARIFICATION"
        and not _has_explicit_topic_change_trigger(text)
    )

    if skip_router:
        logger.info("[ROUTER] NEEDS_CLARIFICATION — skipping router, routing directly to L1")
        print(f"\n[ROUTER] ✓ Intent: clarification_response (session gate — skipped classification)")
        intent = "business_idea"
    else:
        if current_state == "NEEDS_CLARIFICATION":
            logger.info("[ROUTER] NEEDS_CLARIFICATION but explicit topic-change detected — routing normally")

        # Get last question asked (for router context)
        last_question = get_session_data(session_id, "last_question")

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

        welcome_already_sent = get_session_flag(session_id, "welcome_sent", False)

        if (
            not is_mid_conversation
            and not welcome_already_sent
            and should_send_welcome_back(chat_id)
        ):
            print("\n[MEMORY] Generating welcome back message...")

            welcome_msg = generate_welcome_back(ceo_id, chat_id)

            if welcome_msg:
                await send_reply(chat_id, welcome_msg)
                set_session_flag(session_id, "welcome_sent", True)
                print(f"[MEMORY] ✓ Sent welcome back message")
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

        # Clear last question from session state
        set_session_data(session_id, "last_question", None)

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

            # Generate Group 1 task preview BEFORE sending confirmation
            print("[PHASE2] Generating task preview for Group 1...")
            preview_tasks = generate_preview_tasks(session_id)
            task_preview_msg = format_task_preview(preview_tasks)
            print(f"[PHASE2] ✓ Generated preview with {len(preview_tasks)} tasks")

            # Trigger Phase 2 pipeline via message bus
            print("[PHASE2] Triggering Phase 2 pipeline...")
            await message_bus.send(
                sender="main",
                recipient="mother_agent",
                payload={"trigger": "full_pipeline", "session_id": session_id},
                session_id=session_id
            )
            print(f"[PHASE2] ✓ Pipeline trigger sent for session {session_id}")

            # Send confirmation with task preview + inline keyboard
            confirmation = "✅ Decision approved! Building the full business plan.\n\n"
            if task_preview_msg:
                confirmation += task_preview_msg
                keyboard = create_task_preview_keyboard(preview_tasks)
                await send_reply(chat_id, confirmation, reply_markup=keyboard)
            else:
                confirmation += (
                    "Phase 2 pipeline starting — "
                    "you'll receive Group 1 tasks for review shortly."
                )
                await send_reply(chat_id, confirmation)
            print("[DECISION] ✓ Approved and Phase 2 triggered")

        elif text_lower == "adjust":
            # Request adjustment
            print("[DECISION] CEO wants adjustments...")

            # Supersede old decision, increment version
            update_decision_status(decision_id, "superseded")

            # Clear old assumptions so L1 gets a fresh 3-question budget
            from memory.supabase_client import clear_session_assumptions
            clear_session_assumptions(session_id)

            # Clear the last question from session state
            set_session_data(session_id, "last_question", None)

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

            reply_message = l3_result.get("reply_message")

            if reply_message:
                # Send message with inline keyboard buttons
                keyboard = create_decision_keyboard()
                await send_reply(chat_id, reply_message, reply_markup=keyboard)
                print("[L3] ✓ Feedback sent to CEO with inline keyboard")
                print(f"[L3] Decision ID: {l3_result.get('decision_id')}")
            else:
                await send_reply(
                    chat_id,
                    "⚠️ Error generating feedback. Please try again."
                )
                print("[L3] ✗ No reply message generated")

            print("[PIPELINE] ✓ L1 → L3 transition complete")
            print("=" * 60 + "\n")
            return

        # Normal L1 flow - send question to CEO
        question = l1_result["question"]
        assumption_id = l1_result["assumption_id"]

        print(f"[L1] ✓ Question generated")
        print(f"[L1] Assumption: {assumption_id}")

        # Store last question so router knows what to expect next
        set_session_data(session_id, "last_question", question)

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


async def handle_callback(callback_data):
    """Handle button callbacks from the web interface.

    Handles:
      - decision_yes / decision_adjust / decision_kill (L3 approval)
      - task_approve_{id} / task_adjust_{id} / task_kill_{id} / task_challenge_{id}
      - group_approve / group_kill

    Args:
        callback_data: Dict with chat_id, data, from_user
    """
    chat_id = callback_data.get("chat_id")
    data = callback_data.get("data", "")

    print("\n" + "=" * 60)
    print(f"[CALLBACK] Received: {data} from chat {chat_id}")
    print("=" * 60)

    # Handle task-level and group-level callbacks (post-approval, no state gate).
    # NOTE: These are intent-logging only — they acknowledge the CEO's choice but
    # do not modify pipeline state. Wire to Mother Agent execution control when
    # Mother Agent runs end-to-end.
    if data.startswith("task_") or data.startswith("group_"):
        if data.startswith("task_challenge_"):
            task_id = data.replace("task_challenge_", "")
            set_session_data(session_id, "challenge_pending_task_id", task_id)
            await send_reply(
                chat_id,
                f"⚡ Challenging task {task_id}.\n\n"
                "Which dependency are you challenging? "
                "Reply with your reasoning."
            )
            logger.info("[TASK] Challenge pending for %s from chat %s", task_id, chat_id)

        elif data.startswith("task_approve_"):
            # Intent-logging only — no execution control yet
            task_id = data.replace("task_approve_", "")
            logger.info("[TASK] Task %s approved by CEO", task_id)
            await send_reply(chat_id, f"✅ Task {task_id} approved.")

        elif data.startswith("task_adjust_"):
            task_id = data.replace("task_adjust_", "")
            set_session_data(session_id, "adjust_pending_task_id", task_id)
            await send_reply(
                chat_id,
                f"🔧 Adjusting task {task_id}.\n\n"
                "What would you like to adjust on this task?"
            )
            logger.info("[TASK] Adjust pending for %s from chat %s", task_id, chat_id)

        elif data.startswith("task_kill_"):
            # Intent-logging only — no execution control yet
            task_id = data.replace("task_kill_", "")
            logger.info("[TASK] Task %s killed by CEO", task_id)
            await send_reply(chat_id, f"❌ Task {task_id} removed from this group.")

        elif data == "group_approve":
            # Intent-logging only — no execution control yet
            logger.info("[TASK] Group approved by CEO from chat %s", chat_id)
            await send_reply(chat_id, "✅ All tasks approved. Pipeline proceeding.")

        elif data == "group_kill":
            # Intent-logging only — no execution control yet
            logger.info("[TASK] Group killed by CEO from chat %s", chat_id)
            await send_reply(
                chat_id,
                "🛑 Group killed. Pipeline paused.\n\n"
                "Send a new message when you're ready."
            )

        print("[CALLBACK] ✓ Task callback processed")
        print("=" * 60 + "\n")
        return

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

        # Generate Group 1 task preview
        preview_tasks = generate_preview_tasks(session_id)
        task_preview_msg = format_task_preview(preview_tasks)

        # Trigger Phase 2 pipeline via message bus
        print("[PHASE2] Triggering Phase 2 pipeline...")
        await message_bus.send(
            sender="main",
            recipient="mother_agent",
            payload={"trigger": "full_pipeline", "session_id": session_id},
            session_id=session_id
        )
        print(f"[PHASE2] ✓ Pipeline trigger sent for session {session_id}")

        confirmation = "✅ Decision approved! Building the full business plan.\n\n"
        if task_preview_msg:
            confirmation += task_preview_msg
            keyboard = create_task_preview_keyboard(preview_tasks)
            await send_reply(chat_id, confirmation, reply_markup=keyboard)
        else:
            confirmation += (
                "Phase 2 pipeline starting — "
                "you'll receive Group 1 tasks for review shortly."
            )
            await send_reply(chat_id, confirmation)
        print("[DECISION] ✓ Approved and Phase 2 triggered")

    elif data == "decision_adjust":
        print("[DECISION] CEO wants adjustments...")

        # Supersede old decision
        update_decision_status(decision_id, "superseded")

        # Clear assumptions so L1 gets fresh question budget
        from memory.supabase_client import clear_session_assumptions
        clear_session_assumptions(session_id)

        # Clear last question from session state
        set_session_data(session_id, "last_question", None)

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
    """Start the FastAPI web server."""
    import uvicorn
    from web.server import app, set_pipeline_handler

    set_pipeline_handler(handle_message)

    web_port = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
    logger.info("[WEB] Starting web server on port %d", web_port)
    uvicorn.run(app, host="0.0.0.0", port=web_port, log_level="info")


def main():
    """Main entry point — web interface only."""
    print_banner()

    if not verify_system():
        print("✗ System verification failed. Exiting.")
        return

    web_port = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
    print(f"[SYSTEM] Web interface starting at http://localhost:{web_port}")
    print("=" * 60)
    print("PIPELINE ACTIVE — Web interface only")
    print("=" * 60 + "\n")

    try:
        start_web_server()
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
