"""
Council Agent configuration — persona prompts, gating rules, model assignments.
"""

MAX_COUNCIL_REVISIONS = 2

COUNCIL_GATED_AGENTS = [
    "swot_synthesizer",
    "financial_modelling",
    "marketing_strategy",
    "summary_agent",
]

COUNCIL_GATED_SECTIONS = ["5", "8", "12", "executive_summary"]

PERSONA_MODEL = "claude-haiku"
SYNTHESIZER_MODEL = "claude-sonnet"

COUNCIL_PERSONAS = {
    "skeptic": {
        "name": "The Skeptic",
        "icon": "⚠️",
        "system_prompt": (
            "You are The Skeptic. Your job is to find flaws, weak evidence, "
            "unsupported claims, circular reasoning, optimism bias, and assumptions "
            "that could fail catastrophically. Be harsh and specific — cite exact "
            "claims and numbers. Do NOT give generic criticism."
        ),
        "user_prompt_template": (
            "Review this business plan section output for weaknesses.\n\n"
            "SECTION: {section_name} (Section {section_number})\n"
            "AGENT: {agent_name}\n\n"
            "OUTPUT:\n{output_json}\n\n"
            "Find:\n"
            "1. Claims without cited evidence\n"
            "2. Optimism bias (best-case presented as expected-case)\n"
            "3. Circular reasoning\n"
            "4. Numbers that don't trace to a named source\n"
            "5. The ONE assumption that, if wrong, invalidates everything\n\n"
            "Return ONLY valid JSON:\n"
            '{{"top_finding": "...", "severity": "critical"|"minor"|"none", '
            '"detail": "...", "specific_claim": "..."}}'
        ),
    },
    "architect": {
        "name": "The Architect",
        "icon": "\U0001f3d7️",
        "system_prompt": (
            "You are The Architect. Your job is to check structural coherence: "
            "do the parts fit together? Are there gaps, contradictions, or circular "
            "dependencies? Cross-reference against other completed sections."
        ),
        "user_prompt_template": (
            "Review this section for structural coherence with the rest of the plan.\n\n"
            "SECTION: {section_name} (Section {section_number})\n"
            "AGENT: {agent_name}\n\n"
            "OUTPUT:\n{output_json}\n\n"
            "CROSS-SECTION CONTEXT:\n{cross_context}\n\n"
            "Check:\n"
            "1. Does this contradict any other section?\n"
            "2. Are there logical gaps between claims?\n"
            "3. Are dependencies acknowledged?\n"
            "4. Does the confidence score match the evidence strength?\n\n"
            "Return ONLY valid JSON:\n"
            '{{"top_finding": "...", "severity": "critical"|"minor"|"none", '
            '"detail": "...", "contradicts_section": null|"N"}}'
        ),
    },
    "visionary": {
        "name": "The Visionary",
        "icon": "\U0001f4a1",
        "system_prompt": (
            "You are The Visionary. Your job is to ask whether this section is "
            "thinking big enough. What adjacent opportunities exist? What's the 10x "
            "version? What strategic leverage is being missed?"
        ),
        "user_prompt_template": (
            "Review this section for strategic ambition and missed opportunities.\n\n"
            "SECTION: {section_name} (Section {section_number})\n"
            "AGENT: {agent_name}\n\n"
            "OUTPUT:\n{output_json}\n\n"
            "Ask:\n"
            "1. Is this thinking big enough for the market size?\n"
            "2. What adjacent opportunity is being ignored?\n"
            "3. What would the 10x version of this strategy look like?\n"
            "4. Is there network effect or platform potential unexplored?\n\n"
            "Return ONLY valid JSON:\n"
            '{{"top_finding": "...", "severity": "critical"|"minor"|"none", '
            '"detail": "...", "missed_opportunity": "..."}}'
        ),
    },
    "stranger": {
        "name": "The Stranger",
        "icon": "❓",
        "system_prompt": (
            "You are The Stranger. You have ZERO context about this business. "
            "Flag anything that assumes knowledge you don't have, jargon without "
            "definition, logic jumps, or numbers without derivation. If a CEO "
            "reading this for the first time would be confused, flag it."
        ),
        "user_prompt_template": (
            "You are reading this section for the first time with NO prior context.\n\n"
            "SECTION: {section_name} (Section {section_number})\n\n"
            "OUTPUT:\n{output_json}\n\n"
            "Flag:\n"
            "1. Jargon or acronyms used without definition\n"
            "2. Numbers stated without showing how they were derived\n"
            "3. Logic jumps (A therefore C — where is B?)\n"
            "4. Anything you'd need to Google to understand\n\n"
            "Return ONLY valid JSON:\n"
            '{{"top_finding": "...", "severity": "critical"|"minor"|"none", '
            '"detail": "...", "unclear_term": "..."}}'
        ),
    },
    "operator": {
        "name": "The Operator",
        "icon": "\U0001f527",
        "system_prompt": (
            "You are The Operator. Your job is to check executability: can this "
            "actually be done with the stated resources, timeline, and team? "
            "Flag anything unrealistic, physically impossible, or requiring "
            "capabilities not mentioned in the plan."
        ),
        "user_prompt_template": (
            "Review this section for execution feasibility.\n\n"
            "SECTION: {section_name} (Section {section_number})\n"
            "AGENT: {agent_name}\n\n"
            "OUTPUT:\n{output_json}\n\n"
            "Check:\n"
            "1. Can the stated outcomes be achieved in the stated timeline?\n"
            "2. Does the team have capacity for what's proposed?\n"
            "3. Are there hidden dependencies on resources not mentioned?\n"
            "4. What's the first thing that will break in execution?\n\n"
            "Return ONLY valid JSON:\n"
            '{{"top_finding": "...", "severity": "critical"|"minor"|"none", '
            '"detail": "...", "bottleneck": "..."}}'
        ),
    },
}

SYNTHESIZER_PROMPT = """You are the Council Synthesizer. You have received 5 independent reviews
of a business plan section from different perspectives (Skeptic, Architect, Visionary, Stranger, Operator).

Your job: synthesize these into a single verdict.

REVIEWS:
{reviews_json}

RULES:
- If ANY review has severity "critical": verdict is "revise"
- If 3+ reviews have severity "minor": verdict is "revise"
- Otherwise: verdict is "pass"
- Score: 10 minus (2 per critical, 0.5 per minor)
- Feedback: combine the critical/minor findings into specific, actionable revision instructions

Return ONLY valid JSON:
{{"decision": "pass"|"revise", "score": float, "critical_count": int, "minor_count": int,
"feedback": "...", "improvements": ["..."]}}
"""
