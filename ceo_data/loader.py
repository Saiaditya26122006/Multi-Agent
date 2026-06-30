"""
CEO Data Loader — RAG-backed retrieval of Alex's EpistemicOS data.

This module maintains backward-compatible function signatures but now
retrieves data semantically via the RAG knowledge base (Supabase pgvector)
instead of static JSON file mapping.

Fallback: if RAG is unavailable, falls back to local JSON file loading.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CEO_DATA_DIR = Path(__file__).parent

MAX_SECTION_CHARS = 3000

SECTION_QUERIES = {
    "1": "product definition identity what EpistemicOS is category positioning",
    "3": "problem jobs-to-be-done stakeholders pain points workarounds",
    "4": "buyers ICP personas procurement institutional users",
    "5": "value proposition differentiation auditability risk-reduction",
    "8": "market competitive context TAM competitors category positioning",
    "9": "GTM sales motion go-to-market pilot strategy geography",
    "10": "pricing monetization subscription unit of sale business model",
    "12": "financial revenue costs funding burn rate projections",
    "13": "compliance legal risk GDPR AI Act DPA security",
    "executive_summary": "product definition core value buyers geography stage",
}

_rag_available = None


def _check_rag_available() -> bool:
    """Check if RAG service is usable (Supabase + embeddings ready)."""
    global _rag_available
    if _rag_available is not None:
        return _rag_available

    try:
        from services.rag_service import retrieve
        result = retrieve("test", top_k=1, threshold=0.0)
        _rag_available = True
        logger.info("[CEOData] RAG service available — using semantic retrieval")
    except Exception as e:
        _rag_available = False
        logger.warning("[CEOData] RAG unavailable (%s) — falling back to JSON files", e)

    return _rag_available


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
        if file_path.name.startswith("sk-"):
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
        except Exception as e:
            logger.warning("[CEOData] Failed to load %s: %s", file_path.name, e)

    return data


def get_relevant_ceo_data(
    section_number: str, all_ceo_data: Optional[dict] = None
) -> dict:
    """Get CEO data relevant to a specific section via RAG semantic retrieval.

    Falls back to local JSON if RAG is unavailable.

    Args:
        section_number: The business plan section number (e.g., "8", "12")
        all_ceo_data: Pre-loaded data dict (ignored when RAG is available)

    Returns:
        Dict of relevant CEO-provided context for this section.
    """
    if _check_rag_available():
        return _get_via_rag(section_number)

    return _get_via_json_fallback(section_number, all_ceo_data)


def _get_via_rag(section_number: str) -> dict:
    """Retrieve section-relevant data from the RAG knowledge base."""
    from services.rag_service import retrieve, format_chunks_for_injection
    from services.rag_hooks import check_negative_knowledge

    query = SECTION_QUERIES.get(
        str(section_number),
        f"business plan section {section_number}",
    )

    chunks = retrieve(
        query=query,
        source_types=["ceo_doc", "conversation", "decision", "correction", "agent_insight"],
        top_k=10,
        threshold=0.35,
        recency_boost=True,
    )

    if not chunks:
        return {"_rag": True, "_empty": True, "note": f"No relevant data found for section {section_number}"}

    formatted = format_chunks_for_injection(chunks, max_chars=MAX_SECTION_CHARS)

    result = {
        "_rag": True,
        "_chunk_count": len(chunks),
        "relevant_context": formatted,
    }

    confirmed = [c for c in chunks if c.epistemic_status == "CONFIRMED"]
    assumptions = [c for c in chunks if c.epistemic_status == "ASSUMPTION"]
    missing = [c for c in chunks if c.epistemic_status == "MISSING"]

    if missing:
        result["evidence_gaps"] = [c.content for c in missing[:3]]
    if assumptions:
        result["assumption_count"] = len(assumptions)
    if confirmed:
        result["confirmed_count"] = len(confirmed)

    return result


def _get_via_json_fallback(section_number: str, all_ceo_data: Optional[dict] = None) -> dict:
    """Original JSON-file-based retrieval (fallback when RAG is down)."""
    section_relevance = {
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
        "knowledge_architecture.json": ["7"],
        "gtm_sales.json": ["9"],
        "pricing_model.json": ["10"],
        "validation_requirements.json": ["12"],
        "compliance_risks.json": ["13"],
        "assumptions_register.json": ["15"],
        "decision_register.json": ["16"],
        "open_questions.json": ["17"],
        "contradictions.json": ["18"],
        "tasks_register.json": ["19"],
    }

    if all_ceo_data is None:
        all_ceo_data = load_all_ceo_data()

    if not all_ceo_data:
        return {}

    relevant = {}
    for filename, applicable_sections in section_relevance.items():
        if str(section_number) in applicable_sections:
            key = Path(filename).stem
            if key in all_ceo_data:
                topic_data = all_ceo_data[key]
                if isinstance(topic_data, dict):
                    cleaned = {k: v for k, v in topic_data.items() if k != "_meta"}
                    relevant[key] = cleaned
                else:
                    relevant[key] = topic_data

    serialized = json.dumps(relevant, indent=2, default=str)
    if len(serialized) <= MAX_SECTION_CHARS:
        return relevant

    trimmed = {}
    for key, value in relevant.items():
        candidate = {**trimmed, key: value}
        if len(json.dumps(candidate, indent=2, default=str)) <= MAX_SECTION_CHARS:
            trimmed[key] = value

    return trimmed
