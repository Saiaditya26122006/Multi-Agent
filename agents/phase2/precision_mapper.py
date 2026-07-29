"""
Precision Mapper Agent — maps raw facts to SSoT nodes with boundary enforcement.

This agent handles the core mapping logic:
1. Classify section (hierarchical pre-filter)
2. Retrieve candidate nodes within that section
3. Extract signal (map to best node)
4. Boundary check (adversarial: does signal violate prohibited claims?)
5. Enforce epistemic prefix
6. Score confidence and route (auto-map / flag / non-scope)

Follows the child agent pattern but operates differently: it processes
individual facts rather than generating business plan sections.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Precision Mapping Agent. Your job is to map a raw business fact
to the most appropriate SSoT (Structured Source of Truth) node.

For each fact you receive, you must:
1. Identify which candidate node it best fits
2. Extract a concise signal from the fact that satisfies the node's purpose
3. Explain why this node is the best fit (rationale)
4. Check if the extracted signal violates any prohibited claims for that node

RESPOND IN JSON ONLY with these exact fields:
{
  "selected_node_id": "BP.X.Y.Z",
  "extracted_signal": "concise fact text for this node",
  "rationale": "why this fact belongs to this node",
  "confidence": 0.0-1.0,
  "boundary_violations": [],
  "is_non_scope": false
}

If the fact doesn't fit ANY node, set is_non_scope=true and selected_node_id=null.
If the signal could violate a prohibited claim, list the violation in boundary_violations.
"""

BOUNDARY_CHECK_PROMPT = """You are a boundary enforcement checker. Given an extracted signal
and a list of prohibited claims for a node, determine if the signal IMPLIES any prohibited claim.

Be strict: even indirect implication counts as a violation.

RESPOND IN JSON ONLY:
{
  "violations_found": true/false,
  "violations": ["description of each violation found"],
  "safe_signal": "rewritten signal that avoids violations (if needed)",
  "explanation": "why this is/isn't a violation"
}
"""

CONFIDENCE_THRESHOLDS = {
    "auto_map": 0.75,
    "flag_for_review": 0.45,
}


def classify_section(fact: str) -> dict:
    """Classify which BP section a fact belongs to (hierarchical pre-filter).

    This is the cheap first pass — narrows 900+ nodes to ~50-70 within one section.

    Args:
        fact: The raw fact text.

    Returns:
        Dict with: section_id, section_num, confidence.
    """
    from services.node_indexer import get_section_descriptions, retrieve_candidate_nodes

    candidates = retrieve_candidate_nodes(fact, top_k=3, threshold=0.3)

    if not candidates:
        return {
            "section_id": None,
            "section_num": None,
            "confidence": 0.0,
        }

    top = candidates[0]
    node_id = top["node_id"]
    parts = node_id.split(".")
    section_num = parts[1] if len(parts) >= 2 else None

    return {
        "section_id": f"BP.{section_num}" if section_num else None,
        "section_num": section_num,
        "confidence": top["similarity"],
    }


def map_fact_to_node(
    fact: str,
    epistemic_status: str = "INFERRED",
    section_hint: Optional[str] = None,
) -> dict:
    """Map a single fact to its best SSoT node.

    Full pipeline: classify section → retrieve candidates → select best →
    boundary check → enforce prefix → score confidence.

    Args:
        fact: The raw fact text.
        epistemic_status: Pre-tagged epistemic status (CONFIRMED/ASSUMPTION/etc).
        section_hint: Optional section hint to skip classification.

    Returns:
        Dict with: node_id, extracted_signal, rationale, confidence,
        boundary_violations, is_non_scope, primary_secondary, epistemic_status.
    """
    from services.node_indexer import retrieve_candidate_nodes
    from services.epistemic_tagger import enforce_prefix

    section = section_hint
    if not section:
        classification = classify_section(fact)
        section = classification.get("section_num")

    candidates = retrieve_candidate_nodes(
        fact=fact,
        section=section,
        top_k=5,
        threshold=0.3,
    )

    if not candidates:
        return _non_scope_result(fact, "No candidate nodes found")

    best = candidates[0]

    if best["similarity"] < CONFIDENCE_THRESHOLDS["flag_for_review"]:
        return _non_scope_result(
            fact,
            f"Best match similarity ({best['similarity']:.2f}) below threshold",
        )

    extracted_signal = _extract_signal(fact, best)
    boundary_violations = _check_boundaries(extracted_signal, best)

    if boundary_violations:
        extracted_signal = _rewrite_signal(extracted_signal, boundary_violations)

    signal_with_prefix = enforce_prefix(extracted_signal, epistemic_status)

    confidence = _compute_confidence(best["similarity"], boundary_violations)

    secondary_nodes = []
    for candidate in candidates[1:3]:
        if candidate["similarity"] > 0.5:
            secondary_nodes.append({
                "node_id": candidate["node_id"],
                "similarity": candidate["similarity"],
                "evidence_weight": round(candidate["similarity"] * 0.3, 2),
            })

    result = {
        "node_id": best["node_id"],
        "node_title": best["node_title"],
        "extracted_signal": signal_with_prefix,
        "rationale": _generate_rationale(fact, best),
        "confidence": confidence,
        "boundary_violations": boundary_violations,
        "is_non_scope": False,
        "primary_secondary": "primary",
        "secondary_nodes": secondary_nodes,
        "epistemic_status": epistemic_status,
        "section": section,
    }

    if confidence < CONFIDENCE_THRESHOLDS["flag_for_review"]:
        result["is_non_scope"] = True
        result["non_scope_reason"] = "Confidence below threshold after boundary check"

    return result


def map_batch(
    facts: list[dict],
    section_hint: Optional[str] = None,
) -> list[dict]:
    """Map multiple facts to nodes.

    Args:
        facts: List of dicts with "text" and optionally "epistemic_status".
        section_hint: Optional section filter for all facts.

    Returns:
        List of mapping result dicts.
    """
    results = []
    for fact_data in facts:
        text = fact_data.get("text", "")
        status = fact_data.get("epistemic_status", "INFERRED")

        result = map_fact_to_node(
            fact=text,
            epistemic_status=status,
            section_hint=section_hint,
        )
        results.append(result)

    return results


def store_mapping(mapping_result: dict, session_id: Optional[str] = None) -> Optional[str]:
    """Store a mapping result in the RAG knowledge base.

    Args:
        mapping_result: Output from map_fact_to_node().
        session_id: Current session ID.

    Returns:
        Chunk ID if stored, None if routed to non-scope or skipped as duplicate.

    Raises:
        RagStoreError: The mapping could not be written. Deliberately not
            caught below — swallowing it would recreate the silent data loss
            this path was fixed for.
    """
    if mapping_result.get("is_non_scope"):
        from services.non_scope_router import route_to_non_scope

        route_to_non_scope(
            fact=mapping_result.get("extracted_signal", ""),
            reason=mapping_result.get("non_scope_reason", "Below confidence threshold"),
            confidence=mapping_result.get("confidence", 0.0),
            session_id=session_id,
        )
        return None

    from services.rag_service import RagStoreError, store

    try:
        result = store(
            content=mapping_result["extracted_signal"],
            source_type="ceo_doc",
            section=mapping_result.get("section"),
            epistemic_status=mapping_result.get("epistemic_status"),
            topic_tags=[
                "ssot-mapping",
                mapping_result.get("node_id", ""),
                mapping_result.get("primary_secondary", "primary"),
            ],
            session_id=session_id,
            confidence=mapping_result.get("confidence"),
            metadata={
                "mapped_to_node": mapping_result.get("node_id"),
                "node_title": mapping_result.get("node_title"),
                "rationale": mapping_result.get("rationale"),
                "boundary_violations": mapping_result.get("boundary_violations", []),
                "secondary_nodes": mapping_result.get("secondary_nodes", []),
            },
        )
        return result.id
    except RagStoreError:
        raise
    except Exception as e:
        logger.error("[PrecisionMapper] Error storing mapping: %s", e)
        return None


def _extract_signal(fact: str, node: dict) -> str:
    """Extract the relevant signal from a fact for a specific node.

    For now, uses the fact directly. In future, an LLM call will distill it.

    Args:
        fact: The raw fact.
        node: The target node metadata.

    Returns:
        Extracted signal text.
    """
    if len(fact) > 200:
        return fact[:200].rsplit(" ", 1)[0]
    return fact


def _check_boundaries(signal: str, node: dict) -> list[str]:
    """Check if a signal violates the node's prohibited claims.

    Uses keyword matching. In future, an adversarial LLM call will be more thorough.

    Args:
        signal: The extracted signal.
        node: The node metadata including prohibited_claims.

    Returns:
        List of violation descriptions (empty if clean).
    """
    prohibited = node.get("prohibited_claims", "")
    if not prohibited:
        return []

    violations = []
    signal_lower = signal.lower()

    prohibition_keywords = {
        "demand": ["demand exists", "market demand", "buyers want"],
        "adoption": ["will adopt", "adoption rate", "users will"],
        "pmf": ["product-market fit", "pmf achieved", "pmf proven"],
        "feasibility": ["is feasible", "can be built", "technically possible"],
        "effectiveness": ["is effective", "works well", "proven to work"],
        "readiness": ["is ready", "launch-ready", "market-ready"],
        "buyer willingness": ["will pay", "buyers will pay", "willing to pay"],
        "competitive advantage": ["competitive advantage", "better than", "superior to"],
    }

    prohibited_lower = prohibited.lower()
    for concept, indicators in prohibition_keywords.items():
        if concept in prohibited_lower:
            for indicator in indicators:
                if indicator in signal_lower:
                    violations.append(
                        f"Signal implies '{concept}' which is prohibited for this node: "
                        f"'{indicator}' found in signal"
                    )

    return violations


def _rewrite_signal(signal: str, violations: list[str]) -> str:
    """Rewrite a signal to avoid boundary violations.

    Adds explicit qualification to avoid prohibited inferences.

    Args:
        signal: The original signal.
        violations: List of violations to avoid.

    Returns:
        Qualified signal text.
    """
    qualifiers = []
    for v in violations:
        if "demand" in v.lower():
            qualifiers.append("(does not imply market demand)")
        elif "adoption" in v.lower():
            qualifiers.append("(does not imply adoption)")
        elif "buyer" in v.lower() or "will pay" in v.lower():
            qualifiers.append("(does not imply buyer willingness to pay)")
        elif "feasibility" in v.lower():
            qualifiers.append("(does not imply technical feasibility)")

    if qualifiers:
        return f"{signal} {' '.join(set(qualifiers))}"
    return signal


def _generate_rationale(fact: str, node: dict) -> str:
    """Generate a rationale for why a fact maps to a node.

    Args:
        fact: The original fact.
        node: The matched node.

    Returns:
        Rationale text.
    """
    purpose = node.get("purpose") or node.get("node_title", "")
    return (
        f"This fact addresses '{purpose}' which is the core purpose of "
        f"node {node['node_id']}. The content provides data relevant to "
        f"the node's required output: {node.get('required_output', 'N/A')[:80]}"
    )


def _compute_confidence(similarity: float, boundary_violations: list[str]) -> float:
    """Compute final confidence score.

    Starts from similarity and penalizes for boundary violations.

    Args:
        similarity: The retrieval similarity score.
        boundary_violations: List of detected violations.

    Returns:
        Confidence score 0.0-1.0.
    """
    confidence = similarity

    penalty = len(boundary_violations) * 0.1
    confidence = max(0.0, confidence - penalty)

    return round(confidence, 3)


def _non_scope_result(fact: str, reason: str) -> dict:
    """Build a non-scope result dict.

    Args:
        fact: The unmapped fact.
        reason: Why it couldn't be mapped.

    Returns:
        Standard result dict with is_non_scope=True.
    """
    return {
        "node_id": None,
        "node_title": None,
        "extracted_signal": fact,
        "rationale": None,
        "confidence": 0.0,
        "boundary_violations": [],
        "is_non_scope": True,
        "non_scope_reason": reason,
        "primary_secondary": None,
        "secondary_nodes": [],
        "epistemic_status": "INFERRED",
        "section": None,
    }
