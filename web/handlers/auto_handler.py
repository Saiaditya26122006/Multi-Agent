"""
AUTO Workspace Handler — intent classification and routing.

When Alex hasn't picked a specific workspace, this handler classifies
what he wants and either handles it directly or suggests a workspace switch.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

INTENT_PATTERNS = {
    "decision": [
        (r"^(yes|no|approve|reject|kill|adjust)\s*$", 0.9),
        (r"^(yes|approve|kill|adjust)[.,!]?\s*", 0.8),
    ],
    "command": [
        (r"^(run|build|generate|create|export|start|execute)", 0.8),
        (r"^(show me|give me|produce|make)", 0.8),
    ],
    "question": [
        (r"\?$", 0.9),
        (r"^(what|where|when|why|how|who|which|is there|are there|do we|does)", 0.8),
        (r"^(tell me|explain|describe)", 0.8),
        (r"(what are|what is|what's|show me|tell me|can you tell)", 0.8),
        (r"(how many|how much)", 0.8),
        (r"(?:^|\s)(current|status|progress)\b", 0.7),
    ],
    "correction": [
        (r"^(actually|no[, ]|wrong|incorrect|that's not right|fix|change|update)", 0.8),
        (r"(instead of|not .+ but|should be|was wrong)", 0.8),
    ],
    "feedback": [
        (r"^(good|bad|better|worse|like|dislike|prefer|don't like)", 0.8),
        (r"(too .+|not enough|needs more|should have)", 0.8),
    ],
    "new_data": [],
}

WORKSPACE_MAP = {
    "new_data": "feed",
    "correction": "feed",
    "command": "build",
    "question": "inspect",
    "feedback": "validate",
    "decision": None,
}


def classify_intent(message: str) -> dict:
    """Classify the intent of Alex's message.

    Args:
        message: The raw message text.

    Returns:
        Dict with: intent, confidence, suggested_workspace.
    """
    text = message.strip().lower()

    if any(char in text for char in ["|", "\t"]) or text.count(",") > 3:
        return {
            "intent": "new_data",
            "confidence": 0.7,
            "suggested_workspace": "feed",
            "reasoning": "Structured data detected (separators/commas)",
        }

    if len(text) > 100:
        return {
            "intent": "new_data",
            "confidence": 0.6,
            "suggested_workspace": "feed",
            "reasoning": "Long text input — likely raw data",
        }

    for intent, patterns in INTENT_PATTERNS.items():
        for pattern, conf in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "intent": intent,
                    "confidence": conf,
                    "suggested_workspace": WORKSPACE_MAP.get(intent),
                    "reasoning": f"Matched pattern for '{intent}'",
                }

    return {
        "intent": "ambiguous",
        "confidence": 0.3,
        "suggested_workspace": None,
        "reasoning": "No clear pattern matched",
    }


def handle_auto_message(message: str, session_id: Optional[str] = None) -> dict:
    """Handle a message in AUTO mode — classify and route.

    Args:
        message: The raw message text.
        session_id: Current session ID.

    Returns:
        Dict with: action, intent, response_text, suggested_workspace.
    """
    classification = classify_intent(message)
    intent = classification["intent"]
    confidence = classification["confidence"]
    suggested_ws = classification.get("suggested_workspace")

    if intent == "question":
        return _handle_question(message, classification)

    if intent == "decision":
        return _handle_decision(message, classification)

    if confidence < 0.6 or intent == "ambiguous":
        return _handle_question(message, classification)

    return {
        "action": "suggest_workspace",
        "intent": intent,
        "confidence": confidence,
        "response_text": _generate_workspace_suggestion(intent, suggested_ws),
        "suggested_workspace": suggested_ws,
    }


def _handle_question(message: str, classification: dict) -> dict:
    """Handle a question directly using RAG."""
    try:
        from services.rag_service import retrieve

        chunks = retrieve(
            query=message,
            top_k=5,
            threshold=0.35,
        )

        if chunks:
            answer_parts = []
            for chunk in chunks[:3]:
                status_tag = f"[{chunk.epistemic_status}]" if chunk.epistemic_status else ""
                answer_parts.append(f"  {status_tag} {chunk.content[:120]}")

            response = "Based on the knowledge base:\n\n" + "\n".join(answer_parts)
            response += "\n\nFor deeper analysis, switch to INSPECT."
        else:
            response = "No relevant data found in the knowledge base for that question. Try rephrasing, or switch to INSPECT for deeper analysis."

        return {
            "action": "direct_answer",
            "intent": "question",
            "confidence": classification["confidence"],
            "response_text": response,
            "suggested_workspace": "inspect",
        }
    except Exception as e:
        logger.error("[AutoHandler] Error handling question: %s", e)
        return {
            "action": "direct_answer",
            "intent": "question",
            "confidence": 0.5,
            "response_text": "Error retrieving data. Try switching to INSPECT workspace.",
            "suggested_workspace": "inspect",
        }


def _handle_decision(message: str, classification: dict) -> dict:
    """Handle a decision (Yes/Adjust/Kill) in the existing flow."""
    return {
        "action": "route_to_pipeline",
        "intent": "decision",
        "confidence": classification["confidence"],
        "response_text": None,
        "suggested_workspace": None,
    }


def _generate_clarification(message: str) -> str:
    """Generate a clarification question when intent is ambiguous."""
    return (
        "I'm not sure what you'd like to do with that. Are you:\n\n"
        "  [1] Giving me new information (FEED)\n"
        "  [2] Asking a question about the plan (INSPECT)\n"
        "  [3] Correcting something I have wrong (FEED)\n"
        "  [4] Giving a command to build/run something (BUILD)\n\n"
        "Type a number, or rephrase and I'll try again."
    )


def _generate_workspace_suggestion(intent: str, workspace: Optional[str]) -> str:
    """Generate a suggestion to switch workspaces."""
    suggestions = {
        "new_data": "This looks like new data. Switch to FEED (type '1') for the best experience — I'll parse it, tag it, and map it to your plan.",
        "correction": "This looks like a correction. Switch to FEED (type '1') and I'll update the existing knowledge.",
        "command": "That sounds like a build command. Switch to BUILD (type '2') and I'll show you what's ready to generate.",
        "feedback": "That sounds like feedback. Switch to VALIDATE (type '5') to formally record it against the assumption it relates to.",
    }
    return suggestions.get(intent, f"Consider switching to {workspace} for this.")


def format_auto_response(result: dict) -> str:
    """Format auto handler results as a chat message.

    Args:
        result: Output from handle_auto_message().

    Returns:
        Formatted string for chat.
    """
    response = result.get("response_text")
    if response:
        return response
    return "Message received. Processing through the pipeline."
