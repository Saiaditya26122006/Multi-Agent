"""
CEO Data Loader — reads Alex's EpistemicOS source-of-truth data files
and produces section-scoped, epistemic-tagged fact packages for agent injection.

Each fact carries its epistemic status (CONFIRMED, ASSUMPTION, INFERRED, CONTRADICTION)
as a first-class field. Empty source sections are explicit gaps, not empty strings.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CEO_DATA_DIR = Path(__file__).parent

MAX_SECTION_CHARS = 2900

SECTION_RELEVANCE = {
    "financials.json": ["10", "12", "13", "executive_summary"],
    "customers.json": ["1", "3", "5", "8"],
    "competitors.json": ["1", "3", "5"],
    "deck.txt": ["1", "executive_summary"],
    "constraints.json": ["1", "4", "10", "12", "13"],
    "market_research.json": ["3", "5", "8"],
    "team.json": ["4"],
    "buyers_icp.json": ["1", "4", "5", "8"],
    "value_proposition.json": ["1", "5", "8"],
    "product_definition.json": ["1", "4", "executive_summary"],
    "capabilities.json": ["1", "4", "10"],
}

STATUS_PRIORITY = {"CONFIRMED": 0, "CONTRADICTION": 1, "INFERRED": 2, "ASSUMPTION": 3}


def _strip_meta(obj):
    """Remove _meta fields from dicts (they are source metadata, not agent-useful)."""
    if isinstance(obj, dict):
        return {k: _strip_meta(v) for k, v in obj.items() if k != "_meta"}
    if isinstance(obj, list):
        return [_strip_meta(item) for item in obj]
    return obj


def _sort_facts_by_status(facts: list) -> list:
    """Sort fact lists so CONFIRMED comes first, ASSUMPTION last."""
    return sorted(
        facts,
        key=lambda f: STATUS_PRIORITY.get(
            f.get("status", "ASSUMPTION"), 3
        ),
    )


def load_all_ceo_data() -> dict:
    """Load all CEO-provided data files into a structured dict.

    Returns:
        {"financials": {...}, "customers": {...}, ...}
    """
    data = {}
    excluded = {"__init__.py", "loader.py"}
    for file_path in CEO_DATA_DIR.iterdir():
        if file_path.name.startswith("_") or file_path.name in excluded:
            continue
        if file_path.name.startswith("EpistemicOS"):
            continue
        if file_path.suffix not in (".json", ".txt", ".md"):
            continue

        key = file_path.stem
        try:
            content = file_path.read_text(encoding="utf-8")
            if file_path.suffix == ".json":
                data[key] = json.loads(content)
            else:
                data[key] = content
            logger.info("[CEOData] Loaded %s (%d chars)", file_path.name, len(content))
        except Exception as e:
            logger.warning("[CEOData] Failed to load %s: %s", file_path.name, e)

    return data


def _compact_topic(topic_data) -> dict:
    """Compact a topic's data for injection: strip meta, prioritize by status."""
    if isinstance(topic_data, str):
        return topic_data

    cleaned = _strip_meta(topic_data)

    if isinstance(cleaned, dict):
        for key, value in cleaned.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if "status" in value[0]:
                    cleaned[key] = _sort_facts_by_status(value)

    return cleaned


def get_relevant_ceo_data(
    section_number: str, all_ceo_data: Optional[dict] = None
) -> dict:
    """Get CEO data relevant to a specific section, compacted to fit injection budget.

    Args:
        section_number: The business plan section number (e.g., "8", "12")
        all_ceo_data: Pre-loaded data dict (loads fresh if None)

    Returns:
        Dict of relevant CEO-provided context for this section.
        Every fact carries its epistemic status. Empty sections are explicit gaps.
    """
    if all_ceo_data is None:
        all_ceo_data = load_all_ceo_data()

    if not all_ceo_data:
        return {}

    relevant = {}
    for filename, applicable_sections in SECTION_RELEVANCE.items():
        if str(section_number) in applicable_sections:
            key = Path(filename).stem
            if key in all_ceo_data:
                relevant[key] = _compact_topic(all_ceo_data[key])

    serialized = json.dumps(relevant, indent=2, default=str)
    if len(serialized) <= MAX_SECTION_CHARS:
        return relevant

    trimmed = {}
    for key, value in relevant.items():
        candidate = {**trimmed, key: value}
        candidate_size = len(json.dumps(candidate, indent=2, default=str))
        if candidate_size <= MAX_SECTION_CHARS:
            trimmed[key] = value
            continue

        if isinstance(value, dict) and value.get("gap"):
            gap_entry = {
                "status": "no_data",
                "gap": True,
                "gap_reason": value.get("gap_reason", "No data provided"),
            }
            trimmed[key] = gap_entry
            continue

        if isinstance(value, dict):
            compact = {}
            for subkey, subval in value.items():
                if isinstance(subval, list) and subval:
                    kept = []
                    for item in subval:
                        test = {**trimmed, key: {**compact, subkey: kept + [item]}}
                        if len(json.dumps(test, indent=2, default=str)) <= MAX_SECTION_CHARS:
                            kept.append(item)
                        else:
                            break
                    if kept:
                        compact[subkey] = kept
                elif subkey in ("status", "gap", "gap_reason"):
                    compact[subkey] = subval
                else:
                    test = {**trimmed, key: {**compact, subkey: subval}}
                    if len(json.dumps(test, indent=2, default=str)) <= MAX_SECTION_CHARS:
                        compact[subkey] = subval
            if compact:
                trimmed[key] = compact

        elif isinstance(value, str):
            test = {**trimmed, key: value}
            if len(json.dumps(test, indent=2, default=str)) <= MAX_SECTION_CHARS:
                trimmed[key] = value

    return trimmed
