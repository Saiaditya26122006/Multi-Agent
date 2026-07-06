"""
Ingestion Pipeline — chunk and embed Alex's CEO data into the RAG knowledge base.

Reads structured JSON files from ceo_data/, breaks them into atomic chunks,
and stores each chunk with its epistemic status and topic metadata.

Usage:
    python -m services.ingestion_pipeline
"""

import json
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

CEO_DATA_DIR = Path(__file__).parent.parent / "ceo_data"

# Section numbers here MUST correspond to actual BP.N top-level domains
# defined in bp_architecture.json. There are only 11 real domains (verified
# by grepping bp_architecture.json's top-level "node_id": "BP.N" entries):
#
#   BP.1  Product, Workflow, and Scope Definition
#   BP.2  Core Problem, Urgency, and Governing Hypothesis
#   BP.3  Evidence Base and Source Governance
#   BP.4  Market Boundaries, Sizing, Segmentation, and ICP
#   BP.5  Users, Buyers, Procurement, and Buying System
#   BP.6  Customer Discovery, Adoption, and Institutional Legitimacy
#   BP.7  Legal, Regulatory, Data, and Deployability Governance
#   BP.8  Competitive Landscape, Positioning, and Differentiation
#   BP.9  Business Model, Revenue, and GTM Logic
#   BP.10 Validation, Behavioural Evidence, and PMF Decision Logic
#   BP.11 Investor Narrative, Business Plan, and Data Room Readiness
#
# The previous version of this map invented its own numbering (going up to
# "19") that had no relationship to this structure at all — e.g. customers.json
# was tagged "3" (Evidence Base) when its content (problem/JTBD) actually
# belongs in BP.2, and seven files were tagged sections 12-19 which don't
# exist as domains, making that content permanently invisible to
# coverage_calculator.py's per-section fill check regardless of threshold
# or top_k tuning. This map now points every file at the domain its content
# is actually about. Composite files (buyers_icp.json, customers.json,
# constraints.json) override this default per-item in their chunker
# functions below, since a single file's content spans more than one domain.
SECTION_MAP = {
    "product_definition.json": {"section": "1", "topics": ["product", "definition", "identity"]},
    "customers.json": {"section": "2", "topics": ["problem", "jtbd", "customers", "stakeholders"]},
    "buyers_icp.json": {"section": "4", "topics": ["buyers", "icp", "personas", "procurement"]},
    "value_proposition.json": {"section": "8", "topics": ["value", "proposition", "differentiation"]},
    "capabilities.json": {"section": "1", "topics": ["capabilities", "modules", "product"]},
    "knowledge_architecture.json": {"section": "3", "topics": ["architecture", "governance", "knowledge"]},
    "competitors.json": {"section": "8", "topics": ["market", "competitive", "competitors"]},
    "market_research.json": {"section": "4", "topics": ["market", "tam", "geography"]},
    "gtm_sales.json": {"section": "9", "topics": ["gtm", "sales", "go-to-market"]},
    "pricing_model.json": {"section": "9", "topics": ["pricing", "monetization", "business-model"]},
    "financials.json": {"section": "9", "topics": ["financials", "revenue", "costs"]},
    "validation_requirements.json": {"section": "10", "topics": ["validation", "evidence", "proof"]},
    "compliance_risks.json": {"section": "7", "topics": ["compliance", "legal", "risk", "gdpr"]},
    "assumptions_register.json": {"section": "10", "topics": ["assumptions", "hypotheses"]},
    "decision_register.json": {"section": "11", "topics": ["decisions", "strategy"]},
    "open_questions.json": {"section": "10", "topics": ["questions", "unknowns", "gaps"]},
    "contradictions.json": {"section": "3", "topics": ["contradictions", "ambiguities"]},
    "tasks_register.json": {"section": "11", "topics": ["tasks", "execution", "dependencies"]},
    "constraints.json": {"section": "10", "topics": ["constraints", "compliance", "assumptions"]},
    "team.json": {"section": "11", "topics": ["team", "organization"]},
    "bp_architecture.json": {"section": "governance", "topics": ["architecture", "governance", "bp-nodes"]},
    "prohibited_claims.json": {"section": "governance", "topics": ["constraints", "prohibited", "rules"]},
    "bp_dependencies.json": {"section": "governance", "topics": ["dependencies", "architecture"]},
}


def _chunk_list_items(data: dict, list_key: str, section: str, topics: list[str]) -> list[dict]:
    """Chunk a JSON file that contains a list of items under a specific key."""
    chunks = []
    items = data.get(list_key, [])

    for item in items:
        if isinstance(item, dict):
            status = item.get("status") or item.get("epistemic_status")
            content_parts = []
            for k, v in item.items():
                if k in ("status", "epistemic_status", "_meta"):
                    continue
                if v and str(v).strip():
                    content_parts.append(f"{k}: {v}")

            content = "; ".join(content_parts)
            if content:
                chunks.append({
                    "content": content,
                    "source_type": "ceo_doc",
                    "section": section,
                    "epistemic_status": _normalize_status(status),
                    "topic_tags": topics,
                })

    return chunks


def _chunk_facts(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk a JSON file that has a 'facts' list."""
    return _chunk_list_items(data, "facts", section, topics)


def _normalize_status(status: Optional[str]) -> Optional[str]:
    """Normalize various status strings to valid enum values."""
    if not status:
        return None

    status = status.upper().strip()
    mapping = {
        "CONFIRMED": "CONFIRMED",
        "CONFIRMED AS INTENDED FUNCTION": "CONFIRMED",
        "CONFIRMED AS PRODUCT PRINCIPLE": "CONFIRMED",
        "CONFIRMED AS NEEDED VALIDATION": "CONFIRMED",
        "CONFIRMED AS INTERNAL ICP HYPOTHESIS": "CONFIRMED",
        "CONFIRMED AS HYPOTHESIS": "CONFIRMED",
        "CONFIRMED AS UNRESOLVED": "CONFIRMED",
        "ASSUMPTION": "ASSUMPTION",
        "CONTRADICTION": "CONTRADICTION",
        "INFERRED": "INFERRED",
        "MISSING": "MISSING",
        "REQUIRED": "MISSING",
        "UNVERIFIED EXTERNAL CLAIM": "UNVERIFIED_EXTERNAL_CLAIM",
        "UNVERIFIED_EXTERNAL_CLAIM": "UNVERIFIED_EXTERNAL_CLAIM",
    }

    return mapping.get(status, "ASSUMPTION")


def _chunk_product_definition(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk product_definition.json."""
    return _chunk_facts(data, section, topics)


def _chunk_customers(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk customers.json — facts list (BP.2, problem/JTBD) + interviews
    gap (BP.6, Customer Discovery/Adoption — interview evidence is discovery
    activity, not problem-definition content, and BP.6 otherwise has no
    source file mapped to it at all)."""
    chunks = _chunk_facts(data, section, topics)

    interviews = data.get("interviews_conducted", {})
    if interviews.get("gap"):
        chunks.append({
            "content": f"EVIDENCE GAP: {interviews.get('gap_reason', 'No interview data')}",
            "source_type": "ceo_doc",
            "section": "6",
            "epistemic_status": "MISSING",
            "topic_tags": topics + ["evidence-gap", "customer-discovery"],
        })

    return chunks


def _chunk_buyers_icp(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk buyers_icp.json — buyer personas (BP.5, Users/Buyers/Procurement)
    + ICP (BP.4, Market Boundaries/Segmentation/ICP) + facts (default to the
    section passed in, BP.4). These two halves of the file belong to two
    different real BP domains, so they can't share one section tag."""
    chunks = []

    for buyer in data.get("buyer_personas", []):
        parts = [f"Buyer: {buyer.get('buyer', '')}"]
        for k in ("need", "buying_power", "budget", "trigger", "barrier"):
            if buyer.get(k):
                parts.append(f"{k}: {buyer[k]}")
        content = "; ".join(parts)
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": "5",
            "epistemic_status": _normalize_status(buyer.get("status")),
            "topic_tags": topics + ["buyer-persona"],
        })

    for icp in data.get("icp", []):
        content = (
            f"ICP: {icp.get('segment', '')}; "
            f"high-fit: {icp.get('high_fit', '')}; "
            f"exclusion: {icp.get('exclusion', '')}"
        )
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": _normalize_status(icp.get("status")),
            "topic_tags": topics + ["icp"],
        })

    for fact in data.get("facts", []):
        chunks.append({
            "content": fact.get("fact", ""),
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": _normalize_status(fact.get("status")),
            "topic_tags": topics,
        })

    return chunks


def _chunk_capabilities(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk capabilities.json — confirmed + assumed."""
    chunks = []

    for cap in data.get("confirmed_capabilities", []):
        content = f"Module: {cap.get('module', '')}; {cap.get('description', '')}"
        if cap.get("uncertainty"):
            content += f"; uncertainty: {cap['uncertainty']}"
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": "CONFIRMED",
            "topic_tags": topics + ["confirmed-capability"],
        })

    for cap in data.get("assumed_capabilities", []):
        content = f"Module (assumed): {cap.get('module', '')}; {cap.get('description', '')}"
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": "ASSUMPTION",
            "topic_tags": topics + ["assumed-capability"],
        })

    return chunks


def _chunk_knowledge_architecture(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk knowledge_architecture.json — each layer is a chunk."""
    chunks = []
    for layer in data.get("layers", []):
        content = (
            f"Knowledge layer: {layer.get('layer', '')}; "
            f"purpose: {layer.get('purpose', '')}; "
            f"data: {layer.get('data_contained', '')}; "
            f"owner: {layer.get('owner', '')}; "
            f"risk if poorly maintained: {layer.get('risk_if_poorly_maintained', '')}"
        )
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": _normalize_status(layer.get("status")),
            "topic_tags": topics,
        })
    return chunks


def _chunk_gtm_sales(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk gtm_sales.json — each GTM hypothesis is a chunk."""
    return _chunk_list_items(data, "hypotheses", section, topics)


def _chunk_pricing_model(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk pricing_model.json."""
    return _chunk_list_items(data, "items", section, topics)


def _chunk_validation_requirements(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk validation_requirements.json."""
    return _chunk_list_items(data, "claims_to_validate", section, topics)


def _chunk_compliance_risks(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk compliance_risks.json."""
    return _chunk_list_items(data, "risks", section, topics)


def _chunk_assumptions_register(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk assumptions_register.json."""
    return _chunk_list_items(data, "assumptions", section, topics)


def _chunk_decision_register(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk decision_register.json."""
    return _chunk_list_items(data, "decisions", section, topics)


def _chunk_open_questions(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk open_questions.json."""
    return _chunk_list_items(data, "questions", section, topics)


def _chunk_contradictions(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk contradictions.json."""
    return _chunk_list_items(data, "contradictions", section, topics)


def _chunk_tasks_register(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk tasks_register.json."""
    return _chunk_list_items(data, "tasks", section, topics)


def _chunk_constraints(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk constraints.json — strategic assumptions + uncertainty (BP.10,
    Validation/PMF Decision Logic — these are bets that need validating) +
    compliance requirements (BP.7, Legal/Regulatory Governance — a distinct
    real domain, not the same one as the rest of this file)."""
    chunks = []

    for item in data.get("strategic_assumptions", []):
        content = f"Strategic constraint: {item.get('constraint', '')}"
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": _normalize_status(item.get("status")),
            "topic_tags": topics,
        })

    for item in data.get("compliance_requirements", []):
        content = f"Compliance requirement: {item.get('requirement', '')}"
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": "7",
            "epistemic_status": _normalize_status(item.get("status")),
            "topic_tags": topics + ["compliance"],
        })

    uncertainty = data.get("largest_unresolved_uncertainty", {})
    if uncertainty.get("fact"):
        chunks.append({
            "content": f"Largest unresolved uncertainty: {uncertainty['fact']}",
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": _normalize_status(uncertainty.get("status")),
            "topic_tags": topics + ["critical-uncertainty"],
        })

    return chunks


def _chunk_bp_architecture(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk bp_architecture.json — each BP node is a chunk."""
    chunks = []
    for node in data.get("nodes", []):
        content_parts = [
            f"BP Node {node.get('node_id', '')}: {node.get('node_title', '')}",
        ]
        if node.get("purpose"):
            content_parts.append(f"Purpose: {node['purpose']}")
        if node.get("required_output"):
            content_parts.append(f"Required output: {node['required_output']}")
        if node.get("prohibited_claims"):
            content_parts.append(f"PROHIBITED: {node['prohibited_claims']}")

        content = "; ".join(content_parts)
        chunks.append({
            "content": content,
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": "CONFIRMED",
            "topic_tags": topics + [node.get("node_id", "")],
        })

    return chunks


def _chunk_prohibited_claims(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Chunk prohibited_claims.json — global + per-section rules."""
    chunks = []

    for claim in data.get("global_prohibitions", []):
        chunks.append({
            "content": f"GLOBAL PROHIBITION: {claim}",
            "source_type": "ceo_doc",
            "section": section,
            "epistemic_status": "CONFIRMED",
            "topic_tags": topics + ["global-rule"],
        })

    for section_key, claims in data.get("per_section_prohibitions", {}).items():
        for claim in claims:
            chunks.append({
                "content": f"PROHIBITION ({section_key}): {claim}",
                "source_type": "ceo_doc",
                "section": section,
                "epistemic_status": "CONFIRMED",
                "topic_tags": topics + [section_key],
            })

    return chunks


def _chunk_generic(data: dict, section: str, topics: list[str]) -> list[dict]:
    """Fallback chunker for files without a specific handler."""
    chunks = []

    if isinstance(data, dict):
        if data.get("gap") or data.get("status") == "no_data":
            gap_reason = data.get("gap_reason", "No data provided")
            chunks.append({
                "content": f"DATA GAP: {gap_reason}",
                "source_type": "ceo_doc",
                "section": section,
                "epistemic_status": "MISSING",
                "topic_tags": topics + ["evidence-gap"],
            })
            return chunks

        for key, value in data.items():
            if key == "_meta":
                continue
            if isinstance(value, str) and value.strip():
                chunks.append({
                    "content": f"{key}: {value}",
                    "source_type": "ceo_doc",
                    "section": section,
                    "topic_tags": topics,
                })
            elif isinstance(value, dict) and value.get("fact"):
                chunks.append({
                    "content": value["fact"],
                    "source_type": "ceo_doc",
                    "section": section,
                    "epistemic_status": _normalize_status(value.get("status")),
                    "topic_tags": topics,
                })

    return chunks


CHUNKERS = {
    "product_definition.json": _chunk_product_definition,
    "customers.json": _chunk_customers,
    "buyers_icp.json": _chunk_buyers_icp,
    "capabilities.json": _chunk_capabilities,
    "knowledge_architecture.json": _chunk_knowledge_architecture,
    "gtm_sales.json": _chunk_gtm_sales,
    "pricing_model.json": _chunk_pricing_model,
    "validation_requirements.json": _chunk_validation_requirements,
    "compliance_risks.json": _chunk_compliance_risks,
    "assumptions_register.json": _chunk_assumptions_register,
    "decision_register.json": _chunk_decision_register,
    "open_questions.json": _chunk_open_questions,
    "contradictions.json": _chunk_contradictions,
    "tasks_register.json": _chunk_tasks_register,
    "constraints.json": _chunk_constraints,
    "bp_architecture.json": _chunk_bp_architecture,
    "prohibited_claims.json": _chunk_prohibited_claims,
}


def ingest_all_ceo_data() -> dict:
    """Ingest all CEO data files into the RAG knowledge base.

    Returns:
        Summary dict with counts per file.
    """
    from services.rag_service import batch_store

    summary = {}
    total_chunks = 0
    excluded_files = {"__init__.py", "loader.py", "__pycache__"}

    for file_path in sorted(CEO_DATA_DIR.iterdir()):
        if file_path.name in excluded_files:
            continue
        if file_path.name.startswith("_"):
            continue
        if file_path.name.startswith("EpistemicOS"):
            continue
        if file_path.name.startswith("sk-"):
            continue
        if file_path.suffix not in (".json",):
            continue

        section_info = SECTION_MAP.get(file_path.name, {"section": "unknown", "topics": []})
        section = section_info["section"]
        topics = section_info["topics"]

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[Ingest] Failed to read %s: %s", file_path.name, e)
            summary[file_path.name] = {"status": "error", "error": str(e)}
            continue

        chunker = CHUNKERS.get(file_path.name, _chunk_generic)
        chunks = chunker(data, section, topics)

        if chunks:
            ids = batch_store(chunks)
            stored_count = len([i for i in ids if i])
            summary[file_path.name] = {
                "status": "ok",
                "chunks_generated": len(chunks),
                "chunks_stored": stored_count,
            }
            total_chunks += stored_count
            logger.info(
                "[Ingest] %s → %d chunks generated, %d stored",
                file_path.name,
                len(chunks),
                stored_count,
            )
        else:
            summary[file_path.name] = {"status": "empty", "chunks_generated": 0}
            logger.info("[Ingest] %s → no chunks generated", file_path.name)

    logger.info("[Ingest] Complete. Total chunks stored: %d", total_chunks)
    summary["_total"] = total_chunks
    return summary


def ingest_raw_text(
    text: str,
    source_type: str = "ceo_doc",
    section: Optional[str] = None,
    topic_tags: Optional[list[str]] = None,
) -> int:
    """Ingest raw text by splitting into paragraphs and storing each.

    Args:
        text: Raw text content to ingest.
        source_type: Source type tag.
        section: Business plan section.
        topic_tags: Topic tags.

    Returns:
        Number of chunks stored.
    """
    from services.rag_service import batch_store

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []

    for para in paragraphs:
        if len(para) < 20:
            continue
        chunks.append({
            "content": para,
            "source_type": source_type,
            "section": section,
            "topic_tags": topic_tags or [],
        })

    if chunks:
        ids = batch_store(chunks)
        return len([i for i in ids if i])

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("Starting CEO data ingestion...")
    result = ingest_all_ceo_data()
    print(json.dumps(result, indent=2))
