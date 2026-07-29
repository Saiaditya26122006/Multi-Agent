"""Build v2 Phase 4 — safe web/DB research with provenance.

Agents can gather external data, but web content is UNTRUSTED. The defenses:

1. Architectural isolation (the main one): retrieved content is stored as
   `external_research` EVIDENCE in the KB and consumed by agents via RAG
   grounding — never injected into an agent's instructions. Untrusted text is
   data, by construction, so a malicious page can't hijack an agent.
2. Sanitization: neutralize prompt-injection patterns before storage.
3. Provenance + trust tiering: every stored fact carries url, domain, trust
   tier, retrieval time, and the verbatim snippet, so claims built on it are
   defensible and their confidence reflects source trust.
4. Caps: bounded results per call (cost + loop runaway).

NOTE: agents that browse must NOT also have outbound-write access — enforce that
where tools are granted (a browsing agent + confidential data + a write tool is
the exfiltration path).
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_RESULTS = 4
_SNIPPET_CAP = 2000

# Patterns that indicate an attempt to hijack the agent — neutralized on ingest.
_INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|above|prior|earlier) (instructions|prompts?)",
    r"disregard (all |the )?(previous|above|prior) (instructions|prompts?)",
    r"you are now\b", r"\bsystem prompt\b", r"\bnew instructions?\b",
    r"</?(system|assistant|user)>", r"act as\b.*\b(admin|root|developer mode)",
]

# Coarse domain trust tiers. Real deployments should use a curated allow-list.
_HIGH_TRUST_SUFFIXES = (".gov", ".edu", ".int", ".ac.uk", ".gov.uk")
_MED_TRUST_DOMAINS = {
    "reuters.com", "bloomberg.com", "ft.com", "economist.com", "nature.com",
    "sciencedirect.com", "statista.com", "who.int", "worldbank.org", "oecd.org",
    "gartner.com", "mckinsey.com", "crunchbase.com",
}


def _sanitize(text: str) -> str:
    """Neutralize injection patterns and cap length. Web text is data, not code."""
    if not text:
        return ""
    for pat in _INJECTION_PATTERNS:
        text = re.sub(pat, "[removed-untrusted-directive]", text, flags=re.IGNORECASE)
    return text[:_SNIPPET_CAP].strip()


def _trust_tier(url: Optional[str]) -> str:
    if not url:
        return "low"
    try:
        domain = (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:  # noqa: BLE001
        return "low"
    if domain.endswith(_HIGH_TRUST_SUFFIXES):
        return "high"
    if domain in _MED_TRUST_DOMAINS or any(domain.endswith("." + d) for d in _MED_TRUST_DOMAINS):
        return "medium"
    return "low"


def research(query: str, session_id: Optional[str] = None,
             section_id: Optional[str] = None, max_results: int = MAX_RESULTS) -> dict:
    """Search the web, sanitize, and store results as provenance-tagged evidence.

    Returns {available, stored, results:[{url, trust_tier, chunk_id}]}. Never
    raises into the caller.
    """
    try:
        from services import search_service
    except Exception as e:  # noqa: BLE001
        logger.debug("[WebResearch] search_service unavailable: %s", e)
        return {"available": False, "stored": 0, "results": []}

    try:
        hits = search_service.search(query, max_results=min(max_results, MAX_RESULTS)) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("[WebResearch] search failed: %s", e)
        return {"available": False, "stored": 0, "results": []}

    from services.rag_service import store

    stored = []
    for h in hits:
        url = h.get("url")
        content = _sanitize(h.get("content") or h.get("snippet") or h.get("title") or "")
        if len(content) < 20:
            continue
        tier = _trust_tier(url)
        try:
            result = store(
                content=content,
                source_type="external_research",
                section=None,
                topic_tags=["web-research", section_id or "", tier],
                session_id=session_id,
                metadata={
                    "url": url,
                    "domain": (urlparse(url).netloc if url else None),
                    "trust_tier": tier,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "query": query,
                    "retrieved_by": "web_research",
                    "section_id": section_id,
                    "verbatim_snippet": content[:500],
                    "freshness_policy": "stale_after_90_days",
                },
            )
            if result:
                stored.append({"url": url, "trust_tier": tier, "chunk_id": result.id})
        except Exception as e:  # noqa: BLE001
            # Best-effort across many hits: keep going, but never silently.
            logger.error("[WebResearch] store failed for %s: %s", url, e)

    logger.info("[WebResearch] '%s' -> %d evidence chunk(s) stored", query[:60], len(stored))
    return {"available": True, "stored": len(stored), "results": stored}


def research_or_request(session_id: str, section_id: str, query: str,
                        target_nodes: list[str], description: str,
                        why: Optional[str] = None, agent: Optional[str] = None) -> dict:
    """Agent gap flow: try the web first; if it yields nothing usable, raise a
    data request to Alex (Phase 2 handshake). Returns what happened."""
    res = research(query, session_id=session_id, section_id=section_id)
    if res.get("stored"):
        return {"resolved_by": "web", **res}
    from services.data_requests import create

    req = create(session_id, section_id, target_nodes, description, why=why, agent=agent)
    return {"resolved_by": "data_request", "request": req}
