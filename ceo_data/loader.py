"""
CEO Data Loader — reads Alex's provided documents and structures them
for injection into agent input packages.

This is the pre-RAG solution: simple file reading with section relevance mapping.
When the RAG knowledge base is built, this will be replaced with vector retrieval.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CEO_DATA_DIR = Path(__file__).parent

SECTION_RELEVANCE = {
    "financials.json": ["1", "8", "10", "12", "13", "executive_summary"],
    "customers.json": ["1", "3", "5", "8"],
    "competitors.json": ["1", "3", "5", "8", "10"],
    "deck.txt": ["1", "executive_summary"],
    "constraints.json": ["1", "4", "8", "10", "11", "12", "13"],
    "market_research.json": ["3", "5", "8"],
    "team.json": ["4", "11"],
}


def load_all_ceo_data() -> dict:
    """Load all CEO-provided files into a structured dict.

    Returns:
        {
            "financials": {...},
            "customers": {...},
            "constraints": {...},
            ...
        }
    """
    data = {}
    for file_path in CEO_DATA_DIR.iterdir():
        if file_path.name.startswith("_") or file_path.name == "__init__.py" or file_path.name == "loader.py":
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


def get_relevant_ceo_data(section_number: str, all_ceo_data: Optional[dict] = None) -> dict:
    """Get CEO data relevant to a specific section.

    Args:
        section_number: The business plan section number (e.g., "8", "12")
        all_ceo_data: Pre-loaded data dict (loads fresh if None)

    Returns:
        Dict of relevant CEO-provided context for this section.
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
                relevant[key] = all_ceo_data[key]

    # Also include any file that doesn't have explicit mapping (catch-all)
    mapped_keys = {Path(f).stem for f in SECTION_RELEVANCE}
    for key, value in all_ceo_data.items():
        if key not in mapped_keys:
            relevant[key] = value

    return relevant
