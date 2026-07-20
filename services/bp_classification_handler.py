"""
BP Node Classification Error Handler

Resilient classification with 3 layers of error handling:
1. Stage 1 (Embedding) - Retry + fallback to keyword
2. Stage 2 (Domain detection) - Keyword-only if embedding fails
3. Stage 3 (LLM judge) - Retry + fallback to domain-only
4. Stage 4 (Prohibition check) - Permissive default
"""

import logging
import time
import json
from typing import Optional, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 0.5  # seconds, exponential backoff
BEDROCK_TIMEOUT = 30  # seconds


def retry_on_transient_error(max_attempts: int = 3, backoff: float = 0.5):
    """Decorator: retry on transient Bedrock/timeout errors."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_msg = str(e).lower()
                    # Retry on transient errors
                    is_transient = any(x in error_msg for x in [
                        "timeout", "throttl", "rate limit", "503", "429",
                        "temporary", "transient", "connection reset"
                    ])

                    if not is_transient:
                        raise  # Don't retry non-transient errors

                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = backoff * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_attempts} failed: {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time.sleep(delay)

            logger.error(f"{func.__name__} failed after {max_attempts} attempts")
            raise last_exception
        return wrapper
    return decorator


class ClassificationHandler:
    """Resilient BP node classification with graceful degradation."""

    def __init__(self):
        self.embedding_failures = 0
        self.llm_failures = 0
        self.fallback_count = 0

    @retry_on_transient_error(max_attempts=3, backoff=0.5)
    def _retrieve_embedding_candidates(self, text: str, top_k: int = 3) -> list[dict]:
        """
        Stage 1 (with retry): Get embedding-based candidates.

        Returns empty list on persistent failure (will trigger fallback).
        """
        try:
            from web.handlers.feed_handler import match_bp_node

            logger.debug(f"[Classification] Stage 1: Retrieving embedding candidates for '{text[:60]}'")
            candidates = match_bp_node(text, top_k=top_k)

            if candidates:
                logger.info(f"[Classification] Stage 1 success: {len(candidates)} candidates")
                return candidates
            else:
                logger.warning("[Classification] Stage 1: RAG returned empty (threshold too high or no matches)")
                return []

        except Exception as e:
            self.embedding_failures += 1
            logger.error(f"[Classification] Stage 1 failed: {e}")
            raise  # Re-raise for retry decorator

    def _detect_domains_fallback(self, text: str) -> list[str]:
        """
        Stage 2 (fallback): Detect domains when embedding fails.

        Uses keyword-only matching, no LLM calls.
        """
        try:
            from web.handlers.feed_handler import _detect_likely_domains

            logger.debug("[Classification] Stage 2: Detecting domains via keyword matching")
            domains = _detect_likely_domains(text)

            if domains:
                logger.info(f"[Classification] Stage 2 success: {domains}")
                return domains
            else:
                logger.warning("[Classification] Stage 2: No domains detected, using broad search")
                from web.handlers.feed_handler import _detect_likely_domains_broad
                return _detect_likely_domains_broad(text)

        except Exception as e:
            logger.error(f"[Classification] Stage 2 failed: {e}")
            return []  # Return empty, not fatal

    def _get_domain_nodes(self, text: str, domains: list[str], exclude_ids: set, max_inject: int = 15) -> list[dict]:
        """
        Stage 2b: Get nodes from detected domains.

        Keyword-based ranking, no embedding needed.
        """
        try:
            if not domains:
                return []

            from web.handlers.feed_handler import _get_domain_nodes_ranked

            logger.debug(f"[Classification] Getting nodes for domains: {domains}")
            nodes = _get_domain_nodes_ranked(text, domains, exclude_ids, max_inject)
            logger.info(f"[Classification] Got {len(nodes)} domain nodes")
            return nodes

        except Exception as e:
            logger.error(f"[Classification] Failed to get domain nodes: {e}")
            return []

    @retry_on_transient_error(max_attempts=3, backoff=1.0)
    def _judge_candidates_llm(
        self,
        text: str,
        candidates: list[dict],
        document_context: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Stage 3 (with retry): Use LLM to judge candidate pool.

        Returns None on persistent failure (will use best-candidate fallback).
        """
        try:
            if not candidates:
                logger.warning("[Classification] Stage 3: No candidates to judge")
                return None

            from web.handlers.llm_helper import _get_client
            import os

            logger.debug(f"[Classification] Stage 3: LLM judging {len(candidates)} candidates")

            client = _get_client()
            model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

            # Build candidate descriptions for LLM
            candidate_list = "\n".join([
                f"- {c['node_id']}: {c['node_title']} (similarity: {c.get('similarity', 'N/A')})"
                for c in candidates[:15]
            ])

            system_prompt = (
                "You are a precise BP node classifier. Given a fact and candidate nodes, "
                "pick THE SINGLE BEST node based on:\n"
                "1. Semantic match\n"
                "2. Required output alignment\n"
                "3. Prohibition compliance\n\n"
                "Respond with ONLY valid JSON: "
                '{"node_id": "BP.X.Y.Z", "confidence": "high|medium|low", "reasoning": "one sentence"}'
            )

            user_message = (
                f"FACT: \"{text[:500]}\"\n\n"
                f"CANDIDATE NODES:\n{candidate_list}\n\n"
                f"Pick the SINGLE BEST node. Return JSON only."
            )

            if document_context:
                user_message += f"\n\nDOCUMENT CONTEXT: {document_context[:200]}"

            response = client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": 150},
            )

            raw = response["output"]["message"]["content"][0]["text"].strip()

            # Strip markdown if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.split("```")[0].strip()

            result = json.loads(raw)
            logger.info(f"[Classification] Stage 3 success: {result['node_id']}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[Classification] Stage 3: Invalid JSON response: {e}")
            raise  # Will trigger retry
        except Exception as e:
            self.llm_failures += 1
            logger.error(f"[Classification] Stage 3 LLM call failed: {e}")
            raise  # Will trigger retry

    def _check_prohibitions(self, fact_text: str, node_id: str) -> tuple[bool, str]:
        """
        Stage 4: Check if fact violates node prohibitions.

        Permissive fallback: if regex fails, allow the classification.
        """
        try:
            from web.handlers.feed_handler import _check_prohibition_violation

            violated, reason = _check_prohibition_violation(fact_text, node_id)

            if violated:
                logger.warning(f"[Classification] Prohibition violation: {reason}")
                return True, reason

            return False, ""

        except Exception as e:
            logger.warning(f"[Classification] Prohibition check failed (permissive): {e}")
            return False, ""  # Permissive: allow on error

    def _get_best_candidate_fallback(self, candidates: list[dict]) -> Optional[dict]:
        """
        Fallback: pick best candidate by similarity when LLM fails.

        Used when Stage 3 (LLM judge) fails completely.
        """
        if not candidates:
            return None

        # Sort by similarity descending
        sorted_candidates = sorted(
            candidates,
            key=lambda x: x.get("similarity", 0),
            reverse=True
        )

        best = sorted_candidates[0]
        logger.warning(
            f"[Classification] Using fallback: {best['node_id']} "
            f"(similarity: {best.get('similarity', 'N/A')})"
        )

        return {
            "node_id": best["node_id"],
            "node_title": best.get("node_title", ""),
            "confidence": "low",  # Mark as low confidence
            "reasoning": "LLM judge failed, using embedding similarity fallback",
        }

    def classify(
        self,
        text: str,
        session_id: Optional[str] = None,
        document_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resilient 4-stage classification with graceful degradation.

        Returns:
            {
                "node_id": "BP.X.Y.Z",
                "node_title": "...",
                "confidence": "high|medium|low",
                "reasoning": "...",
                "fallback_used": bool,
                "stage_failed": None or int (1-3),
            }
        """
        try:
            logger.info(f"[Classification] Starting classification: '{text[:60]}...'")

            # Stage 1: Get embedding candidates (with retry)
            embedding_candidates = []
            try:
                embedding_candidates = self._retrieve_embedding_candidates(text, top_k=3)
            except Exception as e:
                logger.warning(f"[Classification] Embedding stage exhausted retries: {e}")

            # Stage 2: Detect domains and get domain nodes
            domains = self._detect_domains_fallback(text)
            domain_candidates = self._get_domain_nodes(
                text,
                domains,
                exclude_ids=set(c["node_id"] for c in embedding_candidates),
                max_inject=15
            )

            # Merge candidates (embedding first, then domain)
            all_candidates = embedding_candidates + domain_candidates

            if not all_candidates:
                logger.error("[Classification] No candidates found in either stage")
                return {
                    "node_id": None,
                    "confidence": "low",
                    "reasoning": "No matching nodes found",
                    "fallback_used": False,
                    "stage_failed": 2,
                    "none_fit": True,
                }

            logger.debug(f"[Classification] Candidate pool size: {len(all_candidates)}")

            # Stage 3: LLM judge (with retry)
            judgment = None
            try:
                judgment = self._judge_candidates_llm(text, all_candidates, document_context)
            except Exception as e:
                logger.warning(f"[Classification] LLM judge exhausted retries: {e}")

            # Fallback if LLM judge failed
            if judgment is None:
                self.fallback_count += 1
                judgment = self._get_best_candidate_fallback(all_candidates)

                if judgment is None:
                    return {
                        "node_id": None,
                        "confidence": "low",
                        "reasoning": "LLM judge failed and no fallback candidates",
                        "fallback_used": True,
                        "stage_failed": 3,
                        "none_fit": True,
                    }

                judgment["fallback_used"] = True
                judgment["stage_failed"] = 3
            else:
                judgment["fallback_used"] = False
                judgment["stage_failed"] = None

            # Stage 4: Check prohibitions
            node_id = judgment.get("node_id")
            if node_id:
                violated, reason = self._check_prohibitions(text, node_id)
                if violated:
                    judgment["prohibition_violated"] = True
                    judgment["prohibition_reason"] = reason
                    judgment["confidence"] = "low"

            logger.info(f"[Classification] Final result: {judgment['node_id']} ({judgment['confidence']})")
            return judgment

        except Exception as e:
            logger.error(f"[Classification] Unexpected error: {e}", exc_info=True)
            return {
                "node_id": None,
                "confidence": "low",
                "reasoning": f"Classification error: {str(e)[:100]}",
                "fallback_used": False,
                "stage_failed": None,
                "error": True,
            }

    def get_stats(self) -> Dict[str, int]:
        """Return classification error statistics."""
        return {
            "embedding_failures": self.embedding_failures,
            "llm_failures": self.llm_failures,
            "fallback_used": self.fallback_count,
        }


# Global instance
classifier = ClassificationHandler()


def classify_fact(
    text: str,
    session_id: Optional[str] = None,
    document_context: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience function: classify a fact with resilient error handling."""
    return classifier.classify(text, session_id, document_context)
