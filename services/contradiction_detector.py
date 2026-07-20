"""
Contradiction Detection Service (Hybrid LLM Judge)

Detects contradictions in knowledge_base using:
1. Semantic similarity (fast pre-filter)
2. LLM judge (accurate contradiction validation)
3. Automatic filing to BP.12 governance register
"""

import json
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import numpy as np
from memory.supabase_client import supabase as db
from services.canonical_storage import CanonicalStorage

logger = logging.getLogger(__name__)

try:
    import boto3
except ImportError:
    boto3 = None


class ContradictionDetectorError(Exception):
    """Raised when contradiction detection fails"""
    pass


class ContradictionDetector:
    """
    Hybrid approach: Semantic pre-filter + LLM judge

    Stage 1 (Fast): Find candidate pairs using semantic similarity
    Stage 2 (Accurate): Use Claude to validate if contradiction is real
    """

    def __init__(self):
        """Initialize with database and Bedrock client"""
        self.db = db
        self.canonical = CanonicalStorage()
        self.bedrock_client = self._init_bedrock()

    def _init_bedrock(self):
        """Initialize Bedrock client for Claude LLM"""
        if not boto3:
            logger.warning("[Contradiction] boto3 not installed, LLM judge disabled")
            return None

        try:
            import os
            region = os.getenv("AWS_BEDROCK_REGION", "us-east-1")
            return boto3.client("bedrock-runtime", region_name=region)
        except Exception as e:
            logger.warning(f"[Contradiction] Failed to init Bedrock: {e}")
            return None

    def detect_and_file_all(
        self,
        session_id: Optional[str] = None,
        pre_filter_threshold: float = 0.75,
        llm_confidence_threshold: float = 0.80,
    ) -> Dict:
        """
        Run full detection pipeline: find candidates → judge → file to BP.12

        Returns: {
            "detected_count": int,
            "filed_count": int,
            "by_impact": {"critical": 0, "high": 2, "medium": 3, "low": 1},
            "by_type": {"value_conflict": 3, "scope_conflict": 2, "definition_conflict": 1},
            "bp_nodes_affected": ["BP.1", "BP.2", "BP.5"],
            "sample_contradictions": [...],
        }
        """

        try:
            logger.info("[Contradiction] Starting full detection pipeline")

            # Stage 1: Find candidates (fast semantic pre-filter)
            candidates = self._find_candidate_pairs(
                session_id=session_id,
                similarity_threshold=pre_filter_threshold,
            )
            logger.info(f"[Contradiction] Found {len(candidates)} candidate pairs")

            # Stage 2: Judge each candidate (accurate LLM analysis)
            contradictions = []
            for chunk1, chunk2 in candidates:
                judgment = self._judge_contradiction(chunk1, chunk2)
                if (
                    judgment["is_contradiction"]
                    and judgment["confidence"] >= llm_confidence_threshold
                ):
                    contradictions.append(
                        {
                            "chunk1": chunk1,
                            "chunk2": chunk2,
                            "judgment": judgment,
                        }
                    )

            logger.info(f"[Contradiction] Judged {len(contradictions)} as real contradictions")

            # Stage 3: File to BP.12
            filed_ids = []
            impact_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            type_counts = {}

            for contra in contradictions:
                try:
                    bp12_id = self._file_contradiction(
                        chunk1=contra["chunk1"],
                        chunk2=contra["chunk2"],
                        judgment=contra["judgment"],
                        session_id=session_id,
                    )
                    filed_ids.append(bp12_id)

                    impact = contra["judgment"].get("impact_level", "low")
                    impact_counts[impact] = impact_counts.get(impact, 0) + 1

                    con_type = contra["judgment"].get("contradiction_type", "unknown")
                    type_counts[con_type] = type_counts.get(con_type, 0) + 1
                except Exception as e:
                    logger.error(f"[Contradiction] Error filing contradiction: {e}")

            # Get affected BP nodes
            bp_nodes_affected = set()
            for contra in contradictions:
                if "bp_section" in contra["chunk1"]:
                    domain = contra["chunk1"]["bp_section"].split(".")[0]
                    bp_nodes_affected.add(domain)

            result = {
                "detected_count": len(candidates),
                "filed_count": len(filed_ids),
                "by_impact": impact_counts,
                "by_type": type_counts,
                "bp_nodes_affected": sorted(list(bp_nodes_affected)),
                "sample_contradictions": [
                    {
                        "title": c["judgment"].get("title", "Untitled"),
                        "type": c["judgment"].get("contradiction_type", "unknown"),
                        "impact": c["judgment"].get("impact_level", "low"),
                        "confidence": c["judgment"]["confidence"],
                    }
                    for c in contradictions[:5]
                ],
            }

            logger.info(f"[Contradiction] Pipeline complete: {result}")
            return result

        except Exception as e:
            logger.error(f"[Contradiction] Pipeline error: {e}")
            raise ContradictionDetectorError(f"Pipeline failed: {str(e)}")

    def _find_candidate_pairs(
        self,
        session_id: Optional[str] = None,
        similarity_threshold: float = 0.75,
    ) -> List[Tuple[Dict, Dict]]:
        """
        Stage 1: Fast semantic pre-filter to find candidate pairs

        Returns pairs of KB entries that are similar enough to check for contradiction
        """

        try:
            logger.info(f"[Contradiction] Finding candidates (threshold={similarity_threshold})")

            # Get all KB entries with embeddings
            query = self.db.table("knowledge_base").select(
                "id, content, section, bp_section_id, confidence, embedding"
            )

            if session_id:
                query = query.eq("session_id", session_id)

            kb_entries = query.execute()

            if not kb_entries.data:
                logger.info("[Contradiction] No KB entries found")
                return []

            entries = kb_entries.data
            candidates = []

            # Compare all pairs (expensive but necessary for quality)
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    chunk1 = entries[i]
                    chunk2 = entries[j]

                    # Skip if same section (usually OK to differ)
                    if (
                        chunk1.get("section") == chunk2.get("section")
                        and chunk1.get("section")
                    ):
                        similarity = self._cosine_similarity(
                            chunk1.get("embedding"),
                            chunk2.get("embedding"),
                        )

                        if similarity >= similarity_threshold:
                            candidates.append((chunk1, chunk2))
                            logger.debug(
                                f"[Contradiction] Candidate: {chunk1['id'][:8]} ↔ {chunk2['id'][:8]} "
                                f"(similarity={similarity:.2f})"
                            )

            logger.info(f"[Contradiction] Found {len(candidates)} candidate pairs")
            return candidates

        except Exception as e:
            logger.error(f"[Contradiction] Error finding candidates: {e}")
            raise ContradictionDetectorError(f"Candidate search failed: {str(e)}")

    def _judge_contradiction(
        self,
        chunk1: Dict,
        chunk2: Dict,
    ) -> Dict:
        """
        Stage 2: Use Claude to judge if these chunks contradict

        Returns: {
            "is_contradiction": bool,
            "confidence": 0.0-1.0,
            "contradiction_type": "value_conflict" | "scope_conflict" | "definition_conflict" | "none",
            "impact_level": "critical" | "high" | "medium" | "low",
            "reasoning": str,
            "title": str,
        }
        """

        try:
            if not self.bedrock_client:
                logger.warning("[Contradiction] Bedrock not available, skipping LLM judge")
                return {
                    "is_contradiction": False,
                    "confidence": 0.0,
                    "reasoning": "LLM judge unavailable",
                }

            logger.debug(f"[Contradiction] Judging pair: {chunk1['id'][:8]} ↔ {chunk2['id'][:8]}")

            prompt = f"""Analyze these two statements and determine if they contradict each other.

Statement 1 (Source: {chunk1.get('source_type', 'unknown')}):
{chunk1.get('content', '')[:500]}

Statement 2 (Source: {chunk2.get('source_type', 'unknown')}):
{chunk2.get('content', '')[:500]}

Context:
- Section: {chunk1.get('section', 'unknown')}
- Confidence 1: {chunk1.get('confidence', 0.5)}
- Confidence 2: {chunk2.get('confidence', 0.5)}

Respond with valid JSON ONLY (no markdown, no explanation):
{{
  "is_contradiction": true/false,
  "confidence": 0.0-1.0,
  "contradiction_type": "value_conflict|scope_conflict|definition_conflict|opinion_difference|none",
  "impact_level": "critical|high|medium|low",
  "reasoning": "brief explanation",
  "title": "short title like 'Market Size Conflict'"
}}
"""

            response = self.bedrock_client.invoke_model(
                modelId="anthropic.claude-3-5-sonnet-20241022",
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-06-01",
                        "max_tokens": 500,
                        "system": "You are a contradiction detection expert. Respond ONLY with valid JSON.",
                        "messages": [{"role": "user", "content": prompt}],
                    }
                ),
            )

            response_text = json.loads(response["body"].read())["content"][0]["text"]

            # Parse JSON response (strip markdown if present)
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            judgment = json.loads(response_text.strip())

            logger.debug(
                f"[Contradiction] Judgment: is_contradiction={judgment['is_contradiction']}, "
                f"confidence={judgment['confidence']:.2f}"
            )

            return judgment

        except Exception as e:
            logger.error(f"[Contradiction] Error judging contradiction: {e}")
            return {
                "is_contradiction": False,
                "confidence": 0.0,
                "reasoning": f"Judge error: {str(e)}",
            }

    def _file_contradiction(
        self,
        chunk1: Dict,
        chunk2: Dict,
        judgment: Dict,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Stage 3: File verified contradiction to BP.12 governance register

        Returns: bp12_record_id
        """

        try:
            logger.info(
                f"[Contradiction] Filing: {judgment.get('title', 'Contradiction')} "
                f"(impact={judgment.get('impact_level', 'low')})"
            )

            bp12_id = self.canonical.file_contradiction(
                session_id=session_id or chunk1.get("session_id", ""),
                bp_node="BP.12",
                issue_type="contradiction",
                title=judgment.get("title", "Contradiction"),
                chunk1_id=chunk1.get("id"),
                chunk2_id=chunk2.get("id"),
                description=f"Type: {judgment.get('contradiction_type', 'unknown')}\n"
                f"Reasoning: {judgment.get('reasoning', '')}\n"
                f"LLM Confidence: {judgment.get('confidence', 0.0):.1%}",
            )

            logger.info(f"[Contradiction] Filed to BP.12: {bp12_id}")
            return bp12_id

        except Exception as e:
            logger.error(f"[Contradiction] Error filing contradiction: {e}")
            raise ContradictionDetectorError(f"Filing failed: {str(e)}")

    def get_bp_contradictions(
        self,
        bp_node: str,
        status: str = "open",
    ) -> List[Dict]:
        """
        Get all filed contradictions for a BP node

        Returns list of contradictions for CEO review
        """

        try:
            logger.info(f"[Contradiction] Fetching {status} contradictions for {bp_node}")

            query = self.db.table("bp12_register").select("*").eq("issue_type", "contradiction")

            if status:
                query = query.eq("status", status)

            if bp_node and bp_node != "all":
                query = query.like("bp_node", f"{bp_node}%")

            result = query.execute()

            contradictions = result.data if result.data else []
            logger.info(f"[Contradiction] Found {len(contradictions)} contradictions")

            return contradictions

        except Exception as e:
            logger.error(f"[Contradiction] Error fetching contradictions: {e}")
            raise ContradictionDetectorError(f"Fetch failed: {str(e)}")

    def resolve_contradiction(
        self,
        bp12_record_id: str,
        resolution_type: str,
        ceo_decision: str,
        canonical_value: Optional[str] = None,
    ) -> Dict:
        """
        CEO resolves a contradiction

        Args:
            resolution_type: "accepted" | "rejected" | "consolidated" | "duplicate"
            canonical_value: The single source of truth to set

        Returns: resolution_record
        """

        try:
            logger.info(
                f"[Contradiction] Resolving {bp12_record_id}: {resolution_type}"
            )

            # Update BP.12 record
            self.db.table("bp12_register").update(
                {
                    "status": "resolved",
                    "resolved_at": datetime.utcnow().isoformat(),
                    "ceo_decision": ceo_decision,
                }
            ).eq("id", bp12_record_id).execute()

            resolution_record = {
                "bp12_record_id": bp12_record_id,
                "status": "resolved",
                "resolution_type": resolution_type,
                "ceo_decision": ceo_decision,
                "canonical_value": canonical_value,
                "resolved_at": datetime.utcnow().isoformat(),
            }

            logger.info(f"[Contradiction] Resolved: {bp12_record_id}")
            return resolution_record

        except Exception as e:
            logger.error(f"[Contradiction] Error resolving contradiction: {e}")
            raise ContradictionDetectorError(f"Resolution failed: {str(e)}")

    @staticmethod
    def _cosine_similarity(embed1: Optional[List], embed2: Optional[List]) -> float:
        """Calculate cosine similarity between two embeddings"""

        if not embed1 or not embed2:
            return 0.0

        try:
            v1 = np.array(embed1, dtype=float)
            v2 = np.array(embed2, dtype=float)

            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = np.dot(v1, v2) / (norm1 * norm2)
            return float(similarity)
        except Exception as e:
            logger.debug(f"[Contradiction] Similarity calculation error: {e}")
            return 0.0
