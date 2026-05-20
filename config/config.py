"""
Centralized configuration for the multi-agent system.
"""

# L1 Clarity Agent Settings
MAX_QUESTIONS = 3

# Gemini AI Settings
GEMINI_MODEL = "gemini-2.5-flash"  # Latest and fastest
GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"  # Stable fallback

# Retry Settings
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 5

# Session States
STATE_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
STATE_AWAITING_APPROVAL = "AWAITING_APPROVAL"
STATE_COMPLETED = "COMPLETED"

# Agent IDs
AGENT_L0_SESSION = "L0_SESSION_AGENT"
AGENT_L1_CLARITY = "L1_CLARITY_AGENT"
AGENT_L3_FEEDBACK = "L3_FEEDBACK_AGENT"
