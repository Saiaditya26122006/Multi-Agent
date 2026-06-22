"""
Router Agent
Classifies incoming messages and handles non-pipeline conversations.
Sits between L0 (auth) and L1 (clarity).

Uses a two-tier approach:
1. Fast-path: regex/keyword matching for obvious cases (zero latency)
2. LLM classification: only for ambiguous messages
"""

import os
import re
import sys
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from agents.phase1.llm_client import get_client

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from memory.supabase_client import get_ceo_context
from memory.redis_client import redis_client
from config import GEMINI_MODEL, MAX_RETRIES, RETRY_WAIT_SECONDS
from utils.retry import retry_with_fallback
from tools.trace_emitter import emit_trace

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger(__name__)


def _safe_redis_get(key: str):
    try:
        return redis_client.get(key)
    except Exception as e:
        logger.warning(f"[ROUTER] Redis unavailable on GET '{key}', falling back: {e}")
        return None


def _safe_redis_set(key: str, value, ex: int = None):
    try:
        if ex:
            redis_client.set(key, value, ex=ex)
        else:
            redis_client.set(key, value)
    except Exception as e:
        logger.warning(f"[ROUTER] Redis unavailable on SET '{key}', skipping: {e}")

# LLM client now uses Claude via Bedrock

# ============================================================================
# FAST-PATH PATTERNS (no API call needed)
# ============================================================================

GREETING_PATTERNS = {
    "hi", "hey", "hello", "yo", "sup", "hola", "howdy",
    "good morning", "good evening", "good afternoon", "good night",
    "gm", "morning", "evening",
}

THANKS_PATTERNS = {
    "thanks", "thank you", "thx", "ty", "cheers", "appreciated",
    "thanks!", "thank you!", "great thanks",
}

FAREWELL_PATTERNS = {
    "bye", "goodbye", "see you", "later", "gotta go", "ttyl", "cya",
}

CASUAL_PATTERNS = {
    "how are you", "how's it going", "what's up", "whats up",
    "how are things", "how do you do", "what are you up to",
    "how's your day", "hows your day",
}

COMMAND_PATTERNS = {
    "/reset", "continue", "continue from here", "keep going",
    "something new", "new", "start fresh", "new topic",
}

QUERY_KEYWORDS = [
    "what did we", "what was", "show me", "status", "pending",
    "last time", "previous", "history", "what's happening",
    "what are we working", "where are we", "recap", "summary",
    "what decisions", "what assumptions",
]


def _fast_classify(text_lower: str, session_state: str) -> Optional[str]:
    """
    Rule-based fast classification. Returns None if ambiguous (needs LLM).
    """
    if text_lower in COMMAND_PATTERNS:
        return "command"

    if session_state == "AWAITING_APPROVAL" and text_lower in ["yes", "adjust", "kill"]:
        return "command"

    if text_lower in GREETING_PATTERNS or text_lower.rstrip("!?. ") in GREETING_PATTERNS:
        return "general"

    if text_lower in THANKS_PATTERNS or text_lower.rstrip("!?. ") in THANKS_PATTERNS:
        return "general"

    if text_lower in FAREWELL_PATTERNS or text_lower.rstrip("!?. ") in FAREWELL_PATTERNS:
        return "general"

    for pattern in CASUAL_PATTERNS:
        if text_lower.startswith(pattern):
            return "general"

    for keyword in QUERY_KEYWORDS:
        if keyword in text_lower:
            return "query"

    return None


# ============================================================================
# LLM CLASSIFICATION (only for ambiguous messages)
# ============================================================================

def classify_message(
    message_text: str,
    session_state: str = "UNKNOWN",
    last_question_asked: Optional[str] = None,
) -> str:
    """
    Classify a message into an intent category.

    Fast-path handles obvious cases. LLM handles ambiguous ones.

    Args:
        message_text: The CEO's message
        session_state: Current session state
        last_question_asked: The last clarifying question sent to CEO (if any)

    Returns:
        One of: "general", "business_idea", "query", "command"
    """
    text_lower = message_text.lower().strip()

    ceo_ctx = get_ceo_context()
    session_key = str(ceo_ctx.get("telegram_chat_id")) if ceo_ctx else ""

    emit_trace(session_key, "Router", "fast_path_check", "Checking fast-path rules")
    fast_result = _fast_classify(text_lower, session_state)
    if fast_result:
        emit_trace(session_key, "Router", "routed", f"Routed as: {fast_result}", {"method": "fast_path", "category": fast_result})
        logger.info(f"[ROUTER] Fast-path: '{message_text[:30]}' → {fast_result}")
        return fast_result

    # Short messages (1-2 words) that aren't commands are likely general
    word_count = len(text_lower.split())
    if word_count <= 2 and len(text_lower) < 15:
        emit_trace(session_key, "Router", "routed", "Routed as: general", {"method": "short_message", "category": "general"})
        logger.info(f"[ROUTER] Short message default: '{message_text}' → general")
        return "general"

    # Build context-aware prompt
    context_lines = [f"Session state: {session_state}"]
    if last_question_asked:
        context_lines.append(f"Last question the system asked: \"{last_question_asked}\"")

    context = "\n".join(context_lines)

    system_prompt = f"""You classify messages from a startup CEO into exactly one category.

CATEGORIES:
- general: casual chat, greetings, off-topic, jokes, personal questions, opinions not related to business planning
- business_idea: new idea, strategy, answering a business question, providing details about a plan, market info, product thoughts
- query: asking about status, past decisions, what happened before, requesting information from the system
- command: system commands like reset, continue, start fresh

CONTEXT:
{context}

KEY RULE: If the system just asked a business question and the CEO's reply provides information (even short like "SaaS" or "B2B" or "India"), that's business_idea — they're answering the question.

Respond with ONLY the category name. One word. Nothing else."""

    prompt = f'CEO says: "{message_text}"'

    @retry_with_fallback(max_retries=2, wait_seconds=3)
    def call_llm():
        client = get_client()
        response = client.generate_content(
            prompt=prompt,
            system_instruction=system_prompt
        )
        raw = (response or "").strip().lower().replace('"', '').replace("'", "")
        # Extract just the category word
        for cat in ["general", "business_idea", "query", "command"]:
            if cat in raw:
                return cat
        return raw

    emit_trace(session_key, "Router", "llm_classification", "Classifying with LLM...")
    try:
        category = call_llm()
        valid = {"general", "business_idea", "query", "command"}
        if category not in valid:
            logger.warning(f"[ROUTER] LLM returned '{category}', defaulting to business_idea")
            category = "business_idea"
        emit_trace(session_key, "Router", "routed", f"Routed as: {category}", {"method": "llm", "category": category})
        logger.info(f"[ROUTER] LLM classified: '{message_text[:30]}' → {category}")
        return category
    except Exception as e:
        logger.error(f"[ROUTER] Classification failed: {e}")
        emit_trace(session_key, "Router", "routed", "Routed as: business_idea", {"method": "fallback", "category": "business_idea"})
        return "business_idea"


# ============================================================================
# GENERAL CHAT HANDLER
# ============================================================================

def handle_general_chat(
    message_text: str,
    chat_id: int,
    ceo_name: str = "there",
) -> str:
    """
    Generate a natural conversational reply for casual messages.

    Maintains a short conversation buffer in Redis so replies feel continuous.
    """
    history_key = f"chat_history:{chat_id}"
    raw_history = _safe_redis_get(history_key)

    conversation_history = ""
    if raw_history:
        if isinstance(raw_history, bytes):
            raw_history = raw_history.decode("utf-8")
        conversation_history = raw_history

    ceo_context = get_ceo_context()
    company = ceo_context.get("company", "the company") if ceo_context else "the company"

    system_prompt = f"""You are {ceo_name}'s AI business assistant. You're smart, warm, and concise.

WHO YOU ARE:
- A strategic thinking partner for {ceo_name}, CEO of {company}
- You help structure ideas, track decisions, and keep things moving
- Right now you're having casual conversation (not in work mode)

HOW TO RESPOND:
- 1-2 sentences. Never more than 3.
- Natural and human. Not robotic, not corporate.
- If {ceo_name} says hi → greet back warmly, maybe ask what's on their mind today
- If they ask how you are → brief friendly answer, pivot to them
- If they seem to be shifting to business → say something like "Got an idea? I'm ready to dig in."
- Match their tone. Short input = short output.
- Never say "As an AI" or "I'm just a language model" — you're their assistant, act like one
- Use their name occasionally but not every message"""

    prompt_parts = []
    if conversation_history:
        prompt_parts.append(f"Conversation so far:\n{conversation_history}\n---")
    prompt_parts.append(f"{ceo_name}: {message_text}")
    prompt_parts.append("You:")

    full_prompt = "\n".join(prompt_parts)

    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_gemini():
        client = get_client()
        response = client.generate_content(
            prompt=full_prompt,
            system_instruction=system_prompt
        )
        return response.strip()

    reply = call_llm()

    # Update conversation buffer (keep last 10 exchanges)
    new_entry = f"{ceo_name}: {message_text}\nYou: {reply}"
    if conversation_history:
        updated = conversation_history + "\n" + new_entry
    else:
        updated = new_entry

    lines = updated.strip().split("\n")
    if len(lines) > 20:
        lines = lines[-20:]

    _safe_redis_set(history_key, "\n".join(lines), ex=7200)

    return reply


# ============================================================================
# QUERY HANDLER
# ============================================================================

def handle_query(
    message_text: str,
    ceo_id: str,
    chat_id: int,
) -> str:
    """
    Answer status/history queries by reading Supabase and summarizing.
    """
    from memory.supabase_client import (
        get_pending_decisions,
        get_unresolved_assumptions,
        get_open_business_plan_sections,
        get_recent_sessions,
    )

    pending_decisions = get_pending_decisions()
    unresolved_assumptions = get_unresolved_assumptions()
    open_sections = get_open_business_plan_sections()
    recent_sessions = get_recent_sessions(ceo_id, limit=3)

    context_parts = []

    if pending_decisions:
        context_parts.append("PENDING DECISIONS:")
        for d in pending_decisions[:5]:
            context_parts.append(f"  - {d.get('decision')}")

    if unresolved_assumptions:
        context_parts.append("OPEN ASSUMPTIONS:")
        for a in unresolved_assumptions[:5]:
            context_parts.append(f"  - {a.get('statement')} [{a.get('confidence')}]")

    if open_sections:
        context_parts.append("BUSINESS PLAN SECTIONS:")
        for s in open_sections[:5]:
            context_parts.append(f"  - {s.get('section_name')} ({s.get('status')})")

    if recent_sessions:
        context_parts.append("RECENT SESSIONS:")
        for sess in recent_sessions:
            state = sess.get("state", "unknown")
            decisions = sess.get("decisions", [])
            dec_text = ""
            if decisions:
                dec_text = " | Decisions: " + ", ".join(
                    d.get("decision", "")[:40] for d in decisions[:2]
                )
            context_parts.append(f"  - [{state}]{dec_text}")

    if not context_parts:
        return "Nothing in progress right now. Send me an idea whenever you're ready."

    context = "\n".join(context_parts)

    system_prompt = """You are a CEO's business assistant answering a status question.
Rules:
- Be direct and concise. Bullet points preferred.
- 2-4 sentences max.
- If there's nothing relevant to their question, say so plainly.
- Don't add advice unless asked."""

    prompt = f"""CEO asks: "{message_text}"

Current state:
{context}

Answer:"""

    @retry_with_fallback(max_retries=MAX_RETRIES, wait_seconds=RETRY_WAIT_SECONDS)
    def call_llm():
        client = get_client()
        response = client.generate_content(
            prompt=prompt,
            system_instruction=system_prompt
        )
        return response.strip()

    return call_llm()
