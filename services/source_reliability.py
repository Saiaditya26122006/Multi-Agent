"""
Source Reliability — assesses evidence quality independently of content type
and node placement.

Answers the question: "How reliable is this source?" — separate from
"Where does it belong?" (classifier) and "How certain is it?" (epistemic_status).

Produces four fields per fact:
  - source_family: category of origin (ceo_direct, third_party_report, etc.)
  - evidence_tier: E0-E4 (simplified from Alex's E0-E7, pending full spec)
  - source_traceability: present / partial / missing
  - source_limitations: what this source cannot tell you
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# --- Source Family ---
# Classifies WHERE the evidence comes from, not what it says.

SOURCE_FAMILY_PATTERNS = {
    "ceo_direct": [
        r"(we decided|we chose|i decided|our decision|we will|we have)",
        r"(we signed|we confirmed|i confirmed|we agreed)",
    ],
    "first_party_data": [
        r"(our data shows|our metrics|our dashboard|our records show)",
        r"(signed contract|invoice|bank transfer|payment received)",
        r"(\d+ paying|pilot agreement|signed pilot)",
    ],
    "third_party_report": [
        r"(according to|report shows|annual report|study found)",
        r"(research by|published by|source:|cited in)",
        r"(gartner|mckinsey|forrester|statista|crunchbase)",
    ],
    "interview_transcript": [
        r"(said in interview|dean told us|from discovery call)",
        r"(in our conversation|they mentioned|feedback from)",
    ],
    "market_inference": [
        r"(market shows|industry trend|competitor analysis)",
        r"(comparable saas|benchmark|industry average)",
    ],
    "founder_interpretation": [
        r"(i think|i believe|my interpretation|in my view)",
        r"(it seems like|appears to be|my read is)",
    ],
    "system_generated": [
        r"(system calculated|auto-generated|pipeline output)",
        r"(model suggests|algorithm|computed from)",
    ],
}


def classify_source_family(text: str, input_source: str = "alex_direct") -> str:
    """Classify the source family of a fact based on textual signals.

    Args:
        text: The fact text.
        input_source: How it was submitted ("alex_direct", "document", etc.)

    Returns:
        Source family string.
    """
    text_lower = text.lower()

    for family, patterns in SOURCE_FAMILY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return family

    if input_source == "alex_direct":
        return "ceo_direct"
    if input_source == "document":
        return "document_extracted"

    return "unclassified"


# --- Evidence Tier ---
# Simplified hierarchy (E0-E4). Alex's full E0-E7 can be layered on top
# once he provides the complete specification.
#
# E0: No evidence — claim only
# E1: Single anecdotal source, founder interpretation
# E2: Multiple consistent signals OR single credible third-party
# E3: Direct first-party data (contracts, payments, signed agreements)
# E4: Replicated/validated evidence (multiple independent confirmations)

EVIDENCE_TIER_RULES = {
    "E4": [
        r"(independently verified|cross-verified|peer.?reviewed)",
        r"(replicated|validated by .+ and .+|multiple independent)",
    ],
    "E3": [
        r"(signed|contracted|paid|received payment|invoice)",
        r"(bank transfer|pilot agreement|binding agreement)",
        r"(\d+ paying|\d+ confirmed paying|\d+ signed)",
    ],
    "E2": [
        r"(according to .*(report|study|research|survey))",
        r"(data shows|evidence shows|findings indicate)",
        r"(multiple interviews|several sources|consistent feedback)",
    ],
    "E1": [
        r"(one .*(email|call|meeting|conversation))",
        r"(anecdotal|single source|one dean|one interview)",
        r"(i think|i believe|my interpretation|founder view)",
        r"(estimated|projected|forecast|expected to)",
        r"(target:|goal:|plan to reach|aim for|intend to)",
    ],
}


def classify_evidence_tier(text: str, source_family: str) -> str:
    """Classify the evidence tier of a fact.

    Args:
        text: The fact text.
        source_family: Already-classified source family.

    Returns:
        Evidence tier string (E0-E4).
    """
    text_lower = text.lower()

    for tier in ("E4", "E3", "E2", "E1"):
        for pattern in EVIDENCE_TIER_RULES[tier]:
            if re.search(pattern, text_lower):
                return tier

    if source_family == "first_party_data":
        return "E3"
    if source_family == "third_party_report":
        return "E2"
    if source_family in ("founder_interpretation", "system_generated", "market_inference"):
        return "E1"
    if source_family == "ceo_direct":
        return "E2"

    return "E0"


# --- Source Traceability ---
# Can the original source be found and verified?

TRACEABILITY_PRESENT_PATTERNS = [
    r"(according to .+\d{4})",  # Named source + year
    r"(annual report|published|cited|doi:|isbn:)",
    r"(contract (?:ref|number|id)|invoice \#)",
    r"(interview with .+ on|call on \d)",
    r"(source:|reference:|from .+ report)",
]

TRACEABILITY_PARTIAL_PATTERNS = [
    r"(research shows|studies show|data shows)",  # Generic "research" without specific source
    r"(industry average|benchmark|comparable)",
    r"(feedback from|conversations with)",
]


def assess_traceability(text: str) -> str:
    """Assess whether the source of a fact can be traced back.

    Returns:
        "present" — specific source is named and verifiable
        "partial" — category of source named but not specific
        "missing" — no source information at all
    """
    text_lower = text.lower()

    for pattern in TRACEABILITY_PRESENT_PATTERNS:
        if re.search(pattern, text_lower):
            return "present"

    for pattern in TRACEABILITY_PARTIAL_PATTERNS:
        if re.search(pattern, text_lower):
            return "partial"

    return "missing"


# --- Source Limitations ---
# What this source CANNOT tell you — prevents downstream misuse.

LIMITATION_RULES = {
    "founder_interpretation": "Founder interpretation — cannot be cited as market evidence or buyer validation",
    "market_inference": "Market inference from benchmarks — does not validate this specific product/market",
    "system_generated": "System-generated — requires human verification before claim use",
    "interview_transcript": "Single-source interview — cannot generalize to segment without additional sources",
    "ceo_direct": None,
    "first_party_data": None,
    "third_party_report": "Third-party data — may not apply to this specific market/geography/time period",
    "document_extracted": "Extracted from document — original context may be lost in extraction",
    "unclassified": "Source unclassified — treat as unverified until provenance established",
}


def assess_limitations(source_family: str, evidence_tier: str) -> Optional[str]:
    """Determine what limitations apply to this source.

    Returns:
        Human-readable limitation string, or None if no notable limitation.
    """
    base_limitation = LIMITATION_RULES.get(source_family)

    if evidence_tier == "E0":
        return "No evidence backing — claim only, cannot support any downstream assertion"
    if evidence_tier == "E1" and not base_limitation:
        return "Single/weak evidence — insufficient for high-stakes claims without corroboration"

    return base_limitation


def assess_source_reliability(
    text: str,
    input_source: str = "alex_direct",
) -> dict:
    """Full source reliability assessment for a single fact.

    Args:
        text: The fact text.
        input_source: How it was submitted.

    Returns:
        Dict with: source_family, evidence_tier, source_traceability,
        source_limitations.
    """
    source_family = classify_source_family(text, input_source)
    evidence_tier = classify_evidence_tier(text, source_family)
    traceability = assess_traceability(text)
    limitations = assess_limitations(source_family, evidence_tier)

    return {
        "source_family": source_family,
        "evidence_tier": evidence_tier,
        "source_traceability": traceability,
        "source_limitations": limitations,
    }
