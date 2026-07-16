"""
FEED Workspace Handler — processes raw data input from Alex.

Handles: raw text, corrections, targeted additions, structured data.
Detects format, splits into atomic facts, classifies content type,
matches to BP architecture nodes, presents for approval, and stores.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from tools.trace_emitter import emit_trace

logger = logging.getLogger(__name__)

PENDING_FACT_TTL = 600  # 10 minutes

CONTENT_TYPE_PATTERNS = {
    "decision": [
        (r"(we decided|decision:|i decided|the decision is|we chose|we will go with)", 0.9),
        (r"(approved|rejected|killed|chose .+ over)", 0.85),
        (r"(going with|not going with|final call)", 0.8),
    ],
    "risk": [
        (r"(risk:|the risk is|biggest risk|main risk|key risk)", 0.9),
        (r"(could fail|might not work|danger|threat|vulnerability)", 0.8),
        (r"(worst case|if .+ fails|downside)", 0.75),
    ],
    "metric": [
        (r"(\d+%|\$[\d,.]+|[\d,.]+ users|[\d,.]+ customers)", 0.85),
        (r"(revenue|arpu|cac|ltv|churn|conversion|retention|mrr|arr)", 0.8),
        (r"(target:|goal:|kpi:|metric:)", 0.9),
    ],
    "constraint": [
        (r"(must not|cannot|we can't|we don't have|budget is|deadline)", 0.85),
        (r"(limitation|constraint|restriction|blocker|dependency)", 0.8),
        (r"(not allowed|prohibited|out of scope)", 0.8),
    ],
    "task": [
        (r"(need to|todo|action item|next step|we should)", 0.75),
        (r"(task:|action:|deliverable:)", 0.9),
        (r"(by (monday|tuesday|wednesday|thursday|friday|next week|end of))", 0.8),
    ],
    "open_question": [
        (r"(still unclear|don't know yet|need to find out|unresolved|tbd)", 0.85),
        (r"(question:|open question:|unsure whether)", 0.9),
        (r"(haven't decided|not sure if|need more data on)", 0.8),
    ],
    "assumption": [
        (r"(i assume|we assume|assumption:|assuming that|hypothesis)", 0.9),
        (r"(i think|i believe|probably|likely|should be|expected to)", 0.75),
        (r"(untested|unvalidated|guess|bet is that)", 0.8),
    ],
    "fact": [
        (r"(confirmed|verified|proven|we know|evidence shows|data shows)", 0.85),
        (r"(the contract states|according to|research shows|study found)", 0.85),
    ],
}

EPISTEMIC_STATUS_BY_CONTENT_TYPE = {
    "decision": "CONFIRMED",
    "risk": "INFERRED",
    "metric": "CONFIRMED",
    "constraint": "CONFIRMED",
    "task": "INFERRED",
    "open_question": "MISSING",
    "assumption": "ASSUMPTION",
    "fact": "CONFIRMED",
}


def classify_content_type(text: str) -> dict:
    """Classify the semantic content type of a fact.

    Args:
        text: A single extracted fact/claim.

    Returns:
        Dict with: content_type, confidence, matched_pattern.
    """
    text_lower = text.lower()
    best_type = "fact"
    best_confidence = 0.0
    best_pattern = None

    for content_type, patterns in CONTENT_TYPE_PATTERNS.items():
        for pattern, conf in patterns:
            if re.search(pattern, text_lower):
                if conf > best_confidence:
                    best_type = content_type
                    best_confidence = conf
                    best_pattern = pattern

    if best_confidence == 0.0:
        best_type = "fact"
        best_confidence = 0.5
        best_pattern = None

    return {
        "content_type": best_type,
        "confidence": best_confidence,
        "matched_pattern": best_pattern,
        "low_confidence": best_confidence < 0.7,
    }


_VALID_NODE_ID = re.compile(r"^BP\.\d")


def _load_bp_architecture() -> list[dict]:
    """Load BP architecture nodes from the JSON file.

    Defensively drops any entry whose node_id isn't a real "BP.<digit>..."
    ID — a re-export from the source spreadsheet has previously included the
    header/field-description row as if it were node data (node_id literally
    read "Unique hierarchical ID; permanent once assigned"). That single bad
    row doesn't collide with any real node, but it does get embedded and
    shown to the LLM classifier as a genuine candidate, which is worse: it
    silently pollutes classification instead of failing loudly. This filter
    makes the loader resilient to that class of export bug regardless of
    how it happens again in the future.
    """
    from pathlib import Path

    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"
    if not arch_path.exists():
        logger.warning("[FeedHandler] bp_architecture.json not found at %s", arch_path)
        return []

    with open(arch_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_nodes = data.get("nodes", [])
    valid_nodes = [n for n in all_nodes if _VALID_NODE_ID.match(str(n.get("node_id", "")))]

    dropped = len(all_nodes) - len(valid_nodes)
    if dropped:
        logger.warning(
            "[FeedHandler] Dropped %d entr%s from bp_architecture.json with no valid BP.x node_id "
            "(likely a header/description row that leaked into the data during export)",
            dropped, "y" if dropped == 1 else "ies",
        )

    return valid_nodes


def _get_node_details(node_id: str) -> Optional[dict]:
    """Look up a node's full details from the architecture file."""
    nodes = _load_bp_architecture()
    for node in nodes:
        if node.get("node_id") == node_id:
            return node
    return None


def _get_level1_domains() -> list[dict]:
    """Get all level-1 domains (BP.1, BP.2, etc.) from the architecture."""
    nodes = _load_bp_architecture()
    domains = [
        n for n in nodes
        if n.get("node_id", "").count(".") == 1
        and n["node_id"].startswith("BP.")
        and n.get("level") == 1.0
    ]
    return domains


def _get_all_children(parent_id: str) -> list[dict]:
    """Get all descendants (children, grandchildren, etc.) of a node.

    IMPORTANT: Also includes the parent node itself in the results.
    This ensures that when classifying to BP.2 domain, BP.2 itself
    is a candidate, not just BP.2.1, BP.2.2, etc.
    """
    nodes = _load_bp_architecture()
    children = []
    prefix = f"{parent_id}."

    # Include the parent node itself
    for n in nodes:
        if n.get("node_id") == parent_id:
            children.append(n)
            break

    # Include all descendants
    for n in nodes:
        nid = n.get("node_id", "")
        if nid.startswith(prefix):
            children.append(n)
    return children


def _next_child_id(parent_id: str, all_nodes: list[dict]) -> str:
    """Compute the next available sibling ID under a parent.

    bp_architecture.json's IDs are plain dotted-decimal — a node's children
    are exactly parent_id + ".N" for increasing N (e.g. BP.6's children are
    BP.6.1, BP.6.2, BP.6.3...). This finds the highest existing sibling
    number and returns the next one, so a newly created node never collides
    with an existing ID.
    """
    prefix = f"{parent_id}."
    max_n = 0
    for node in all_nodes:
        nid = node.get("node_id", "")
        if not nid.startswith(prefix):
            continue
        remainder = nid[len(prefix):]
        # Only count DIRECT children (remainder has no further dots) — a
        # grandchild like BP.6.1.2 shouldn't influence BP.6's own next slot.
        if "." in remainder:
            continue
        try:
            n = int(remainder)
        except ValueError:
            continue
        max_n = max(max_n, n)
    return f"{parent_id}.{max_n + 1}"


def _create_new_node(node_title: str, parent_id: str, verbatim_text: str = "") -> dict:
    """Permanently add a new node to bp_architecture.json under a parent.

    This used to just tag a fact with a placeholder node_id ("NEW_PENDING")
    and Alex's name as a label — nothing was actually added to the
    architecture, so the "new node" was invisible to every future
    classification pass. Per explicit direction, this now writes a real
    node with a real hierarchical ID, so later facts can match against it
    too.

    Most of the richer schema fields (proof_burden, evidence_requirement,
    execution_mode, controller, etc.) can't be meaningfully inferred from a
    one-line CEO-given name, so they're left null exactly like the fields
    canonical nodes leave null when not applicable — this new node is
    architecture_status "candidate" (the same status the canonical BP.1
    itself carries until reviewed), not asserted as fully specified.

    Args:
        node_title: The name Alex gave the new node.
        parent_id: The node_id this should be created under (e.g. "BP.6").
        verbatim_text: The fact that triggered creation, used only to write
            an honest, non-invented purpose/required_output description.

    Returns:
        Dict with node_id, node_title, level of the newly created node.
        Falls back to a synthetic in-memory-only node (never written) if
        the architecture file can't be read or written, so the caller can
        still proceed without crashing — it just won't be classifiable
        again later.
    """
    from pathlib import Path

    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"

    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_nodes = data.get("nodes", [])
    except Exception as e:
        logger.error("[FeedHandler] Could not read bp_architecture.json to create new node: %s", e)
        return {"node_id": "NEW_PENDING", "node_title": node_title, "level": 0}

    parent = next((n for n in all_nodes if n.get("node_id") == parent_id), None)
    parent_level = parent.get("level", 1.0) if parent else 1.0
    parent_type = parent.get("node_type", "domain") if parent else "domain"

    new_id = _next_child_id(parent_id, all_nodes)
    # Overwhelming majority pattern in the real file: domain -> section,
    # anything deeper -> subsection. A handful of nodes use more specific
    # types (decision_gate, component...) but those reflect a role only a
    # human architect would assign, not something inferable from a title.
    new_type = "section" if parent_type == "domain" else "subsection"

    new_node = {
        "node_id": new_id,
        "parent_node": parent_id,
        "level": parent_level + 1,
        "node_type": new_type,
        "atomic_status": "non_atomic",
        "node_title": node_title,
        "purpose": f"Captures CEO-submitted information about {node_title.lower()} — created because no existing node under {parent_id} covered this fact.",
        "required_output": f"Structured facts related to {node_title.lower()}, as submitted by the CEO via Feed.",
        "output_format": None,
        "proof_burden": None,
        "evidence_requirement": None,
        "evidence_gaps_assumptions": None,
        "linked_uncertainties": None,
        "prohibited_claims_inference_patterns": None,
        "dependencies": None,
        "reopen_condition": None,
        "decision_implication": None,
        "execution_mode": None,
        "human_review_type": None,
        "executor": None,
        "controller": None,
        "architecture_status": "candidate",
        "evidence_status": None,
        "notes_limitations": (
            "Created automatically via Feed when Alex named a new node for a fact that didn't "
            "fit any existing node — not yet reviewed by an architecture pass."
            + (f" Originating fact: \"{verbatim_text[:200]}\"" if verbatim_text else "")
        ),
        "source_data_refs": None,
        "extracted_data": None,
        "mapping_rationale": None,
        "evidence_use_boundary": None,
        "rag_confidence": None,
        "review_notes": None,
    }

    all_nodes.append(new_node)
    data["nodes"] = all_nodes

    try:
        with open(arch_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("[FeedHandler] Could not write new node %s to bp_architecture.json: %s", new_id, e)
        return {"node_id": "NEW_PENDING", "node_title": node_title, "level": 0}

    # The in-memory embedding cache (_get_node_embeddings) was built before
    # this node existed — invalidate it so the very next classification call
    # picks the new node up instead of silently missing it until restart.
    global _node_embedding_cache
    _node_embedding_cache = None

    logger.info("[FeedHandler] Created new node %s (%s) under %s", new_id, node_title, parent_id)

    return {"node_id": new_id, "node_title": node_title, "level": new_node["level"]}


# This map MUST mirror the real top-level domains defined in
# bp_architecture.json (verified directly against that file — there are
# exactly 11, BP.1-BP.11, no BP.12). The previous version of this map was a
# second, independent, WRONG invented taxonomy that didn't match the real
# architecture at all — e.g. it claimed BP.6 = "Evidence and Validation",
# when the real BP.6 is "Customer Discovery, Adoption, and Institutional
# Legitimacy" (the real "evidence" domain is BP.3). That mismatch is exactly
# why Alex saw "a new node could sit under: BP.6 — Evidence and Validation"
# and assumed the system was about to create a duplicate BP.6 — it wasn't
# creating anything, it was just mislabeling the real, existing node.
# services/ingestion_pipeline.py's SECTION_MAP had this identical class of
# bug; this is the same fix applied here.
LEVEL1_KEYWORDS: dict[str, list[str]] = {
    "BP.1": ["product", "scope", "workflow", "platform",
             "diagnostic", "tool", "system", "capability"],
    "BP.2": ["problem", "urgency", "hypothesis", "pain point",
             "jtbd", "why now", "governing"],
    "BP.3": ["evidence", "source", "citation", "provenance",
             "methodology", "research quality", "data governance"],
    "BP.4": ["market", "size", "tam", "segment", "industry",
             "icp", "boundary", "geography"],
    "BP.5": ["buyer", "customer", "persona", "willingness",
             "pay", "wtp", "dean", "director", "procurement",
             "budget owner"],
    "BP.6": ["interview", "discovery", "adoption", "pilot",
             "legitimacy", "institutional trust", "early customer"],
    "BP.7": ["gdpr", "compliance", "legal", "regulation",
             "deployability", "privacy", "data governance"],
    "BP.8": ["competition", "competitor", "alternative",
             "substitute", "positioning", "differentiation"],
    "BP.9": ["pricing", "price", "revenue", "model", "euros",
             "license", "subscription", "gtm", "sales", "channel",
             "distribution", "outreach", "partner"],
    "BP.10": ["validation", "confirmed", "survey", "pmf",
              "behavioural evidence", "proof", "traction"],
    "BP.11": ["investor", "pitch", "data room", "narrative",
              "fundraise", "cap table", "finance", "cost",
              "budget", "unit economics"],
}

LEVEL1_TITLES: dict[str, str] = {
    "BP.1": "Product, Workflow, and Scope Definition",
    "BP.2": "Core Problem, Urgency, and Governing Hypothesis",
    "BP.3": "Evidence Base and Source Governance",
    "BP.4": "Market Boundaries, Sizing, Segmentation, and ICP",
    "BP.5": "Users, Buyers, Procurement, and Buying System",
    "BP.6": "Customer Discovery, Adoption, and Institutional Legitimacy",
    "BP.7": "Legal, Regulatory, Data, and Deployability Governance",
    "BP.8": "Competitive Landscape, Positioning, and Differentiation",
    "BP.9": "Business Model, Revenue, and GTM Logic",
    "BP.10": "Validation, Behavioural Evidence, and PMF Decision Logic",
    "BP.11": "Investor Narrative, Business Plan, and Data Room Readiness",
}


def _get_suggested_parent(candidates: list[dict], text: str = "") -> dict:
    """Determine the best level-1 parent, or flag "no domain match."

    Uses an LLM call to verify the content's subject matter actually matches
    the scope of a domain — not just keyword overlap. If no domain genuinely
    fits (e.g., legal entity structure, jurisdiction, cap table, incorporation),
    returns a special "no_domain_match" result so the UI can show "flag for
    manual domain-creation review" instead of force-fitting under a wrong parent.

    Args:
        candidates: Node match candidates (unused, kept for call-site compat).
        text: The fact text to match against domain scopes.

    Returns:
        Dict with node_id and node_title of the best level-1 parent,
        OR node_id=None and no_domain_match=True when nothing fits.
    """
    if not text:
        return {"node_id": None, "node_title": "", "no_domain_match": True}

    # Build a compact domain description for the LLM
    domain_descriptions = "\n".join(
        f"- {nid}: {title}"
        for nid, title in sorted(LEVEL1_TITLES.items())
    )

    try:
        from web.handlers.llm_helper import _get_client
        import os

        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

        system_prompt = (
            "You are a strict domain classifier. Given a fact and a list of business plan domains, "
            "determine which domain this fact GENUINELY belongs to based on the domain's PRIMARY subject — "
            "not keyword overlap or tangential mentions.\n\n"
            "CRITICAL RULES:\n"
            "- The CORE SUBJECT of the fact determines the domain, not incidental mentions. "
            "A fact about incorporation structure that MENTIONS fundraising is about "
            "legal/corporate structure, NOT investor narrative.\n"
            "- Legal entity structure, incorporation, jurisdiction, corporate formation, "
            "and where-to-incorporate decisions do NOT belong under any existing domain. "
            "These are corporate governance topics outside BP.1–BP.11 scope.\n"
            "- 'Geographic Scope' (in BP.1) means product geographic targeting, NOT physical "
            "location of the company's legal entity.\n"
            "- 'Compliance' (BP.7) means PRODUCT data/privacy/regulatory compliance, NOT "
            "business entity legal choices.\n"
            "- 'Investor Narrative' (BP.11) means pitch deck and fundraise strategy, NOT "
            "corporate entity decisions that happen to affect future fundraising.\n"
            "- If the fact's PRIMARY subject does not genuinely fall within ANY domain's "
            "stated scope, return domain_id=null. Prefer null over force-fitting.\n\n"
            "Respond with ONLY a JSON object:\n"
            '{"domain_id": "<BP.X or null>", "reasoning": "<one sentence>"}'
        )

        user_message = (
            f"FACT: \"{text[:500]}\"\n\n"
            f"AVAILABLE DOMAINS:\n{domain_descriptions}\n\n"
            "Which domain does this fact GENUINELY belong to? Return the JSON."
        )

        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 100},
        )

        raw = response["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

        import json as _json
        data = _json.loads(raw)
        domain_id = data.get("domain_id")

        if domain_id and domain_id in LEVEL1_TITLES:
            return {"node_id": domain_id, "node_title": LEVEL1_TITLES[domain_id]}

        # LLM said null or invalid domain → no genuine match
        logger.info(
            "[FeedHandler] Parent suggestion: no domain match for fact. Reason: %s",
            data.get("reasoning", "unknown"),
        )
        return {"node_id": None, "node_title": "", "no_domain_match": True}

    except Exception as e:
        logger.error("[FeedHandler] Parent suggestion LLM call failed: %s", e)
        # On failure, return no_domain_match rather than guessing
        return {"node_id": None, "node_title": "", "no_domain_match": True}


def match_bp_node(text: str, top_k: int = 3) -> list[dict]:
    """Find the best-matching BP architecture node(s) for a fact.

    Uses RAG semantic similarity against embedded architecture nodes.
    Returns whatever RAG finds (may be fewer than top_k or even empty) —
    the domain-aware expansion in classify_and_match_node fills the gaps.

    Never falls back to _direct_node_match (which builds a 745-node
    embedding cache on first call, taking 2-3 minutes).

    Args:
        text: The fact text to match.
        top_k: Number of candidates to return.

    Returns:
        List of dicts with: node_id, node_title, similarity, level, purpose, parent_node.
    """
    try:
        from services.rag_service import retrieve

        chunks = retrieve(
            query=text,
            source_types=["ceo_doc"],
            top_k=top_k,
            threshold=0.3,
            metadata_filter={"layer": "bp_architecture"},
        )

        results = []
        if chunks:
            for chunk in chunks[:top_k]:
                node_id = chunk.metadata.get("node_id", chunk.section or "unknown")
                node_title = chunk.metadata.get("node_title") or ""
                if not node_title and chunk.content:
                    node_title = chunk.content[:60]
                node_details = _get_node_details(node_id)
                results.append({
                    "node_id": node_id,
                    "node_title": node_title,
                    "similarity": round(chunk.similarity, 3),
                    "level": chunk.metadata.get("level", 0),
                    "purpose": (node_details.get("purpose") or "") if node_details else "",
                    "required_output": (node_details.get("required_output") or "") if node_details else "",
                    "prohibited_claims": (node_details.get("prohibited_claims_inference_patterns") or "") if node_details else "",
                    "parent_node": (node_details.get("parent_node") or "") if node_details else "",
                })
        return results
    except Exception as e:
        logger.debug("[FeedHandler] RAG node retrieval failed: %s", e)
        return []


_node_embedding_cache: Optional[list[dict]] = None


def _get_node_embeddings() -> list[dict]:
    """Return cached node embeddings, computing them on first call."""
    global _node_embedding_cache
    if _node_embedding_cache is not None:
        return _node_embedding_cache

    from services.rag_service import embed

    nodes = _load_bp_architecture()
    if not nodes:
        return []

    import numpy as np

    cache = []
    for node in nodes:
        # bp_architecture.json has a handful of nodes with node_title/purpose
        # explicitly set to null (not just absent) — .get(key, "") does NOT
        # catch that (the default only applies when the key is missing), so
        # every field is normalized with `or ""` here, once, at the source,
        # so nothing downstream (match_bp_node, the LLM candidate block,
        # node-title search) can crash on a None from one of these nodes.
        node_title = node.get("node_title") or ""
        purpose = node.get("purpose") or ""
        required_output = node.get("required_output") or ""
        # prohibited_claims_inference_patterns is new in the richer
        # bp_architecture.json schema (added 2026-07) — it states what a
        # node must NOT be used to claim or infer, which is exactly the
        # kind of negative-constraint signal that helps the LLM classifier
        # disambiguate nodes that look similar on title/purpose alone.
        prohibited = node.get("prohibited_claims_inference_patterns") or ""
        match_text = f"{node_title}. {purpose}. {required_output}"
        embedding = np.array(embed(match_text, input_type="search_document"))
        cache.append({
            "node_id": node.get("node_id", "unknown"),
            "node_title": node_title,
            "level": node.get("level", 0),
            "purpose": purpose,
            "required_output": required_output,
            "prohibited_claims": prohibited,
            "parent_node": node.get("parent_node") or "",
            "embedding": embedding,
        })

    _node_embedding_cache = cache
    logger.info("[FeedHandler] Cached embeddings for %d architecture nodes", len(cache))
    return _node_embedding_cache


def _direct_node_match(text: str, top_k: int = 3) -> list[dict]:
    """Match text against architecture nodes using cached embedding comparison."""
    try:
        from services.rag_service import embed
    except Exception as e:
        logger.error("[FeedHandler] Cannot load embedding model: %s", e)
        return []

    node_embeddings = _get_node_embeddings()
    if not node_embeddings:
        return []

    import numpy as np
    query_vec = np.array(embed(text))
    query_norm = np.linalg.norm(query_vec)

    scored = []
    for node in node_embeddings:
        node_vec = node["embedding"]
        similarity = float(np.dot(query_vec, node_vec) / (query_norm * np.linalg.norm(node_vec)))
        scored.append({
            "node_id": node["node_id"],
            "node_title": node["node_title"],
            "similarity": round(similarity, 3),
            "level": node["level"],
            "purpose": node.get("purpose", ""),
            "required_output": node.get("required_output", ""),
            "prohibited_claims": node.get("prohibited_claims", ""),
            "parent_node": node.get("parent_node", ""),
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


# Candidate pool size handed to the LLM classifier below. Wide enough that
# the right node is almost always in the shortlist even when the raw
# embedding similarity is mediocre, narrow enough to keep the prompt (and
# cost) bounded — this is not "show Alex 3 options", it's "give the LLM
# enough real context to make one confident, specific pick".
CLASSIFY_CANDIDATE_POOL = 15


def _detect_likely_domains(text: str) -> list[str]:
    """Detect which level-1 BP domains a fact likely belongs to.

    Returns up to 2 domain IDs (e.g. ["BP.10", "BP.2"]) based on keyword
    match strength. Used by classify_and_match_node to forcibly include
    all nodes from the likely domain(s) in the candidate list — this ensures
    the correct node is present even when embedding similarity misses it
    (the root cause of BP.1.2.1 / BP.10.3.2 style misclassifications).

    Cap at 2 domains to keep the candidate list bounded (~120-180 nodes max
    including the embedding shortlist, which is within Sonnet's effective
    reasoning window for this kind of task).
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for node_id, keywords in LEVEL1_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[node_id] = score

    if not scores:
        return []

    sorted_domains = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_domains[0][1]

    if top_score >= 2:
        # Strong signal — take the top scorer plus any runner-up within 1 point
        result = [sorted_domains[0][0]]
        if len(sorted_domains) > 1 and sorted_domains[1][1] >= top_score - 1:
            result.append(sorted_domains[1][0])
        return result

    # Weak signal (all at score=1) — take top 2 by dict order. The document
    # context (Approach C) is what disambiguates in this case, not domain
    # expansion alone.
    return [d[0] for d in sorted_domains[:2]]


def _detect_likely_domains_broad(text: str) -> list[str]:
    """Like _detect_likely_domains but more inclusive — returns all domains
    with any keyword hit, sorted by score descending, up to 3.

    Used for document-context detection where we want to cast a wide net:
    the context "PMF options analysis for product-market fit" should return
    BP.10, BP.4, and BP.1 even though they all score similarly low.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for node_id, keywords in LEVEL1_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[node_id] = score

    if not scores:
        return []

    sorted_domains = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [d[0] for d in sorted_domains[:3]]


def _get_domain_nodes_ranked(
    text: str, domains: list[str], exclude_ids: set, max_inject: int = 15
) -> list[dict]:
    """Get the most relevant nodes from given domains using keyword scoring.

    Instead of using embedding similarity (which requires a 745-node embedding
    cache that takes 2-3 min to build on first call), this uses cheap keyword
    overlap scoring between the fact text and each node's title+purpose. The
    LLM classifier does the real disambiguation — this just ensures the right
    nodes are in the candidate list.

    Args:
        text: The fact text to match against.
        domains: List of domain IDs (e.g. ["BP.10", "BP.2"]).
        exclude_ids: Node IDs already in the candidate list (skip these).
        max_inject: Maximum number of domain nodes to inject.

    Returns:
        List of node dicts sorted by keyword relevance descending.
    """
    nodes = _load_bp_architecture()
    if not nodes:
        return []

    prefixes = tuple(f"{d}." for d in domains)
    domain_set = set(domains)
    text_lower = text.lower()
    text_words = set(re.findall(r"[a-z]{4,}", text_lower))

    scored = []
    for node in nodes:
        nid = node.get("node_id", "")
        if not (nid in domain_set or nid.startswith(prefixes)):
            continue
        if nid in exclude_ids:
            continue

        node_title = node.get("node_title") or ""
        purpose = node.get("purpose") or ""
        required_output = node.get("required_output") or ""
        node_text = f"{node_title} {purpose} {required_output}".lower()
        node_words = set(re.findall(r"[a-z]{4,}", node_text))

        overlap = len(text_words & node_words)

        scored.append({
            "node_id": nid,
            "node_title": node_title,
            "similarity": round(overlap * 0.1, 3),
            "level": node.get("level", 0),
            "purpose": purpose[:200],
            "required_output": required_output[:150],
            "prohibited_claims": (node.get("prohibited_claims_inference_patterns") or "")[:150],
            "parent_node": node.get("parent_node") or "",
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:max_inject]


_REQUIRED_OUTPUT_STOPWORDS = frozenset({
    "must", "should", "will", "would", "could", "that", "this", "with",
    "from", "which", "have", "been", "their", "these", "those", "about",
    "into", "over", "each", "every", "both", "more", "most", "other",
    "some", "such", "than", "also", "only", "does", "based", "under",
    "between", "through", "during", "before", "after", "include",
    "including", "across", "within", "where", "what", "when", "they",
    "them", "there", "then", "here", "just", "very", "well", "like",
    "still", "even", "however", "provides", "required", "output",
    "format", "structured", "list", "data", "information", "related",
})


def _pseudo_stem(word: str) -> str:
    """Cheap suffix stripping for keyword overlap — not a real stemmer."""
    for suffix in ("ation", "tion", "sion", "ment", "ness", "ence", "ance", "ity", "ing", "ive", "ous", "ful", "less", "able", "ible", "ally", "ily", "ised", "ized", "ist", "est", "ise", "ize", "ify", "ate", "ed", "er", "ly", "al"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _required_output_precheck(fact_text: str, required_output: str, node_details: dict = None) -> bool:
    """Fast programmatic check: does the fact share ANY key nouns with required_output?

    Extracts 4+ char words (excluding stopwords) from the node's required_output
    and checks for overlap with the fact text. If ZERO overlap, the fact almost
    certainly does not belong under this node — skip the expensive LLM validation.

    EXCEPTION: For definitional/governance nodes (Framework, Definition, Register,
    Hypothesis, Prohibited patterns), skip this check entirely. These nodes store
    the definitions themselves, not data that produces the definitions, so keyword
    overlap with required_output is not a reliable signal.

    Args:
        fact_text: The fact being classified.
        required_output: The node's required_output field.
        node_details: Optional full node dict for checking node_type/title.

    Returns:
        True if there is at least one meaningful word overlap (fact MAY fit).
        False if zero overlap (fact almost certainly does NOT fit).
    """
    if not required_output:
        return True  # No required_output to check against — permissive

    # Check if this is a definitional/governance node type
    # These nodes store definitions, not data, so keyword overlap is unreliable
    if node_details:
        node_title = (node_details.get("node_title") or "").lower()
        node_type = (node_details.get("node_type") or "").lower()

        DEFINITIONAL_PATTERNS = [
            "definition", "framework", "register", "hypothesis", "prohibited",
            "inference", "evidence framework", "assumption", "boundary",
        ]

        for pattern in DEFINITIONAL_PATTERNS:
            if pattern in node_title or pattern in node_type:
                logger.debug(
                    "[FeedHandler] Skipping precheck for definitional node: %s",
                    node_details.get("node_id")
                )
                return True  # Skip precheck for definitional nodes

    ro_lower = required_output.lower()
    fact_lower = fact_text.lower()

    ro_words = set(re.findall(r"[a-z]{4,}", ro_lower))
    ro_nouns = ro_words - _REQUIRED_OUTPUT_STOPWORDS

    if not ro_nouns:
        return True  # Only stopwords — cannot meaningfully filter

    fact_words = set(re.findall(r"[a-z]{4,}", fact_lower))

    # Direct word overlap
    if ro_nouns & fact_words:
        return True

    # Stem-based overlap (catches urgent/urgency, assume/assumption, etc.)
    ro_stems = {_pseudo_stem(w) for w in ro_nouns}
    fact_stems = {_pseudo_stem(w) for w in fact_words}
    return len(ro_stems & fact_stems) > 0


def _check_prohibition_violation(fact_text: str, node_id: str) -> tuple[bool, str]:
    """Programmatically check if fact violates node's prohibited_claims.

    This is a HARD gate that runs after LLM classification. The LLM is told
    to respect prohibitions, but sometimes picks prohibited nodes anyway with
    "high" confidence. This function enforces the rule programmatically.

    Args:
        fact_text: The fact being classified.
        node_id: The node_id the LLM picked.

    Returns:
        (violated: bool, reason: str) — True if the fact violates a prohibition,
        with a human-readable reason. False if no violation detected.
    """
    node = _get_node_details(node_id)
    if not node:
        return False, ""

    prohibited_claims = node.get("prohibited_claims_inference_patterns") or ""
    if not prohibited_claims:
        return False, ""  # No prohibitions to check

    fact_lower = fact_text.lower()

    # Define prohibition patterns and their semantic keywords
    # These are common patterns from Alex's BP architecture
    PROHIBITION_PATTERNS = [
        {
            "keywords": ["manuscript", "writing", "improve", "quality", "submission"],
            "pattern": "manuscript.*improv|writing.*assist|improve.*manuscript",
            "description": "manuscript improvement/writing assistance",
        },
        {
            "keywords": ["adopt", "need", "want", "systematic", "workflow", "require"],
            "pattern": "adopt|need.*systematic|want.*workflow|requir.*assessment",
            "description": "inferring adoption/urgency from workflow existence",
        },
        {
            "keywords": ["like", "satisfaction", "positive", "feedback", "enjoy"],
            "pattern": "researcher.*like|user.*satisfaction|positive.*feedback",
            "description": "satisfaction/liking as PMF signal",
        },
        {
            "keywords": ["email", "dean", "single", "anecdote", "one"],
            "pattern": "one.*email|single.*dean|anecdot",
            "description": "anecdotal evidence as PMF proof",
        },
    ]

    prohibited_lower = prohibited_claims.lower()

    # Check each prohibition pattern
    for pattern_def in PROHIBITION_PATTERNS:
        # First check if this prohibition is mentioned in the node's prohibited_claims
        pattern_matched = False
        for keyword in pattern_def["keywords"]:
            if keyword in prohibited_lower:
                pattern_matched = True
                break

        if not pattern_matched:
            continue  # This prohibition doesn't apply to this node

        # Now check if the fact asserts something matching this prohibition
        if re.search(pattern_def["pattern"], fact_lower, re.IGNORECASE):
            reason = (
                f"Fact asserts '{pattern_def['description']}' which node {node_id} "
                f"explicitly prohibits: '{prohibited_claims[:100]}...'"
            )
            return True, reason

    return False, ""


def classify_and_match_node(text: str, session_id: Optional[str] = None, document_context: Optional[str] = None, use_fast_model: bool = False) -> dict:
    """Classify a fact to exactly one BP architecture node, accurately.

    Three-stage:
    1. Cheap local embedding similarity narrows ~745 nodes to a shortlist
    2. Domain-aware expansion injects all nodes from the detected domain(s)
       so the correct node is always present even with mediocre embedding scores
    3. An LLM reasons over the merged shortlist to pick one specific node

    Args:
        text: The atomic fact/claim to classify.
        session_id: Optional session for live confidence stream.
        document_context: Optional 1-2 sentence summary of the larger document
            this fact was extracted from. Passed to the LLM to disambiguate
            facts that are ambiguous in isolation (Approach C).

    Returns:
        Dict with: node_id, node_title, confidence ("high"/"medium"/"low"),
        reasoning, none_fit (bool), suggested_parent (dict, only populated
        when none_fit is True — where a brand-new node would go).
    """
    if document_context:
        logger.debug(
            "[FeedHandler] Classifying with document context: fact='%s...', context='%s...'",
            text[:60], document_context[:80]
        )
    else:
        logger.debug(
            "[FeedHandler] Classifying WITHOUT document context: fact='%s...'",
            text[:60]
        )

    # --- Pre-routing: Force correct domains for explicitly labeled facts ---
    # Facts with clear prefixes like "Risk:", "Assumption:", "Constraint:"
    # get misrouted by the domain classifier. Force the correct domains.
    text_lower = text.lower().strip()
    forced_domains = None

    if text_lower.startswith("risk:") or text_lower.startswith("main risk:"):
        forced_domains = ["BP.8", "BP.9"]
    elif text_lower.startswith("assumption:") or text_lower.startswith("key assumption:"):
        forced_domains = ["BP.2", "BP.10"]
    elif text_lower.startswith("constraint:") or text_lower.startswith("limitation:"):
        forced_domains = ["BP.7", "BP.1"]
    elif text_lower.startswith("revenue") or text_lower.startswith("pricing"):
        forced_domains = ["BP.9", "BP.5"]
    elif text_lower.startswith("target market") or text_lower.startswith("target customer"):
        forced_domains = ["BP.4", "BP.3"]

    # --- Two-Stage Classification (Task #4) ---
    # Stage 1: Use LLM to pick 1-3 level-1 domains (BP.1, BP.2, etc.)
    #          This mirrors how Alex's Claude Desktop got 9/9 — domain first, then node.
    # Stage 2: Build candidate pool from those domains + embedding top-k, then LLM picks specific node.

    # Stage 1: Domain classification
    from web.handlers.llm_helper import classify_fact_to_domain

    level1_domains = _get_level1_domains()

    if forced_domains:
        selected_domain_ids = forced_domains
        logger.info(
            "[FeedHandler] Pre-routed fact to domains %s based on prefix keyword",
            forced_domains
        )
    else:
        selected_domain_ids = classify_fact_to_domain(text, level1_domains, document_context=document_context)

    if session_id and selected_domain_ids:
        emit_trace(
            session_id, "Classifier", "domain_selected",
            f"Domain(s): {', '.join(selected_domain_ids)}",
            data={"domains": selected_domain_ids, "phase": "domain_classification"},
        )

    # Stage 2: Hierarchical classification — section then leaf.
    # Instead of throwing 30+ flat nodes at the LLM, narrow progressively:
    #   Step A: Pick the best level-2 section(s) from selected domains (~5-10 candidates)
    #   Step B: Pick the specific leaf node from that section's children (~5-10 candidates)
    # This makes each LLM call a focused 5-10 way choice instead of a noisy 30-way one.

    from web.handlers.llm_helper import classify_fact_to_node

    all_nodes = _load_bp_architecture()

    # Step A: Get level-2 sections from selected domains
    section_candidates = []
    for domain_id in selected_domain_ids:
        prefix = f"{domain_id}."
        for n in all_nodes:
            nid = n.get("node_id", "")
            if nid.startswith(prefix) and nid.count(".") == 2:
                section_candidates.append({
                    "node_id": nid,
                    "node_title": n.get("node_title") or "",
                    "similarity": 0.0,
                    "level": n.get("level", 2),
                    "purpose": (n.get("purpose") or "")[:250],
                    "required_output": (n.get("required_output") or "")[:150],
                    "prohibited_claims": (n.get("prohibited_claims_inference_patterns") or "")[:150],
                    "parent_node": n.get("parent_node") or "",
                })

    # Also include embedding top-k as a safety net for cross-domain edge cases
    embedding_candidates = match_bp_node(text, top_k=CLASSIFY_CANDIDATE_POOL)

    if not section_candidates and not embedding_candidates:
        parent = _get_suggested_parent([], text=text)
        return {
            "node_id": None, "node_title": "", "confidence": "low",
            "reasoning": "No architecture nodes available to match against.",
            "none_fit": True, "suggested_parent": parent,
        }

    if session_id and section_candidates:
        sect_names = [f"{c['node_id']}" for c in section_candidates[:5]]
        emit_trace(
            session_id, "Classifier", "considering",
            f"Sections: {', '.join(sect_names)}...",
            data={"candidates": sect_names, "phase": "section_selection"},
        )

    # Pick the best section (or the embedding candidate if it's clearly better)
    # Merge sections + embedding candidates for the first LLM call
    step_a_candidates = []
    seen_ids = set()
    for c in section_candidates:
        if c["node_id"] not in seen_ids:
            seen_ids.add(c["node_id"])
            step_a_candidates.append(c)
    for c in embedding_candidates:
        if c.get("node_id") and c["node_id"] not in seen_ids:
            seen_ids.add(c["node_id"])
            step_a_candidates.append(c)

    # Cap at 20 for Step A
    step_a_candidates = step_a_candidates[:20]

    try:
        step_a_result = classify_fact_to_node(
            text, step_a_candidates, document_context=document_context,
            use_fast_model=use_fast_model,
        )
    except Exception as e:
        logger.error("[FeedHandler] Step A classification failed: %s", e)
        if embedding_candidates:
            top = embedding_candidates[0]
            result = {
                "node_id": top["node_id"], "node_title": top.get("node_title", ""),
                "confidence": "low",
                "reasoning": "LLM classification unavailable — used top embedding match.",
                "none_fit": False,
            }
        else:
            result = {
                "node_id": None, "node_title": "", "confidence": "low",
                "reasoning": "Classification failed.", "none_fit": True,
                "suggested_parent": _get_suggested_parent([], text=text),
            }
        step_a_result = result

    # Step B: If Step A picked a section (non-leaf), drill into its children
    picked_id = step_a_result.get("node_id")
    result = step_a_result

    if picked_id and not step_a_result.get("none_fit"):
        # Check if this node has children (i.e., it's a section not a leaf)
        child_prefix = f"{picked_id}."
        children = [
            {
                "node_id": n.get("node_id"),
                "node_title": n.get("node_title") or "",
                "similarity": 0.0,
                "level": n.get("level", 0),
                "purpose": (n.get("purpose") or "")[:250],
                "required_output": (n.get("required_output") or "")[:150],
                "prohibited_claims": (n.get("prohibited_claims_inference_patterns") or "")[:150],
                "parent_node": n.get("parent_node") or "",
            }
            for n in all_nodes
            if n.get("node_id", "").startswith(child_prefix)
            and n.get("node_id", "").count(".") == picked_id.count(".") + 1
        ]

        if children:
            # Include the parent itself as an option (fact might be general enough
            # to stay at the section level)
            parent_node_data = next(
                (c for c in step_a_candidates if c.get("node_id") == picked_id), None
            )
            step_b_candidates = []
            if parent_node_data:
                step_b_candidates.append(parent_node_data)
            step_b_candidates.extend(children)

            if session_id:
                child_names = [c["node_id"] for c in children[:5]]
                emit_trace(
                    session_id, "Classifier", "narrowing",
                    f"Narrowing within {picked_id}: {', '.join(child_names)}...",
                    data={"parent": picked_id, "children": child_names, "phase": "leaf_selection"},
                )

            try:
                step_b_result = classify_fact_to_node(
                    text, step_b_candidates, document_context=document_context,
                    use_fast_model=use_fast_model,
                )
                if step_b_result.get("none_fit") or not step_b_result.get("node_id"):
                    # Step B couldn't find a fitting child — keep the section-level
                    # result from Step A (the fact belongs at the parent level)
                    logger.info(
                        "[FeedHandler] Step B returned none_fit — keeping Step A result (%s)",
                        picked_id,
                    )
                    result = step_a_result
                else:
                    result = step_b_result
            except Exception as e:
                logger.error("[FeedHandler] Step B classification failed, using Step A result: %s", e)
                result = step_a_result

    if result.get("none_fit") or not result.get("node_id"):
        result["suggested_parent"] = _get_suggested_parent(step_a_candidates, text=text)
    else:
        node_details = _get_node_details(result["node_id"]) or {}
        result["purpose"] = node_details.get("purpose") or ""
        result["level"] = node_details.get("level", 0)

        # Track original confidence before any demotions for Fix #2
        original_confidence = result.get("confidence")

        # --- MANDATORY: Hard prohibition gate (runs for ALL classifications) ---
        # Check if the fact violates the node's prohibited_claims before accepting
        # the LLM's classification. This catches cases where the LLM ignores
        # prohibitions and picks a node with "high" confidence anyway.
        violation_detected, violation_reason = _check_prohibition_violation(text, result["node_id"])

        if violation_detected:
            # Hard reject: set none_fit=True and clear the node_id
            result["node_id"] = None
            result["node_title"] = ""
            result["confidence"] = "low"
            result["none_fit"] = True
            result["reasoning"] = f"REJECTED: {violation_reason}"
            result["suggested_parent"] = _get_suggested_parent(step_a_candidates, text=text)
            if session_id:
                emit_trace(
                    session_id, "Classifier", "rejected",
                    f"Prohibition violation detected — rejecting classification",
                    data={"reason": violation_reason, "phase": "prohibition_gate"},
                )
            logger.info(
                "[FeedHandler] Prohibition gate REJECTED classification: %s",
                violation_reason,
            )
        # --- Hybrid validation (replaces old keyword-stem prohibition gate) ---
        # For high-confidence matches from domains where Haiku validation is
        # reliable, run a two-stage check. Skip for domains where Haiku
        # consistently gives false-positive rejections.
        elif result.get("confidence") == "high":
            node_id_for_val = result.get("node_id") or ""
            skip_validation = (
                node_id_for_val.startswith("BP.2.3") or  # Urgency
                node_id_for_val.startswith("BP.2.5") or  # Hypothesis
                node_id_for_val.startswith("BP.10.3") or  # PMF definitions
                node_id_for_val.startswith("BP.8.7") or  # Incumbent threats
                node_id_for_val.startswith("BP.8.8")  # Category compression
            )
            if not skip_validation:
                required_output = node_details.get("required_output") or ""
                precheck_passed = _required_output_precheck(text, required_output, node_details)

                if not precheck_passed:
                    result["confidence"] = "medium"
                    result["reasoning"] = (
                        f"Demoted: fact has no keyword overlap with node's required_output "
                        f"({result['node_id']}). Needs human review."
                    )
                    logger.info(
                        "[FeedHandler] Required-output precheck failed: demoted high -> medium for %s",
                        result["node_id"],
                    )
                else:
                    if session_id:
                        emit_trace(
                            session_id, "Classifier", "validating",
                            f"Validating placement against node constraints...",
                            data={"node_id": result["node_id"], "phase": "validation"},
                        )
                    try:
                        from web.handlers.llm_helper import validate_classification

                        validation = validate_classification(text, node_details)

                        if validation["prohibition_violated"] and not validation["required_output_match"]:
                            result["node_id"] = None
                            result["node_title"] = ""
                            result["confidence"] = "low"
                            result["none_fit"] = True
                            result["reasoning"] = (
                                f"Validation rejected: prohibition violated AND required_output mismatch. "
                                f"{validation['reasoning']}"
                            )
                            result["suggested_parent"] = _get_suggested_parent(step_a_candidates, text=text)
                            if session_id:
                                emit_trace(
                                    session_id, "Classifier", "validated",
                                    f"Validation failed — prohibition violated and output mismatch",
                                    data={"passed": False, "phase": "validation"},
                                )
                            logger.info(
                                "[FeedHandler] Validation double-reject: forced none_fit for fact"
                            )
                        elif validation["prohibition_violated"]:
                            result["confidence"] = "low"
                            result["reasoning"] = (
                                f"Demoted: fact violates node's prohibition "
                                f"({result['node_id']}). {validation['reasoning']}"
                            )
                            if session_id:
                                emit_trace(
                                    session_id, "Classifier", "validated",
                                    f"Validation failed — prohibition violated ({result['node_id']})",
                                    data={"node_id": result["node_id"], "passed": False, "phase": "validation"},
                                )
                            logger.info(
                                "[FeedHandler] Prohibition validated: demoted high -> low for %s",
                                result["node_id"],
                            )
                        elif not validation["required_output_match"]:
                            result["confidence"] = "medium"
                            result["reasoning"] = (
                                f"Demoted: fact doesn't produce node's required_output "
                                f"({result['node_id']}). {validation['reasoning']}"
                            )
                            if session_id:
                                emit_trace(
                                    session_id, "Classifier", "validated",
                                    f"Validation partial — required output mismatch ({result['node_id']})",
                                    data={"node_id": result["node_id"], "passed": False, "phase": "validation"},
                                )
                            logger.info(
                                "[FeedHandler] Required-output mismatch: demoted high -> medium for %s",
                                result["node_id"],
                            )
                        else:
                            result["validated"] = True
                            if session_id:
                                emit_trace(
                                    session_id, "Classifier", "validated",
                                    f"Validated ✓ — placement confirmed for {result['node_id']}",
                                    data={"node_id": result["node_id"], "passed": True, "phase": "validation"},
                                )
                    except Exception as e:
                        logger.error(
                            "[FeedHandler] LLM validation failed (keeping original classification): %s", e
                        )

        # Strict none_fit enforcement REMOVED — the keyword-overlap gate had a
        # high false-positive rate across a 747-node architecture where most valid
        # placements don't share surface keywords with required_output. The LLM's
        # own none_fit judgment (informed by purpose + prohibited_claims context in
        # the prompt) is more reliable. The prohibition gate above already catches
        # truly invalid placements.

    if session_id:
        if result.get("node_id"):
            emit_trace(
                session_id, "Classifier", "locked",
                f"Locked → {result['node_id']} ({result.get('node_title', '')}) [{result.get('confidence', '?')}]",
                data={"node_id": result["node_id"], "confidence": result.get("confidence"), "phase": "llm_decision"},
            )
        else:
            emit_trace(
                session_id, "Classifier", "no_match",
                "No node matched — will ask Alex to place it.",
                data={"phase": "no_match"},
            )

    return result


def _get_redis():
    """Get the Redis client."""
    from memory.redis_client import redis_client
    return redis_client


def _pending_key(session_id: str) -> str:
    """Redis key for pending fact awaiting approval."""
    return f"feed_pending:{session_id}"


def _state_key(session_id: str) -> str:
    """Redis key for feed handler state."""
    return f"feed_state:{session_id}"


def get_feed_state(session_id: str) -> Optional[str]:
    """Get the current feed handler state for a session.

    Returns:
        State string or None if no special state is active.
    """
    try:
        r = _get_redis()
        value = r.get(_state_key(session_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return value
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting feed state: %s", e)
        return None


def set_feed_state(session_id: str, state: Optional[str]) -> None:
    """Set the feed handler state for a session.

    Args:
        session_id: Session identifier.
        state: State string or None to clear.
    """
    try:
        r = _get_redis()
        if state is None:
            r.delete(_state_key(session_id))
        else:
            r.set(_state_key(session_id), state, ex=PENDING_FACT_TTL)
    except Exception as e:
        logger.error("[FeedHandler] Redis error setting feed state: %s", e)


def store_pending_fact(session_id: str, fact_data: dict) -> None:
    """Store a pending fact in Redis awaiting Alex's approval.

    Args:
        session_id: Session identifier.
        fact_data: Full fact dict (text, content_type, epistemic_status, node, etc).
    """
    try:
        r = _get_redis()
        r.set(_pending_key(session_id), json.dumps(fact_data), ex=PENDING_FACT_TTL)
    except Exception as e:
        logger.error("[FeedHandler] Redis error storing pending fact: %s", e)


def get_pending_fact(session_id: str) -> Optional[dict]:
    """Retrieve the pending fact from Redis.

    Returns:
        The pending fact dict or None.
    """
    try:
        r = _get_redis()
        raw = r.get(_pending_key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting pending fact: %s", e)
        return None


def clear_pending_fact(session_id: str) -> None:
    """Remove the pending fact and clear approval state."""
    try:
        r = _get_redis()
        r.delete(_pending_key(session_id))
    except Exception as e:
        logger.error("[FeedHandler] Redis error clearing pending fact: %s", e)


def _handle_duplicate_confirm(response_text: str, session_id: str) -> dict:
    """Handle Alex's response to the duplicate detection prompt.

    Args:
        response_text: 'yes' to proceed with storage, 'skip' to discard.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    text_lower = response_text.strip().lower()

    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "expired",
            "response_text": "Duplicate confirmation expired. Please re-submit.",
        }

    original_text = pending.get("original_text", "")
    if not original_text:
        clear_pending_fact(session_id)
        set_feed_state(session_id, None)
        return {
            "action": "expired",
            "response_text": "Original text lost. Please re-submit.",
        }

    if text_lower in ("yes", "y", "ok", "store", "1"):
        clear_pending_fact(session_id)
        set_feed_state(session_id, None)
        return handle_raw_text(original_text, session_id=session_id, _skip_dupe_check=True)

    clear_pending_fact(session_id)
    set_feed_state(session_id, None)
    return {
        "action": "skipped",
        "response_text": "Duplicate skipped. Nothing was stored.",
    }


# Confidence bar for auto-filing a fact without asking Alex first. Per
# explicit direction, only the classifier's own "high" tier qualifies —
# medium/low confidence and no-fit all still go through confirm/adjust/skip.
# This is intentionally strict: once a fact auto-files, Alex never sees it
# before it's written, so there's no human safety net left to catch a wrong
# pick the way there is for anything routed through the review flow.
AUTO_FILE_CONFIDENCE = "high"

# --- Confidence Tier Routing ---
# Four tiers map existing classifier signals to UX decisions:
#   auto_file:         high + validated → store immediately, no prompt
#   auto_file_flagged: high + not validated (or medium promoted by source) → store + flag
#   soft_ask:          medium → lean-yes prompt, auto-file after 30s with needs_review
#   ask:               low / none_fit → full review flow

QUARANTINE_TTL = 60 * 60 * 24 * 7  # 7 days
QUARANTINE_KEY_PREFIX = "quarantine"
QUARANTINE_COUNT_KEY_PREFIX = "quarantine_count"


def _determine_tier(classification: dict, source: str = "alex_direct") -> str:
    """Map classifier signals to one of four confidence tiers.

    Args:
        classification: Result dict from classify_and_match_node().
        source: Origin of the fact — "alex_direct" for typed input, "document" for uploads.

    Returns:
        One of: "auto_file", "auto_file_flagged", "soft_ask", "ask".
    """
    none_fit = classification.get("none_fit", True) or not classification.get("node_id")
    confidence = classification.get("confidence", "low")
    validated = classification.get("validated", False)

    if none_fit or confidence == "low":
        return "ask"

    if confidence == "high":
        if validated:
            return "auto_file"
        return "auto_file_flagged"

    if confidence == "medium":
        if source == "alex_direct":
            return "auto_file_flagged"
        return "soft_ask"

    return "ask"


def _build_audit_metadata(
    classification: dict,
    source: str,
    tier: str,
) -> dict:
    """Build audit trail metadata from existing classifier signals.

    Returns:
        Dict of audit fields to merge into chunk metadata.
    """
    return {
        "classifier_confidence": classification.get("confidence", "low"),
        "classifier_validated": classification.get("validated", False),
        "classifier_similarity": classification.get("similarity", 0.0),
        "source_authority": source,
        "tier_decision": tier,
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }


def _quarantine_key(ceo_id: str) -> str:
    """Redis key for quarantine list."""
    return f"{QUARANTINE_KEY_PREFIX}:{ceo_id}"


def _quarantine_count_key(ceo_id: str) -> str:
    """Redis key for quarantine count."""
    return f"{QUARANTINE_COUNT_KEY_PREFIX}:{ceo_id}"


def add_to_quarantine(session_id: str, fact_data: dict) -> None:
    """Add a fact to the quarantine queue in Redis.

    Args:
        session_id: Current session (used as ceo_id proxy).
        fact_data: Full fact dict to quarantine.
    """
    try:
        r = _get_redis()
        key = _quarantine_key(session_id)
        entry = json.dumps({
            **fact_data,
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
        })
        r.rpush(key, entry)
        r.expire(key, QUARANTINE_TTL)
        count_key = _quarantine_count_key(session_id)
        r.incr(count_key)
        r.expire(count_key, QUARANTINE_TTL)
    except Exception as e:
        logger.error("[FeedHandler] Redis error adding to quarantine: %s", e)


def get_quarantine_count(session_id: str) -> int:
    """Get current quarantine count for a session."""
    try:
        r = _get_redis()
        val = r.get(_quarantine_count_key(session_id))
        if val is None:
            return 0
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        return int(val)
    except Exception:
        return 0


def get_quarantine_items(session_id: str) -> list[dict]:
    """Get all quarantined facts for a session.

    Returns:
        List of fact dicts with quarantine metadata.
    """
    try:
        r = _get_redis()
        key = _quarantine_key(session_id)
        raw_items = r.lrange(key, 0, -1)
        if not raw_items:
            return []
        items = []
        for raw in raw_items:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            items.append(json.loads(raw))
        return items
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting quarantine items: %s", e)
        return []


def resolve_quarantine_item(session_id: str, index: int, action: str) -> dict:
    """Resolve a quarantined fact by index.

    Args:
        session_id: Session/CEO ID.
        index: 0-based index into the quarantine list.
        action: "approve", "skip", or "adjust".

    Returns:
        Dict with action result.
    """
    try:
        r = _get_redis()
        key = _quarantine_key(session_id)
        raw_items = r.lrange(key, 0, -1)
        if not raw_items or index >= len(raw_items):
            return {"action": "error", "response_text": "Item not found in quarantine."}

        raw = raw_items[index]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        fact_data = json.loads(raw)

        if action == "skip":
            r.lrem(key, 1, raw_items[index])
            count_key = _quarantine_count_key(session_id)
            r.decr(count_key)
            return {"action": "skipped", "response_text": "Quarantined fact discarded."}

        if action == "approve":
            classification = fact_data.get("classification", {})
            node_id = classification.get("node_id")
            node_title = classification.get("node_title", "")
            if node_id:
                result = _store_fact_now(
                    verbatim=fact_data["verbatim_text"],
                    content_type=fact_data.get("content_type", "fact"),
                    epistemic_status=fact_data.get("epistemic_status", "INFERRED"),
                    node_id=node_id,
                    node_title=node_title,
                    session_id=session_id,
                    content_confidence=fact_data.get("content_confidence", 0.5),
                    node_confidence=classification.get("confidence"),
                )
                r.lrem(key, 1, raw_items[index])
                count_key = _quarantine_count_key(session_id)
                r.decr(count_key)
                if result["status"] == "stored":
                    return {"action": "stored", "response_text": f"Stored → {node_id} ({node_title})"}
                return {"action": result["status"], "response_text": f"Storage result: {result['status']}"}

        return {"action": "error", "response_text": "Use 'approve' or 'skip'."}
    except Exception as e:
        logger.error("[FeedHandler] Quarantine resolution error: %s", e)
        return {"action": "error", "response_text": f"Error: {e}"}


def _classify_one_fact(
    fact_text: str,
    source_format: str,
    inferred_status: str = "INFERRED",
    session_id: Optional[str] = None,
    document_context: Optional[str] = None,
    use_fast_model: bool = False,
) -> dict:
    """Build a complete, classified fact_data dict for one atomic fact.

    Shared by handle_raw_text (classifying a whole batch up front, to decide
    what auto-files vs. what needs Alex's input) and _process_next_fact
    (pulling the next already-classified fact off the review queue), so the
    two never end up building fact_data in slightly different shapes.
    """
    content_classification = classify_content_type(fact_text)
    content_type = content_classification["content_type"]
    epistemic_status = EPISTEMIC_STATUS_BY_CONTENT_TYPE.get(content_type, inferred_status)
    classification = classify_and_match_node(
        fact_text, session_id=session_id, document_context=document_context, use_fast_model=use_fast_model
    )

    return {
        "verbatim_text": fact_text,
        "content_type": content_type,
        "content_confidence": content_classification["confidence"],
        "epistemic_status": epistemic_status,
        "classification": classification,
        "source_format": source_format,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


def _is_strong_match(classification: dict) -> bool:
    """True if this classification is confident enough to auto-file.

    Legacy gate kept for backward compatibility with batch document uploads.
    The chat flow now uses _determine_tier() for finer-grained routing.
    """
    return (
        not classification.get("none_fit", True)
        and bool(classification.get("node_id"))
        and classification.get("confidence") == AUTO_FILE_CONFIDENCE
    )


def _format_auto_file_table(results: list[dict]) -> str:
    """Format auto-filed facts as a markdown table Alex can see at a glance.

    web/static/index.html loads marked.js and renders chat text through
    marked.parse(text, {breaks: true}), which supports real markdown
    tables — so this builds a `| Node | Status | Confidence | Data |` table
    rather than hand-aligned plain text. This is the "show in a table
    that the raw data which matches to these nodes and i have
    automatically updated to that node" piece of the request.

    The Confidence column distinguishes items that passed LLM validation
    (showing "verified") from direct high-confidence matches (showing "high").

    Stored facts get the table. Duplicates and errors get a short note
    underneath instead of a row each, so a failure or a dupe skip is
    still visible but doesn't clutter the main table with non-writes.

    Args:
        results: List of dicts with verbatim_text, node_id, node_title,
            epistemic_status, status ("stored"/"duplicate"/"error"),
            confidence ("high"/"medium"/"low"), validated (bool).

    Returns:
        Markdown-formatted summary string.
    """
    stored = [r for r in results if r["status"] == "stored"]
    duplicates = [r for r in results if r["status"] == "duplicate"]
    errors = [r for r in results if r["status"] == "error"]

    lines = []
    if stored:
        lines.append(f"**Auto-filed {len(stored)} fact(s):**")
        lines.append("")
        lines.append("| Node | Status | Confidence | Data |")
        lines.append("|------|--------|------------|------|")
        for r in stored:
            node = f"{r['node_id']} — {r['node_title']}" if r.get("node_id") else "Unmatched"
            text = r["verbatim_text"].replace("|", "\\|").replace("\n", " ")
            if len(text) > 120:
                text = text[:117] + "..."
            tier = r.get("tier", "")
            if r.get("validated"):
                conf_indicator = "✓ verified"
            elif tier == "auto_file_flagged":
                conf_indicator = "flagged"
            elif r.get("confidence") == "high":
                conf_indicator = "high"
            else:
                conf_indicator = r.get("confidence", "?")
            lines.append(f"| {node} | [{r['epistemic_status']}] | {conf_indicator} | {text} |")

    if duplicates:
        lines.append("")
        lines.append(f"_{len(duplicates)} fact(s) skipped — already in the knowledge base as a near-exact duplicate._")

    if errors:
        lines.append("")
        lines.append(f"_{len(errors)} fact(s) failed to store — check logs, nothing was silently dropped._")

    return "\n".join(lines)


# Threshold: only generate a document context summary when the input is long
# enough that individual facts are likely ambiguous in isolation. A 2-sentence
# input where facts are already self-contained doesn't need it.
_CONTEXT_MIN_LENGTH = 300


def _generate_document_context(text: str) -> Optional[str]:
    """Generate a 1-2 sentence summary of a longer input for classification context.

    When a large block of text is split into atomic facts, individual sentences
    lose the context of what the whole document is about — "Job: Improve
    manuscript quality" means something very different in a PMF analysis vs.
    a product feature spec. This summary is passed alongside each fact to the
    LLM classifier so it can disambiguate.

    Uses Haiku for speed/cost — this is a one-shot summary, not a reasoning task.

    Returns:
        A 1-2 sentence context string, or None if the input is too short to
        need context or if the LLM call fails.
    """
    if len(text) < _CONTEXT_MIN_LENGTH:
        return None

    try:
        from web.handlers.llm_helper import _get_client
        import os

        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

        response = client.converse(
            modelId=model_id,
            system=[{"text": (
                "Summarize the topic and purpose of this text in exactly 1-2 sentences. "
                "Focus on WHAT the document is analyzing (e.g. 'PMF options for a SaaS product', "
                "'competitor positioning analysis', 'pricing model evaluation'). "
                "Do NOT summarize the content itself — just name the topic and analytical frame."
            )}],
            messages=[{"role": "user", "content": [{"text": text[:2000]}]}],
            inferenceConfig={"maxTokens": 80},
        )

        summary = response["output"]["message"]["content"][0]["text"].strip()
        logger.debug("[FeedHandler] Document context generated: %s", summary[:100])
        return summary

    except Exception as e:
        logger.debug("[FeedHandler] Document context generation failed (non-fatal): %s", e)
        return None


def handle_raw_text(
    text: str,
    session_id: Optional[str] = None,
    _skip_dupe_check: bool = False,
) -> dict:
    """Process raw text: split into facts, classify all of them, auto-file
    the strong matches immediately, and only ask Alex about the rest.

    This replaces the old "ask about every single fact, one at a time"
    flow. What Alex actually wants: raw data should get automatically
    divided into the right node when the match is genuinely strong, with a
    summary of what happened afterward — and only surface a decision to
    Alex when a fact doesn't clearly belong anywhere (skip / adjust / name
    a new node). Every auto-filed fact still shows up in the summary table
    below and in Stored Data, so nothing happens invisibly — Alex just
    isn't blocked confirming things the classifier is already confident
    about.

    Args:
        text: Raw text from Alex.
        session_id: Current session ID.
        _skip_dupe_check: Internal flag to bypass duplicate detection on re-entry.

    Returns:
        Dict with: action, response_text, auto_filed_count, review_count.
    """
    if not _skip_dupe_check:
        try:
            from services.rag_service import retrieve as rag_retrieve

            dupes = rag_retrieve(query=text, top_k=1, threshold=0.95)
            if dupes and dupes[0].similarity > 0.95:
                if session_id:
                    store_pending_fact(session_id, {"original_text": text})
                    set_feed_state(session_id, "FEED_AWAITING_DUPLICATE_CONFIRM")
                return {
                    "action": "duplicate_detected",
                    "response_text": (
                        "This looks like something already in the knowledge base. "
                        "Want to store it again anyway? [yes/skip]"
                    ),
                }
        except Exception as e:
            logger.debug("[FeedHandler] Duplicate check failed (non-fatal): %s", e)

    try:
        if session_id:
            emit_trace(session_id, "Feed", "splitting", "Splitting input into atomic facts...")

        fmt = detect_format(text)
        raw_facts = split_into_atomic_facts(text, fmt)

        if not raw_facts:
            return {
                "action": "no_facts",
                "response_text": "No extractable facts found in that input. Try rephrasing or adding more detail.",
            }

        # --- Approach C: Generate document context for disambiguation ---
        # One cheap Haiku call summarizes the topic of the whole input. This
        # context travels with every fact to the classifier so ambiguous
        # sentences like "Job: Improve manuscript quality" are understood in
        # their original frame (PMF analysis, not writing-assistance).
        document_context = None
        if len(raw_facts) > 1:
            if session_id:
                emit_trace(session_id, "Feed", "summarizing", "Understanding document context...")
            document_context = _generate_document_context(text)

        if session_id:
            emit_trace(
                session_id, "Feed", "classifying",
                f"Classifying {len(raw_facts)} fact(s) against architecture...",
                {"fact_count": len(raw_facts)},
            )

        # Classify the whole batch up front — this is what decides auto-file
        # vs. needs-review for every fact before anything is written.
        # Use Haiku for batches >3 facts — it's 10x faster per call. With
        # document context (Approach C) and the prohibition gate (Approach A)
        # as safety nets, Haiku's accuracy is sufficient for the initial pass;
        # anything uncertain still goes to human review. Single facts (or very
        # small batches) use Sonnet for maximum accuracy.
        use_fast = len(raw_facts) > 3
        classified = []
        for i, f in enumerate(raw_facts):
            classified.append(
                _classify_one_fact(
                    f["text"], fmt, f.get("inferred_status", "INFERRED"),
                    session_id=session_id, document_context=document_context,
                    use_fast_model=use_fast,
                )
            )
            if session_id and len(raw_facts) > 3 and (i + 1) % max(1, len(raw_facts) // 4) == 0:
                emit_trace(
                    session_id, "Feed", "classifying_progress",
                    f"Classified {i + 1}/{len(raw_facts)} facts...",
                    {"done": i + 1, "total": len(raw_facts)},
                )

        # --- Tier-based routing ---
        # Classify each fact into one of four tiers using existing signals.
        # Source is "alex_direct" for chat input (Alex typed it himself).
        source = "alex_direct"
        tiered = []
        for fact_data in classified:
            tier = _determine_tier(fact_data["classification"], source=source)
            tiered.append({"fact": fact_data, "tier": tier})

        auto_file_batch = [t for t in tiered if t["tier"] == "auto_file"]
        flagged_batch = [t for t in tiered if t["tier"] == "auto_file_flagged"]
        soft_ask_batch = [t for t in tiered if t["tier"] == "soft_ask"]
        ask_batch = [t for t in tiered if t["tier"] == "ask"]

        if session_id and (auto_file_batch or flagged_batch):
            emit_trace(
                session_id, "Feed", "auto_filing",
                f"Auto-filing {len(auto_file_batch) + len(flagged_batch)} fact(s) "
                f"({len(auto_file_batch)} high, {len(flagged_batch)} flagged)...",
                {"auto": len(auto_file_batch), "flagged": len(flagged_batch),
                 "soft_ask": len(soft_ask_batch), "ask": len(ask_batch)},
            )

        auto_results = []

        # Tier 1: auto_file — store immediately, no flag
        for item in auto_file_batch:
            fact_data = item["fact"]
            classification = fact_data["classification"]
            node_id = classification["node_id"]
            node_title = classification.get("node_title", "")
            audit = _build_audit_metadata(classification, source, "auto_file")
            result = _store_fact_now(
                verbatim=fact_data["verbatim_text"],
                content_type=fact_data["content_type"],
                epistemic_status=fact_data["epistemic_status"],
                node_id=node_id,
                node_title=node_title,
                session_id=session_id,
                content_confidence=fact_data["content_confidence"],
                node_confidence=classification.get("confidence"),
                extra_metadata=audit,
            )
            auto_results.append({
                "verbatim_text": fact_data["verbatim_text"],
                "node_id": node_id,
                "node_title": node_title,
                "epistemic_status": fact_data["epistemic_status"],
                "status": result["status"],
                "confidence": classification.get("confidence", "high"),
                "validated": classification.get("validated", False),
                "tier": "auto_file",
                "audit": audit,
            })

        # Tier 2: auto_file_flagged — store with needs_review=True
        for item in flagged_batch:
            fact_data = item["fact"]
            classification = fact_data["classification"]
            node_id = classification["node_id"]
            node_title = classification.get("node_title", "")
            audit = _build_audit_metadata(classification, source, "auto_file_flagged")
            audit["needs_review"] = True
            result = _store_fact_now(
                verbatim=fact_data["verbatim_text"],
                content_type=fact_data["content_type"],
                epistemic_status=fact_data["epistemic_status"],
                node_id=node_id,
                node_title=node_title,
                session_id=session_id,
                content_confidence=fact_data["content_confidence"],
                node_confidence=classification.get("confidence"),
                extra_metadata=audit,
            )
            auto_results.append({
                "verbatim_text": fact_data["verbatim_text"],
                "node_id": node_id,
                "node_title": node_title,
                "epistemic_status": fact_data["epistemic_status"],
                "status": result["status"],
                "confidence": classification.get("confidence", "high"),
                "validated": False,
                "tier": "auto_file_flagged",
                "audit": audit,
            })

        if auto_results and session_id:
            stored_facts = [r for r in auto_results if r["status"] == "stored"]
            if stored_facts:
                from tools.trace_emitter import emit_classification
                emit_classification(session_id, stored_facts)

        response_parts = []
        if auto_results:
            response_parts.append(_format_auto_file_table(auto_results))

        # Tier 3: soft_ask — present with a lean-yes prompt
        # Combined with Tier 4 (ask) into the review queue, but soft_ask
        # facts get a friendlier prompt and auto-file after timeout.
        review_batch = []
        for item in soft_ask_batch:
            fact_data = item["fact"]
            fact_data["tier"] = "soft_ask"
            review_batch.append(fact_data)
        for item in ask_batch:
            fact_data = item["fact"]
            fact_data["tier"] = "ask"
            review_batch.append(fact_data)

        if review_batch:
            first = review_batch[0]
            first["remaining_facts"] = review_batch[1:]
            if session_id:
                store_pending_fact(session_id, first)
                set_feed_state(session_id, "FEED_AWAITING_APPROVAL")
            review_text = _format_review_block(first, remaining=len(review_batch) - 1)
            response_parts.append(review_text)
        elif session_id:
            set_feed_state(session_id, None)

        return {
            "action": "auto_filed_with_review" if review_batch else "auto_filed",
            "response_text": "\n\n---\n\n".join(response_parts) if response_parts else "Nothing extractable was found in that input.",
            "auto_filed_count": len(auto_results),
            "review_count": len(review_batch),
        }
    except Exception as e:
        logger.error("[FeedHandler] Error in handle_raw_text: %s", e)
        return {
            "action": "error",
            "response_text": f"Error processing input: {e}",
        }


def _normalize_menu_reply(text: str) -> str:
    """Normalize a menu reply so pasting the literal option text works too.

    Alex's actual reported bug: typing "[skip] — discard this fact" (copy-
    pasted straight from the menu) didn't match the exact-string checks
    that only recognized bare "skip", so the system just re-displayed the
    same prompt with no explanation. This strips a leading bracketed token
    ("[skip]" -> "skip", "[2]" -> "2") so pasting the option text works
    identically to typing just the short form.
    """
    t = text.strip().lower()
    m = re.match(r"^\[([^\]]+)\]", t)
    if m:
        return m.group(1).strip()
    return t


SKIP_WORDS = ("skip", "no", "drop", "cancel", "discard", "discard this fact", "n")
CONFIRM_WORDS = ("confirm", "approve", "yes", "y", "ok", "confirmed", "1", "store here", "store", "create new node here")
ADJUST_WORDS = ("adjust", "2", "change node", "different node", "pick a different node")


def handle_approval_response(response_text: str, session_id: str) -> dict:
    """Handle Alex's response to a pending fact review.

    Every fact now gets exactly the same three options regardless of match
    confidence: confirm/create, adjust, skip. (Previously this branched
    into three different menus with three different option sets depending
    on similarity thresholds — which is exactly why "[2] Adjust — pick a
    different node" showed up on some prompts but not others, the
    inconsistency Alex reported.)

    Args:
        response_text: Alex's reply.
        session_id: Current session ID.

    Returns:
        Dict with: action, response_text, and any follow-up state.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact to approve. The review may have expired. Please re-submit your data.",
        }

    text_lower = _normalize_menu_reply(response_text)
    classification = pending.get("classification", {})
    none_fit = classification.get("none_fit", True) or not classification.get("node_id")

    if text_lower in SKIP_WORDS:
        clear_pending_fact(session_id)
        remaining = pending.get("remaining_facts", [])
        if remaining:
            set_feed_state(session_id, None)
            return _process_next_fact(remaining, session_id)
        set_feed_state(session_id, None)
        return {
            "action": "skipped",
            "response_text": "Fact skipped. Nothing was stored.",
        }

    if text_lower in ADJUST_WORDS:
        set_feed_state(session_id, "FEED_AWAITING_NODE_SELECTION")
        return {
            "action": "awaiting_node_selection",
            "response_text": (
                "Type the node ID directly (e.g. BP.1.1.3), or part of a node's title to search for it."
            ),
        }

    if text_lower in CONFIRM_WORDS or (none_fit and text_lower in ("1", "create", "create new node", "create here")):
        if none_fit:
            suggested_parent = classification.get("suggested_parent") or _get_suggested_parent(
                [], text=pending.get("verbatim_text", "")
            )

            # Handle no_domain_match: offer to create a new top-level domain
            if suggested_parent.get("no_domain_match"):
                set_feed_state(session_id, "FEED_AWAITING_NEW_DOMAIN_NAME")
                pending["suggested_parent"] = suggested_parent
                store_pending_fact(session_id, pending)
                return {
                    "action": "awaiting_new_domain_name",
                    "response_text": (
                        "This content doesn't fit any existing domain (BP.1–BP.11).\n"
                        "To create a new top-level domain, type its name "
                        "(e.g. \"Corporate and Legal Structure\").\n"
                        "Or type [skip] to discard this fact."
                    ),
                }

            set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")
            pending["suggested_parent"] = suggested_parent
            store_pending_fact(session_id, pending)
            return {
                "action": "awaiting_new_node_name",
                "response_text": (
                    f"Creating a new node under {suggested_parent['node_id']} — {suggested_parent['node_title']}.\n"
                    "What should it be called?"
                ),
            }
        # Promote the LLM's classification into the "final decided node"
        # field _execute_approval() writes from, now that Alex has
        # confirmed it.
        pending["proposed_node"] = {
            "node_id": classification["node_id"],
            "node_title": classification.get("node_title", ""),
        }
        pending["node_confidence"] = classification.get("confidence", "medium")
        store_pending_fact(session_id, pending)
        return _execute_approval(pending, session_id)

    return {
        "action": "unrecognized",
        "response_text": _format_review_block(pending, remaining=len(pending.get("remaining_facts", []))),
    }


def handle_node_selection(response_text: str, session_id: str) -> dict:
    """Handle Alex's node selection after choosing 'adjust'.

    Accepts either an exact node ID (e.g. "BP.1.1.3") or free text to
    search node titles by substring — there's no numbered candidate list
    to pick from anymore (the review block now shows one classified node,
    not a menu of three), so "adjust" is a general lookup, not a pick from
    a fixed set.

    Args:
        response_text: A node ID or a title search string.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    text = response_text.strip()
    nodes = _load_bp_architecture()
    selected_node = None

    if re.match(r"^BP\.\d", text, re.IGNORECASE):
        for node in nodes:
            if node.get("node_id", "").upper() == text.upper():
                selected_node = {
                    "node_id": node["node_id"],
                    "node_title": node.get("node_title") or "",
                }
                break
        if selected_node is None:
            return {
                "action": "invalid_selection",
                "response_text": f"No node with ID '{text}' exists. Double-check the ID, or search by part of a title instead.",
            }
    else:
        # Some architecture nodes have node_title: null in bp_architecture.json
        # (4 confirmed via grep) — n.get("node_title", "") only substitutes the
        # default when the KEY is absent, not when its value is None, so a
        # bare .get(...).lower() crashes with 'NoneType has no attribute lower'
        # the moment a null-titled node is scanned. Always coerce with `or ""`.
        matches = [
            n for n in nodes
            if text.lower() in (n.get("node_title") or "").lower() and n.get("node_id")
        ]
        if not matches:
            return {
                "action": "invalid_selection",
                "response_text": f"No node title matches '{text}'. Try a node ID (e.g. BP.1.1.3) or different search words.",
            }
        if len(matches) > 1:
            lines = [f"{len(matches)} nodes match '{text}'. Reply with the exact ID:\n"]
            for n in matches[:10]:
                lines.append(f"  {n['node_id']} — {n.get('node_title') or ''}")
            if len(matches) > 10:
                lines.append(f"  ...and {len(matches) - 10} more. Try narrowing your search.")
            return {"action": "multiple_matches", "response_text": "\n".join(lines)}
        selected_node = {"node_id": matches[0]["node_id"], "node_title": matches[0].get("node_title") or ""}

    pending["proposed_node"] = selected_node
    pending["node_confidence"] = "manual_override"
    store_pending_fact(session_id, pending)

    return _execute_approval(pending, session_id)


def handle_parent_selection(response_text: str, session_id: str) -> dict:
    """Handle Alex's parent node selection (option 5 in low-confidence flow).

    Args:
        response_text: Node ID or title from Alex.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    text = response_text.strip()
    nodes = _load_bp_architecture()
    selected_parent = None

    for node in nodes:
        if node.get("node_id", "").upper() == text.upper():
            selected_parent = {"node_id": node["node_id"], "node_title": node.get("node_title") or ""}
            break
        if (node.get("node_title") or "").lower() == text.lower():
            selected_parent = {"node_id": node["node_id"], "node_title": node.get("node_title") or ""}
            break

    if not selected_parent:
        for node in nodes:
            if text.lower() in (node.get("node_title") or "").lower():
                selected_parent = {"node_id": node["node_id"], "node_title": node.get("node_title") or ""}
                break

    if not selected_parent:
        return {
            "action": "invalid_parent",
            "response_text": f"Couldn't find node '{text}'. Try a node ID (e.g. BP.2.1) or an exact title.",
        }

    pending["suggested_parent"] = selected_parent
    store_pending_fact(session_id, pending)
    set_feed_state(session_id, "FEED_AWAITING_NEW_NODE_NAME")

    return {
        "action": "awaiting_new_node_name",
        "response_text": (
            f"Creating a new node under {selected_parent['node_id']} — {selected_parent['node_title']}.\n"
            "What should it be called?"
        ),
    }


def _get_next_domain_id() -> str:
    """Find the next available top-level domain ID.

    BP.12 is reserved for the risk/uncertainty register (referenced in
    linked_uncertainties fields across the architecture). Scans existing
    top-level nodes and returns the next integer after the highest found,
    skipping BP.12 if it hasn't been created yet.
    """
    from pathlib import Path

    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"
    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_nodes = data.get("nodes", [])
    except Exception:
        return "BP.13"

    existing_nums = set()
    for node in all_nodes:
        nid = node.get("node_id", "")
        if nid.startswith("BP.") and nid.count(".") == 1:
            try:
                num = int(nid.split(".")[1])
                existing_nums.add(num)
            except ValueError:
                pass

    # BP.12 is reserved even if not yet created
    existing_nums.add(12)

    next_num = max(existing_nums) + 1 if existing_nums else 13
    return f"BP.{next_num}"


def _create_new_domain(domain_name: str, verbatim_text: str = "") -> dict:
    """Create a new top-level domain node in bp_architecture.json.

    This is the real domain-creation function: assigns a permanent ID,
    writes a level-1 "domain" type node, and persists it so future
    classification passes can match facts against it.

    Args:
        domain_name: The name Alex gave the domain (e.g. "Corporate and Legal Structure").
        verbatim_text: The fact that triggered creation (for provenance).

    Returns:
        Dict with node_id, node_title, level of the newly created domain.
    """
    from pathlib import Path

    arch_path = Path(__file__).parent.parent.parent / "ceo_data" / "bp_architecture.json"

    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_nodes = data.get("nodes", [])
    except Exception as e:
        logger.error("[FeedHandler] Could not read bp_architecture.json to create domain: %s", e)
        return {"node_id": "NEW_PENDING", "node_title": domain_name, "level": 1}

    new_id = _get_next_domain_id()

    new_node = {
        "node_id": new_id,
        "parent_node": None,
        "level": 1.0,
        "node_type": "domain",
        "atomic_status": "non_atomic",
        "node_title": domain_name,
        "purpose": f"Top-level domain covering {domain_name.lower()}. Created because existing domains (BP.1–BP.11) did not cover this subject matter.",
        "required_output": f"Structured facts, decisions, and constraints related to {domain_name.lower()}.",
        "output_format": None,
        "proof_burden": None,
        "evidence_requirement": None,
        "evidence_gaps_assumptions": None,
        "linked_uncertainties": None,
        "prohibited_claims_inference_patterns": None,
        "dependencies": None,
        "reopen_condition": None,
        "decision_implication": None,
        "execution_mode": None,
        "human_review_type": None,
        "executor": None,
        "controller": None,
        "architecture_status": "candidate",
        "evidence_status": None,
        "notes_limitations": (
            f"Created automatically via Feed when content didn't fit any existing domain. "
            f"Not yet reviewed by an architecture pass."
            + (f" Originating fact: \"{verbatim_text[:200]}\"" if verbatim_text else "")
        ),
        "source_data_refs": None,
        "extracted_data": None,
        "mapping_rationale": None,
        "evidence_use_boundary": None,
        "rag_confidence": None,
        "review_notes": None,
    }

    all_nodes.append(new_node)
    data["nodes"] = all_nodes

    try:
        with open(arch_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("[FeedHandler] Could not write new domain %s: %s", new_id, e)
        return {"node_id": "NEW_PENDING", "node_title": domain_name, "level": 1}

    # Update the in-memory keyword map and titles so immediate re-classification sees it
    LEVEL1_KEYWORDS[new_id] = [w.lower() for w in domain_name.split() if len(w) > 3]
    LEVEL1_TITLES[new_id] = domain_name

    # Invalidate embedding cache
    global _node_embedding_cache
    _node_embedding_cache = None

    logger.info("[FeedHandler] Created new domain %s (%s)", new_id, domain_name)
    return {"node_id": new_id, "node_title": domain_name, "level": 1}


def handle_new_domain_name(response_text: str, session_id: str) -> dict:
    """Handle Alex's new domain name after choosing [1] on a no_domain_match review.

    Creates a real top-level domain in bp_architecture.json, then stores
    the pending fact under it.

    Args:
        response_text: The domain name Alex typed.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    text_lower = response_text.strip().lower()
    if text_lower in SKIP_WORDS:
        clear_pending_fact(session_id)
        remaining = pending.get("remaining_facts", [])
        if remaining:
            set_feed_state(session_id, None)
            return _process_next_fact(remaining, session_id)
        set_feed_state(session_id, None)
        return {"action": "skipped", "response_text": "Fact skipped. Nothing was stored."}

    domain_name = response_text.strip()
    if len(domain_name) < 3:
        return {
            "action": "invalid_name",
            "response_text": "Domain name too short. Please provide a descriptive title (3+ characters).",
        }

    new_domain = _create_new_domain(
        domain_name=domain_name,
        verbatim_text=pending.get("verbatim_text", ""),
    )

    if new_domain["node_id"] == "NEW_PENDING":
        set_feed_state(session_id, None)
        clear_pending_fact(session_id)
        return {
            "action": "error",
            "response_text": "Failed to create domain. Please try again.",
        }

    # Store the fact under the newly created domain
    pending["proposed_node"] = new_domain
    pending["node_confidence"] = "new_domain_created"
    pending["new_node_flag"] = True
    pending["proposed_parent"] = {"node_id": new_domain["node_id"], "node_title": new_domain["node_title"]}
    store_pending_fact(session_id, pending)

    return _execute_approval(pending, session_id)


def handle_new_node_name(response_text: str, session_id: str) -> dict:
    """Handle Alex's new node name after choosing 'create new node'.

    Args:
        response_text: The proposed node name.
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    pending = get_pending_fact(session_id)
    if not pending:
        set_feed_state(session_id, None)
        return {
            "action": "no_pending",
            "response_text": "No pending fact found. Please re-submit.",
        }

    node_name = response_text.strip()
    if len(node_name) < 3:
        return {
            "action": "invalid_name",
            "response_text": "Node name too short. Please provide a descriptive title (3+ characters).",
        }

    suggested_parent = pending.get("suggested_parent") or _get_suggested_parent(
        [], text=pending.get("verbatim_text", "")
    )

    # Actually creates the node in bp_architecture.json now (real ID, real
    # parent, real level) — this used to just tag the fact with a
    # NEW_PENDING placeholder and Alex's name, which meant the "new node"
    # never existed anywhere future classification could find it.
    new_node = _create_new_node(
        node_title=node_name,
        parent_id=suggested_parent.get("node_id", "BP.1"),
        verbatim_text=pending.get("verbatim_text", ""),
    )

    pending["proposed_node"] = new_node
    pending["node_confidence"] = "new_node_created"
    pending["new_node_flag"] = True
    pending["proposed_parent"] = suggested_parent
    store_pending_fact(session_id, pending)

    return _execute_approval(pending, session_id)


def _store_fact_now(
    verbatim: str,
    content_type: str,
    epistemic_status: str,
    node_id: Optional[str],
    node_title: str,
    session_id: str,
    content_confidence: float = 0.5,
    node_confidence: Optional[str] = None,
    new_node_flag: bool = False,
    proposed_parent: Optional[dict] = None,
    extra_metadata: Optional[dict] = None,
) -> dict:
    """Write one fact to knowledge_base and run post-store hooks.

    Shared by both storage paths in this file: the interactive
    confirm/adjust flow (_execute_approval, one fact at a time, human
    approved) and the auto-file path (handle_raw_text, high-confidence
    facts written immediately with no human step). This function only
    knows how to store — it doesn't touch pending-fact/session state or
    decide what happens next; callers own that.

    Returns:
        Dict with status ("stored" | "duplicate" | "error"), chunk_id
        (None for duplicate/error), and error (str, only on "error").
    """
    section = node_id.split(".")[1] if node_id and "." in node_id else None

    try:
        from services.rag_service import store

        metadata = {
            "content_type": content_type,
            "node_id": node_id,
            "node_title": node_title,
            "source": "feed_handler",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "node_confidence": node_confidence,
            "new_node_flag": new_node_flag,
            "proposed_parent": proposed_parent,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        chunk_id = store(
            content=verbatim,
            source_type="ceo_doc",
            section=section,
            epistemic_status=epistemic_status,
            topic_tags=[content_type, f"node:{node_id or 'unmatched'}"],
            session_id=session_id,
            confidence=content_confidence,
            metadata=metadata,
        )

        if chunk_id is None:
            return {"status": "duplicate", "chunk_id": None}

        _run_post_store_hooks(verbatim, chunk_id, epistemic_status, session_id)
        return {"status": "stored", "chunk_id": chunk_id}

    except Exception as e:
        logger.error("[FeedHandler] Storage failed for node %s: %s", node_id, e)
        return {"status": "error", "chunk_id": None, "error": str(e)}


def _execute_approval(fact_data: dict, session_id: str) -> dict:
    """Write an Alex-approved fact to knowledge_base and advance the queue.

    Args:
        fact_data: The full pending fact dict.
        session_id: Current session ID.

    Returns:
        Dict with action and confirmation text.
    """
    verbatim = fact_data["verbatim_text"]
    content_type = fact_data["content_type"]
    epistemic_status = fact_data["epistemic_status"]
    proposed_node = fact_data.get("proposed_node")
    node_id = proposed_node["node_id"] if proposed_node else None
    node_title = proposed_node["node_title"] if proposed_node else "Unmatched"

    classification = fact_data.get("classification", {})
    tier = fact_data.get("tier", "ask")
    source = "alex_direct"
    audit = _build_audit_metadata(classification, source, tier)

    result = _store_fact_now(
        verbatim=verbatim,
        content_type=content_type,
        epistemic_status=epistemic_status,
        node_id=node_id,
        node_title=node_title,
        session_id=session_id,
        content_confidence=fact_data.get("content_confidence", 0.5),
        node_confidence=fact_data.get("node_confidence"),
        new_node_flag=fact_data.get("new_node_flag", False),
        proposed_parent=fact_data.get("proposed_parent"),
        extra_metadata=audit,
    )

    if result["status"] == "duplicate":
        clear_pending_fact(session_id)
        set_feed_state(session_id, None)
        return {
            "action": "deduplicated",
            "response_text": (
                f"This fact already exists in the knowledge base (duplicate detected). "
                f"Nothing new was stored."
            ),
        }

    if result["status"] == "error":
        set_feed_state(session_id, None)
        clear_pending_fact(session_id)
        return {
            "action": "error",
            "response_text": f"Storage failed: {result['error']}. Your data was NOT stored. Please try again.",
        }

    chunk_id = result["chunk_id"]
    clear_pending_fact(session_id)

    remaining = fact_data.get("remaining_facts", [])
    if remaining:
        set_feed_state(session_id, None)
        next_result = _process_next_fact(remaining, session_id)
        confirmation = (
            f"Stored [{epistemic_status}] → {node_id or 'Unmatched'} ({node_title})\n\n"
            f"---\n\n"
            f"{next_result['response_text']}"
        )
        return {
            "action": "stored_with_next",
            "response_text": confirmation,
            "chunk_id": chunk_id,
        }

    set_feed_state(session_id, None)
    new_node_note = ""
    if fact_data.get("new_node_flag"):
        parent_id = (fact_data.get("proposed_parent") or {}).get("node_id", "?")
        new_node_note = f"\n(New node created under {parent_id} — now part of the architecture, classifiable for future facts too.)"

    return {
        "action": "stored",
        "response_text": (
            f"Stored [{epistemic_status}] → {node_id or 'Unmatched'} ({node_title})"
            f"{new_node_note}"
        ),
        "chunk_id": chunk_id,
    }


def _run_post_store_hooks(
    content: str, chunk_id: str, epistemic_status: str, session_id: str
) -> None:
    """Run post-storage hooks: contradiction check, negative knowledge, temporal scoring."""
    try:
        from services.rag_service import retrieve
        from services.rag_hooks import store_contradiction_resolution, store_negative_knowledge
        from services.temporal_decay import compute_final_score

        contradictions = retrieve(
            query=content,
            source_types=["ceo_doc", "conversation", "decision"],
            top_k=3,
            threshold=0.75,
        )

        for chunk in contradictions:
            if chunk.id == chunk_id:
                continue
            if chunk.similarity > 0.85 and chunk.epistemic_status != epistemic_status:
                store_contradiction_resolution(
                    contradiction=f"New fact conflicts with existing: '{chunk.content[:80]}'",
                    resolution=f"New input supersedes (approved by CEO): '{content[:80]}'",
                    reasoning="CEO directly submitted and approved newer data",
                    session_id=session_id,
                )
                logger.info(
                    "[FeedHandler] Contradiction resolved: new chunk %s vs existing %s",
                    chunk_id,
                    chunk.id,
                )
                emit_trace(
                    session_id, "Contradiction", "detected",
                    f"New fact conflicts with existing ({chunk.epistemic_status}): \"{chunk.content[:60]}\"",
                    data={
                        "type": "contradiction",
                        "new_fact": content[:100],
                        "old_fact": chunk.content[:100],
                        "old_status": chunk.epistemic_status,
                        "old_chunk_id": chunk.id,
                        "resolution": "new_supersedes",
                    },
                )
                break

        if epistemic_status == "CONFIRMED":
            invalidated = retrieve(
                query=content,
                source_types=["ceo_doc", "agent_insight"],
                epistemic_status=["ASSUMPTION"],
                top_k=3,
                threshold=0.8,
            )
            for chunk in invalidated:
                if chunk.id == chunk_id:
                    continue
                store_negative_knowledge(
                    what_failed=f"Assumption invalidated: '{chunk.content[:80]}'",
                    reason=f"CEO confirmed contradicting fact: '{content[:80]}'",
                    source="ceo_correction",
                    session_id=session_id,
                )
                logger.info(
                    "[FeedHandler] Assumption invalidated: %s", chunk.id
                )
                break

        score = compute_final_score(
            similarity=1.0,
            created_at=datetime.now(timezone.utc).isoformat(),
            epistemic_status=epistemic_status,
        )
        logger.debug("[FeedHandler] Stored chunk %s with temporal score: %.4f", chunk_id, score)

        try:
            from services.memory_index import link_new_chunk

            link_new_chunk(
                chunk_id=chunk_id,
                content=content,
                metadata={"epistemic_status": epistemic_status},
                session_id=session_id,
            )
        except Exception as link_err:
            logger.error("[FeedHandler] Memory index linking failed (non-fatal): %s", link_err)

    except Exception as e:
        logger.error("[FeedHandler] Post-store hooks error (non-fatal): %s", e)


# --- Batch document processing (file uploads) ---
#
# handle_raw_text() above is the CHAT flow: one fact at a time, approved via
# a back-and-forth conversation. That doesn't scale to a whole uploaded
# document, which can yield dozens of atomic facts. The functions below
# classify the ENTIRE batch up front (for the Process panel's live narration
# and review checklist) and defer storage until Alex bulk-approves a subset.


def _batch_key(session_id: str) -> str:
    """Redis key for a pending document batch awaiting bulk review."""
    return f"feed_batch:{session_id}"


def _store_batch(session_id: str, batch_data: dict) -> None:
    """Store a classified document batch in Redis awaiting bulk approval."""
    try:
        r = _get_redis()
        r.set(_batch_key(session_id), json.dumps(batch_data), ex=PENDING_FACT_TTL * 3)
    except Exception as e:
        logger.error("[FeedHandler] Redis error storing batch: %s", e)


def get_batch(session_id: str) -> Optional[dict]:
    """Retrieve the pending document batch from Redis.

    Returns:
        The batch dict (batch_id, filename, facts, ...) or None if expired
        or never created.
    """
    try:
        r = _get_redis()
        raw = r.get(_batch_key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.error("[FeedHandler] Redis error getting batch: %s", e)
        return None


def clear_batch(session_id: str) -> None:
    """Remove the pending document batch."""
    try:
        r = _get_redis()
        r.delete(_batch_key(session_id))
    except Exception as e:
        logger.error("[FeedHandler] Redis error clearing batch: %s", e)


def process_uploaded_document(
    text: str, filename: str, session_id: str, max_facts: int = 200
) -> dict:
    """Split extracted document text into atomic facts and classify all of them.

    Unlike handle_raw_text() (one fact per chat turn), this classifies the
    whole document up front so the Process panel can show a single bulk
    review checklist. Nothing is written to the knowledge base here —
    bulk_store_facts() does that once Alex approves a subset.

    Args:
        text: Extracted plain text (from services.document_extractor).
        filename: Original filename, kept for provenance and display.
        session_id: Current session ID.
        max_facts: Safety cap on facts classified in one batch. Node
            matching runs a local embedding compare per fact — cheap
            individually, but a large document can yield hundreds of
            sentences, so this keeps a single upload bounded.

    Returns:
        Dict with: batch_id, filename, total_facts, truncated, facts (each:
        id, verbatim_text, content_type, content_confidence,
        epistemic_status, proposed_node, node_confidence,
        all_node_candidates, selected).
    """
    fmt = detect_format(text)
    raw_facts = split_into_atomic_facts(text, fmt)

    truncated = len(raw_facts) > max_facts
    raw_facts = raw_facts[:max_facts]

    if not raw_facts:
        emit_trace(
            session_id, "Feed", "no_facts_found",
            f"No extractable facts found in {filename}",
            {"filename": filename},
        )
        return {
            "batch_id": None,
            "filename": filename,
            "total_facts": 0,
            "truncated": False,
            "facts": [],
        }

    emit_trace(
        session_id, "Feed", "classifying",
        f"Classifying {len(raw_facts)} extracted fact(s) from {filename}...",
        {"filename": filename, "fact_count": len(raw_facts)},
    )

    classified = []
    checkpoint = max(1, len(raw_facts) // 5)
    for i, fact in enumerate(raw_facts):
        fact_text = fact["text"]

        content_classification = classify_content_type(fact_text)
        content_type = content_classification["content_type"]
        epistemic_status = EPISTEMIC_STATUS_BY_CONTENT_TYPE.get(
            content_type, fact.get("inferred_status", "INFERRED")
        )

        # Use the local cached-embedding matcher (not match_bp_node's RAG
        # round trip) — a batch of a hundred-plus facts doing a network
        # call each would be far too slow for a "live" process panel.
        node_matches = _direct_node_match(fact_text, top_k=3)

        proposed_node = None
        node_confidence = "unmatched"
        if node_matches and node_matches[0]["similarity"] >= 0.6:
            proposed_node = node_matches[0]
            node_confidence = "matched"
        elif node_matches:
            proposed_node = node_matches[0]
            node_confidence = "low_confidence"

        classified.append({
            "id": str(i),
            "verbatim_text": fact_text,
            "content_type": content_type,
            "content_confidence": content_classification["confidence"],
            "epistemic_status": epistemic_status,
            "proposed_node": proposed_node,
            "node_confidence": node_confidence,
            "all_node_candidates": node_matches[:3],
            "source_format": fact.get("source_format", fmt),
            # Pre-check facts with a usable node match; leave unmatched ones
            # unchecked so Alex has to make an explicit call on those.
            "selected": node_confidence in ("matched", "low_confidence"),
        })

        if (i + 1) % checkpoint == 0 or i == len(raw_facts) - 1:
            emit_trace(
                session_id, "Feed", "classifying_progress",
                f"Classified {i + 1}/{len(raw_facts)} fact(s) from {filename}...",
                {"filename": filename, "done": i + 1, "total": len(raw_facts)},
            )

    batch_id = f"batch_{session_id}_{int(datetime.now(timezone.utc).timestamp())}"

    batch_data = {
        "batch_id": batch_id,
        "filename": filename,
        "total_facts": len(classified),
        "truncated": truncated,
        "facts": classified,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    _store_batch(session_id, batch_data)

    emit_trace(
        session_id, "Feed", "ready_for_review",
        f"Ready for review — {len(classified)} fact(s) extracted from {filename}"
        + (" (truncated)" if truncated else ""),
        {"filename": filename, "batch_id": batch_id, "fact_count": len(classified)},
    )

    return batch_data


def bulk_store_facts(
    session_id: str,
    accepted_fact_ids: list[str],
    edited_texts: Optional[dict] = None,
) -> dict:
    """Store the Alex-approved subset of a classified document batch.

    Runs each approved fact through the same store() + post-store-hooks path
    as the chat approval flow (_execute_approval), so document-sourced facts
    get identical epistemic tagging, dedup, and contradiction handling.

    Args:
        session_id: Current session ID.
        accepted_fact_ids: The "id" values (from process_uploaded_document's
            fact list) that Alex approved for storage.
        edited_texts: Optional {fact_id: corrected_text} for facts Alex
            edited in the review panel before approving.

    Returns:
        Dict with: stored_count, duplicate_count, skipped_count, results
        (per-fact outcome), filename.
    """
    from services.rag_service import store

    batch = get_batch(session_id)
    if not batch:
        return {
            "stored_count": 0,
            "duplicate_count": 0,
            "skipped_count": 0,
            "results": [],
            "error": "No pending batch found — it may have expired. Please re-upload.",
        }

    edited_texts = edited_texts or {}
    facts_by_id = {f["id"]: f for f in batch["facts"]}
    filename = batch["filename"]

    emit_trace(
        session_id, "Feed", "storing",
        f"Storing {len(accepted_fact_ids)} approved fact(s) from {filename}...",
        {"filename": filename, "count": len(accepted_fact_ids)},
    )

    results = []
    stored_count = 0
    duplicate_count = 0

    for fact_id in accepted_fact_ids:
        fact = facts_by_id.get(fact_id)
        if not fact:
            continue

        verbatim = edited_texts.get(fact_id, fact["verbatim_text"])
        proposed_node = fact.get("proposed_node")
        node_id = proposed_node["node_id"] if proposed_node else None
        node_title = proposed_node["node_title"] if proposed_node else "Unmatched"
        section = (
            node_id.split(".")[1] if node_id and "." in node_id else None
        )

        try:
            chunk_id = store(
                content=verbatim,
                source_type="ceo_doc",
                section=section,
                epistemic_status=fact["epistemic_status"],
                topic_tags=[
                    fact["content_type"],
                    f"node:{node_id or 'unmatched'}",
                    f"file:{filename}",
                ],
                session_id=session_id,
                confidence=fact.get("content_confidence", 0.5),
                metadata={
                    "content_type": fact["content_type"],
                    "node_id": node_id,
                    "node_title": node_title,
                    "source": "feed_document_upload",
                    "source_filename": filename,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            if chunk_id is None:
                duplicate_count += 1
                results.append({
                    "fact_id": fact_id, "status": "duplicate", "text": verbatim[:80],
                })
                continue

            _run_post_store_hooks(verbatim, chunk_id, fact["epistemic_status"], session_id)
            stored_count += 1
            results.append({
                "fact_id": fact_id, "status": "stored", "chunk_id": chunk_id,
                "node_id": node_id, "text": verbatim[:80],
            })

        except Exception as e:
            logger.error("[FeedHandler] Bulk store failed for fact %s: %s", fact_id, e)
            results.append({
                "fact_id": fact_id, "status": "error", "error": str(e), "text": verbatim[:80],
            })

    skipped_count = len(batch["facts"]) - len(accepted_fact_ids)
    clear_batch(session_id)

    emit_trace(
        session_id, "Feed", "storage_complete",
        f"Stored {stored_count} fact(s) from {filename} "
        f"({duplicate_count} duplicate(s), {skipped_count} skipped)",
        {
            "filename": filename, "stored": stored_count,
            "duplicates": duplicate_count, "skipped": skipped_count,
        },
    )

    return {
        "stored_count": stored_count,
        "duplicate_count": duplicate_count,
        "skipped_count": skipped_count,
        "results": results,
        "filename": filename,
    }


def _process_next_fact(remaining_facts: list[dict], session_id: str) -> dict:
    """Pop the next already-classified fact off the review queue.

    remaining_facts entries are fact_data dicts built once, up front, by
    _classify_one_fact() during handle_raw_text's batch classification pass
    (the same pass that decides what auto-files vs. what needs review) —
    this no longer re-classifies plain text here. It used to: the old
    signature took list[str] and called classify_content_type() /
    classify_and_match_node() again on each one, which both re-did work
    already done upfront and broke the moment handle_raw_text started
    passing pre-classified dicts instead of raw strings.

    Args:
        remaining_facts: List of fact_data dicts (verbatim_text,
            content_type, content_confidence, epistemic_status,
            classification, source_format, submitted_at), in queue order.
        session_id: Session ID.

    Returns:
        Dict with review block for the next fact.
    """
    if not remaining_facts:
        return {
            "action": "complete",
            "response_text": "All facts processed.",
        }

    next_fact = dict(remaining_facts[0])
    next_fact["remaining_facts"] = remaining_facts[1:]

    store_pending_fact(session_id, next_fact)
    set_feed_state(session_id, "FEED_AWAITING_APPROVAL")

    review_text = _format_review_block(next_fact, remaining=len(remaining_facts) - 1)
    return {
        "action": "awaiting_approval",
        "response_text": review_text,
    }


def _format_review_block(fact_data: dict, remaining: int = 0) -> str:
    """Format a pending fact as a single, clean confirmation for Alex.

    One consistent shape regardless of classification confidence — the
    LLM-based classifier (classify_and_match_node) picks exactly one node,
    so there's no menu of 3 similarity-ranked guesses to choose between
    anymore. This is the "Node: BP.x.x.x / node_title: ..." format Alex
    asked for directly, matching what he saw a manual ChatGPT pass produce.

    Args:
        fact_data: The full fact dict (must include "classification").
        remaining: Number of facts still queued.

    Returns:
        Formatted review string.
    """
    content_type = fact_data["content_type"]
    confidence = fact_data.get("content_confidence", 0.0)
    epistemic_status = fact_data["epistemic_status"]
    verbatim = fact_data["verbatim_text"]
    classification = fact_data.get("classification", {})

    lines = []
    lines.append("ADD — Review before storing")
    lines.append("=" * 40)
    lines.append("")

    none_fit = classification.get("none_fit", True) or not classification.get("node_id")

    if none_fit:
        suggested_parent = classification.get("suggested_parent") or _get_suggested_parent(
            [], text=verbatim
        )
        fact_data["suggested_parent"] = suggested_parent
        no_domain = suggested_parent.get("no_domain_match", False)

        if no_domain:
            lines.append("  No existing domain covers this content.")
            lines.append("")
            lines.append("  This fact is outside the scope of BP.1–BP.11 entirely.")
            lines.append("  (e.g., legal entity structure, jurisdiction, incorporation)")
        else:
            lines.append("  No existing node is a strong fit for this fact.")
            lines.append("")
            lines.append(f"  Suggested parent: {suggested_parent['node_id']} — {suggested_parent['node_title']}")
        reasoning = classification.get("reasoning")
        if reasoning:
            lines.append(f"  ({reasoning})")
    else:
        lines.append(f"  Node: {classification['node_id']}")
        lines.append(f"  node_title: {classification.get('node_title', '')}")
        conf = classification.get("confidence", "medium")
        if conf != "high":
            reasoning = classification.get("reasoning")
            lines.append(f"  Match confidence: {conf}" + (f" — {reasoning}" if reasoning else ""))

    lines.append("")
    lines.append(f"  Type: {content_type.upper()}" + (
        " (low confidence)" if confidence < 0.7 else ""
    ))
    lines.append(f"  Status: [{epistemic_status}]")
    lines.append("")
    lines.append(f"  \"{verbatim}\"")
    lines.append("")
    lines.append("-" * 40)

    tier = fact_data.get("tier", "ask")

    if none_fit:
        suggested_parent = fact_data.get("suggested_parent", {})
        no_domain = suggested_parent.get("no_domain_match", False)
        if no_domain:
            lines.append("  [1] Create new top-level domain — type a name to create it")
        else:
            lines.append(f"  [1] Create new node under {suggested_parent.get('node_id', '?')}")
    elif tier == "soft_ask":
        lines.append("  Looks like a good fit. [yes] to confirm, [adjust] to move, [skip] to discard")
    else:
        lines.append("  [1] Confirm — store here")
    lines.append("  [2] Adjust — pick a different node")
    lines.append("  [skip] — discard this fact")

    if remaining > 0:
        lines.append("")
        lines.append(f"  ({remaining} more fact(s) queued after this one)")

    return "\n".join(lines)


# --- Question detection ---
#
# Feed mode has exactly one job: turn typed/uploaded text into stored facts.
# It has zero intent detection otherwise, so a plain question like "can you
# show me the last feed" used to get run through classify_content_type() /
# match_bp_node() just like real data, and come back as a nonsensical
# "ADD — Review before storing" prompt. The functions below catch anything
# question-shaped before it reaches that pipeline.

QUESTION_LEAD_PATTERN = re.compile(
    r"^\s*(who|what|when|where|why|how|which)\b"
    r"|^\s*(can|could|would|should|do|does|did|is|are|will)\s+(i|you|we|it|this|there|these)\b"
    r"|^\s*(show|tell|list|find|give|help)\s+me\b",
    re.IGNORECASE,
)

# Phrases that signal Alex is asking for an ACTION Feed can't perform —
# these get an honest "I can't do that here" redirect instead of an
# attempted Q&A answer, since a RAG lookup would just come back empty for
# something like "build the financial section".
ACTION_REDIRECTS = [
    (re.compile(r"\bbuild\b.*\bsection\b|\bgenerate the plan\b|\bcompile the plan\b|\bwrite the section\b", re.IGNORECASE), "Build"),
    (re.compile(r"\bexport\b|\bdownload the plan\b|\bgenerate a (pdf|docx|word doc)\b", re.IGNORECASE), "Export"),
    (re.compile(r"\bvalidate\b|\bvalidation queue\b|\bcheck( the)? assumptions\b", re.IGNORECASE), "Validate"),
    (re.compile(r"\bchallenge\b|\bdevil'?s advocate\b|\bstress[- ]test\b", re.IGNORECASE), "Challenge"),
]


def looks_like_question(text: str) -> bool:
    """Heuristic: does this read as a question/request rather than data to store?

    Args:
        text: Alex's raw message.

    Returns:
        True if this looks like a question or command rather than a
        statement of fact that should be classified and stored.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    return bool(QUESTION_LEAD_PATTERN.match(stripped))


def handle_feed_question(text: str, session_id: str) -> dict:
    """Answer a question typed in Feed mode instead of misfiling it as data.

    Tries Inspect's RAG-backed Q&A first (a lot of questions asked while
    sitting in Feed are legitimate lookups — "what's our TAM assumption").
    If that comes back empty, or the question is really an action request
    for a different workspace, this says so plainly instead of pretending
    Feed can help.

    Args:
        text: Alex's message (already detected as question-shaped).
        session_id: Current session ID.

    Returns:
        Dict with action and response_text.
    """
    for pattern, workspace_name in ACTION_REDIRECTS:
        if pattern.search(text):
            return {
                "action": "redirect",
                "response_text": (
                    f"I can't do that from Feed — I don't have that feature here. "
                    f"Feed only adds new data. That sounds like a {workspace_name} "
                    f"task; switch workspaces (the + menu, top left) and ask there."
                ),
            }

    try:
        from web.handlers.inspect_handler import answer_inspect_question

        result = answer_inspect_question(text)
        answer = result.get("answer", "") or ""
        sources = result.get("sources", [])

        if not sources or answer.startswith("No relevant data found"):
            return {
                "action": "no_answer",
                "response_text": (
                    "I don't have anything in the knowledge base to answer that. "
                    "If you meant to add data, rephrase it as a statement — "
                    "otherwise try asking in Inspect instead."
                ),
            }

        return {"action": "answered", "response_text": answer}
    except Exception as e:
        logger.error("[FeedHandler] Error answering question in Feed mode: %s", e)
        return {
            "action": "error",
            "response_text": "I couldn't look that up right now. Try again, or ask in Inspect.",
        }


def handle_feed_message(text: str, session_id: str) -> str:
    """Main entry point for all feed workspace messages.

    Routes based on current feed state (approval flow or new input).

    Args:
        text: Message text from Alex.
        session_id: Current session ID.

    Returns:
        Response text for the chat. Never returns empty string.
    """
    try:
        state = get_feed_state(session_id)

        if state == "FEED_AWAITING_DUPLICATE_CONFIRM":
            result = _handle_duplicate_confirm(text, session_id)
            return result.get("response_text") or "Processing..."

        if state == "FEED_AWAITING_APPROVAL":
            result = handle_approval_response(text, session_id)
            return result.get("response_text") or "Processing your response..."

        if state == "FEED_AWAITING_NODE_SELECTION":
            result = handle_node_selection(text, session_id)
            return result.get("response_text") or "Processing node selection..."

        if state == "FEED_AWAITING_NEW_NODE_NAME":
            result = handle_new_node_name(text, session_id)
            return result.get("response_text") or "Processing new node..."

        if state == "FEED_AWAITING_PARENT_SELECTION":
            result = handle_parent_selection(text, session_id)
            return result.get("response_text") or "Processing parent selection..."

        if state == "FEED_AWAITING_NEW_DOMAIN_NAME":
            result = handle_new_domain_name(text, session_id)
            return result.get("response_text") or "Processing new domain..."

        if looks_like_question(text):
            result = handle_feed_question(text, session_id)
            return result.get("response_text") or "I'm not sure how to answer that here."

        result = handle_raw_text(text, session_id=session_id)
        return result.get("response_text") or "Input received but no facts could be extracted. Try rephrasing."
    except Exception as e:
        logger.error("[FeedHandler] Unhandled error in handle_feed_message: %s", e, exc_info=True)
        return f"Feed processing error: {e}. Please try again."


# --- Format detection and splitting (unchanged from original) ---


def detect_format(text: str) -> str:
    """Detect the format of raw text input.

    Args:
        text: The raw text.

    Returns:
        One of: "bullets", "table", "paragraph", "mixed".
    """
    lines = text.strip().split("\n")
    lines = [l for l in lines if l.strip()]

    if not lines:
        return "paragraph"

    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    bullet_lines = sum(1 for l in lines if bullet_pattern.match(l))

    separator_pattern = re.compile(r"[|\t]")
    table_lines = sum(1 for l in lines if separator_pattern.search(l))

    total = len(lines)

    if table_lines > total * 0.5:
        return "table"
    elif bullet_lines > total * 0.5:
        return "bullets"
    elif bullet_lines > 0 and (total - bullet_lines) > 2:
        return "mixed"
    else:
        return "paragraph"


def split_into_atomic_facts(text: str, fmt: str) -> list[dict]:
    """Split text into atomic facts based on detected format.

    Args:
        text: The raw text.
        fmt: Detected format (bullets/table/paragraph/mixed).

    Returns:
        List of dicts with: text, inferred_status, source_format.
    """
    if fmt == "bullets":
        return _split_bullets(text)
    elif fmt == "table":
        return _split_table(text)
    elif fmt == "mixed":
        return _split_mixed(text)
    else:
        return _split_paragraph(text)


def _split_bullets(text: str) -> list[dict]:
    """Parse bullet-point text into individual facts."""
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    lines = text.strip().split("\n")
    facts = []

    for line in lines:
        cleaned = bullet_pattern.sub("", line).strip()
        if cleaned and len(cleaned) > 3:
            facts.append({
                "text": cleaned,
                "inferred_status": _infer_epistemic_status(cleaned),
                "source_format": "bullets",
            })

    return facts


def _split_table(text: str) -> list[dict]:
    """Parse table-formatted text into individual facts."""
    lines = text.strip().split("\n")
    facts = []

    separator = "\t" if "\t" in text else "|"
    header = None

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^[\s\-|+]+$", line):
            continue

        cells = [c.strip() for c in line.split(separator) if c.strip()]

        if header is None and cells:
            header = cells
            continue

        if cells and header:
            row_text = "; ".join(
                f"{header[j]}: {cells[j]}" if j < len(header) else cells[j]
                for j in range(len(cells))
            )
            if len(row_text) > 5:
                facts.append({
                    "text": row_text,
                    "inferred_status": _infer_epistemic_status(row_text),
                    "source_format": "table",
                })
        elif cells:
            row_text = "; ".join(cells)
            if len(row_text) > 5:
                facts.append({
                    "text": row_text,
                    "inferred_status": _infer_epistemic_status(row_text),
                    "source_format": "table",
                })

    return facts


def _split_paragraph(text: str) -> list[dict]:
    """Split paragraph text into sentences as atomic facts.

    Preserves semantic blocks (JTBD, Outcome+Metric, PMF definitions) to
    avoid context loss. Individual facts within these blocks will carry
    their full semantic context rather than being isolated sentences.
    """
    text = text.strip()

    # Define semantic block patterns that should NOT be split
    SEMANTIC_BLOCK_PATTERNS = [
        # JTBD blocks: "Job: ... Outcome: ... Metric: ..."
        r"Job:\s*[^.]+?(?:Outcome:\s*[^.]+?)?(?:Metric:\s*[^.]+?)?(?=\n\n|Job:|$)",
        # Outcome+Metric pairs
        r"Outcome:\s*[^.]+?Metric:\s*[^.]+?(?=\n\n|Outcome:|Job:|$)",
        # PMF definition statements (keep "PMF is/is not: ... when ..." together)
        r"PMF\s+is(?:\s+not)?:\s*[^.]+?when\s+[^.]+?(?=\n\n|PMF\s+is|$)",
        # Evidence statements (keep "Evidence: ... shows/indicates ..." together)
        r"Evidence:\s*[^.]+?(?:shows|indicates|suggests)\s+[^.]+?(?=\n\n|Evidence:|$)",
        # Prohibition statements (keep "We do not/cannot ... because ..." together)
        r"We\s+(?:do not|cannot|don't)\s+[^.]+?because\s+[^.]+?(?=\n\n|We\s+(?:do not|cannot)|$)",
    ]

    # First, extract semantic blocks
    semantic_blocks = []
    remaining_text = text

    for pattern in SEMANTIC_BLOCK_PATTERNS:
        for match in re.finditer(pattern, remaining_text, re.IGNORECASE | re.DOTALL):
            block_text = match.group(0).strip()
            if len(block_text) > 10:
                semantic_blocks.append({
                    "text": block_text,
                    "inferred_status": _infer_epistemic_status(block_text),
                    "source_format": "paragraph_semantic_block",
                    "start": match.start(),
                    "end": match.end(),
                })
        # Remove matched blocks from remaining text
        remaining_text = re.sub(pattern, "", remaining_text, flags=re.IGNORECASE | re.DOTALL)

    # Now split remaining text into sentences
    sentences = re.split(r"(?<=[.!?])\s+", remaining_text.strip())
    facts = []

    for sent in sentences:
        sent = sent.strip()
        if len(sent) > 10:
            facts.append({
                "text": sent,
                "inferred_status": _infer_epistemic_status(sent),
                "source_format": "paragraph",
            })

    # Merge semantic blocks back in, remove start/end markers
    for block in semantic_blocks:
        del block["start"]
        del block["end"]
        facts.append(block)

    return facts


def _split_mixed(text: str) -> list[dict]:
    """Split mixed-format text (bullets + paragraphs)."""
    lines = text.strip().split("\n")
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    facts = []
    paragraph_buffer = []

    for line in lines:
        if bullet_pattern.match(line):
            if paragraph_buffer:
                para_text = " ".join(paragraph_buffer)
                facts.extend(_split_paragraph(para_text))
                paragraph_buffer = []
            cleaned = bullet_pattern.sub("", line).strip()
            if cleaned and len(cleaned) > 3:
                facts.append({
                    "text": cleaned,
                    "inferred_status": _infer_epistemic_status(cleaned),
                    "source_format": "mixed_bullet",
                })
        elif line.strip():
            paragraph_buffer.append(line.strip())

    if paragraph_buffer:
        para_text = " ".join(paragraph_buffer)
        facts.extend(_split_paragraph(para_text))

    return facts


def _infer_epistemic_status(text: str) -> str:
    """Infer epistemic status from language cues in the text.

    Args:
        text: A single fact/claim.

    Returns:
        One of: CONFIRMED, ASSUMPTION, CONTRADICTION, INFERRED.
    """
    text_lower = text.lower()

    confirmed_cues = [
        "confirmed", "verified", "proven", "validated", "they said",
        "the contract states", "we know", "evidence shows", "data shows",
    ]
    assumption_cues = [
        "i think", "maybe", "hypothesis", "likely", "probably",
        "we assume", "should be", "expected to", "might", "could be",
        "we believe", "our assumption",
    ]
    contradiction_cues = [
        "contradicts", "conflicts with", "inconsistent",
        "but earlier", "however", "on the other hand",
    ]

    for cue in contradiction_cues:
        if cue in text_lower:
            return "CONTRADICTION"

    for cue in confirmed_cues:
        if cue in text_lower:
            return "CONFIRMED"

    for cue in assumption_cues:
        if cue in text_lower:
            return "ASSUMPTION"

    return "INFERRED"


# --- Legacy format functions (kept for backward compatibility) ---


def format_feed_response(results: dict) -> str:
    """Format the feed processing results as a chat message.

    Args:
        results: Output from handle_raw_text() or handle_feed_message().

    Returns:
        Formatted string for the chat panel.
    """
    if "response_text" in results:
        return results["response_text"]

    facts = results.get("facts", [])
    count = results.get("count", 0)
    fmt = results.get("format_detected", "unknown")

    if count == 0:
        return "No extractable facts found in that input. Try rephrasing or adding more detail."

    lines = [f"Extracted {count} fact(s) from {fmt} input:"]
    lines.append("")

    status_counts = {}
    for fact in facts:
        status = fact.get("inferred_status", "INFERRED")
        status_counts[status] = status_counts.get(status, 0) + 1

    for i, fact in enumerate(facts[:10], 1):
        status_tag = f"[{fact.get('inferred_status', 'INFERRED')}]"
        lines.append(f"  {i}. {status_tag} {fact['text'][:100]}")

    if count > 10:
        lines.append(f"  ... and {count - 10} more")

    lines.append("")
    lines.append("Epistemic breakdown: " + ", ".join(
        f"{status}: {c}" for status, c in sorted(status_counts.items())
    ))

    return "\n".join(lines)
