"""
Shared question-detection gate for chat-driven workspaces.

Several workspaces have menu-driven command dispatch (type "a"/"b"/"c" for a
quick action) that falls through to "treat free text as workspace-specific
input" when nothing matches a menu key — a section ID for Build
(build_handler.build_section), a claim to challenge for Challenge
(challenge_handler.challenge_claim), and so on. If Alex types a plain
question instead of a command or an ID, that fallback silently misfires.

Observed in production: typing "can you tell me where we are in opportunity
part" while in Build got normalized into a section ID and produced "Building
section BP.can you tell me where we are in opportunity part." Feed mode had
this exact bug already (see web/handlers/feed_handler.py's question-detection
section) and was fixed there first; this module generalizes the same
detection so Build/Validate/Challenge/Export can use it too instead of each
re-implementing (or forgetting to implement) their own version.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

QUESTION_LEAD_PATTERN = re.compile(
    r"^\s*(who|what|when|where|why|how|which)\b"
    r"|^\s*(can|could|would|should|do|does|did|is|are|will)\s+(i|you|we|it|this|there|these)\b"
    r"|^\s*(show|tell|list|find|give|help)\s+me\b",
    re.IGNORECASE,
)

# Phrases that signal Alex is asking for an action a DIFFERENT workspace
# handles — these get an honest "I can't do that here" redirect instead of
# an attempted Q&A answer, since a RAG lookup would just come back empty for
# something like "build the financial section".
ACTION_REDIRECTS = [
    (re.compile(r"\bbuild\b.*\bsection\b|\bgenerate the plan\b|\bcompile the plan\b|\bwrite the section\b", re.IGNORECASE), "Build"),
    (re.compile(r"\bexport\b|\bdownload the plan\b|\bgenerate a (pdf|docx|word doc)\b", re.IGNORECASE), "Export"),
    (re.compile(r"\bvalidate\b|\bvalidation queue\b|\bcheck( the)? assumptions\b", re.IGNORECASE), "Validate"),
    (re.compile(r"\bchallenge\b|\bdevil'?s advocate\b|\bstress[- ]test\b", re.IGNORECASE), "Challenge"),
    (re.compile(r"\bfeed\b.*\bdata\b|\badd (a |this )?fact\b|\bupload\b", re.IGNORECASE), "Feed"),
]


def looks_like_question(text: str) -> bool:
    """Heuristic: does this read as a question/request rather than a
    command, section ID, or claim that should be handled literally?

    Args:
        text: Alex's raw message.

    Returns:
        True if this looks like a question rather than workspace-specific
        input (a section ID, a claim to challenge, etc).
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    return bool(QUESTION_LEAD_PATTERN.match(stripped))


def handle_workspace_question(
    text: str, current_workspace: str, session_id: Optional[str] = None
) -> str:
    """Answer a question typed into a workspace's free-text fallback instead
    of letting it fall through to that workspace's literal-input handler.

    Tries Inspect's RAG-backed Q&A first — a lot of questions asked while
    sitting in Build/Validate/Challenge/Export are legitimate lookups
    ("where are we on the opportunity section?"). If that comes back empty,
    or the question is really an action request for a different workspace,
    this says so plainly instead of pretending the current workspace can
    help.

    Args:
        text: Alex's message (already detected as question-shaped).
        current_workspace: Display name of the workspace Alex is in
            (e.g. "Build", "Validate", "Challenge", "Export").
        session_id: Current session ID, for live trace narration.

    Returns:
        Response text: either a direct answer, an honest "can't do that
        here" redirect, or a "nothing found" message.
    """
    for pattern, workspace_name in ACTION_REDIRECTS:
        if workspace_name == current_workspace:
            continue
        if pattern.search(text):
            return (
                f"I can't do that from {current_workspace} — I don't have that feature here. "
                f"That sounds like a {workspace_name} task; switch workspaces (the + menu, top left) and ask there."
            )

    try:
        from web.handlers.inspect_handler import answer_inspect_question

        result = answer_inspect_question(text, session_id=session_id)
        answer = result.get("answer", "") or ""
        sources = result.get("sources", [])

        if not sources or answer.startswith("No relevant data found"):
            return (
                "I don't have anything in the knowledge base to answer that. "
                "Try rephrasing, or ask in Inspect for a more thorough lookup."
            )

        return answer
    except Exception as e:
        logger.error(
            "[QuestionGate] Error answering question in %s mode: %s",
            current_workspace,
            e,
        )
        return "I couldn't look that up right now. Try again, or ask in Inspect."
