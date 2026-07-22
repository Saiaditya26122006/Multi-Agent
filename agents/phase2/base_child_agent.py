"""
Base class for all Phase 2 child agents.

Uses MessageBus for in-process async communication (SPADE removed).

Eliminates duplicated boilerplate across 10 agents by providing:
- Bedrock LLM calls with retry + exponential backoff
- Message handling (send, escalate, inform) via either transport
- Intelligence Engine integration with enforced reasoning chain
- Structured failure handling (retry-simple → partial → refuse)
- Pre/post consistency checks against cross-section data
- Standard handle_request flow

Subclasses must implement:
- SYSTEM_PROMPT: str
- AGENT_NAME: str
- AGENT_ROLE: str
- SECTION_NUMBER: str
- MODEL_ENV: str (env var name for model ID)
- INPUT_SCHEMA: type (Pydantic model)
- OUTPUT_SCHEMA: type (Pydantic model)
- _build_schema_prompt() -> str
- _build_prompt(validated_input) -> str
- _extract_input(input_package, task) -> dict (kwargs for INPUT_SCHEMA)
- _build_ie_input_data(input_package) -> dict
- _fallback_defaults(validated_input) -> dict

Optionally override:
- handle_revise() — for council-gated agents
- _derive_from_inputs() — domain-specific partial output derivation
- _evaluate_proposal() — custom proposal evaluation logic
- reasoning_budget(revision_required: bool) -> int
"""

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import boto3
from botocore.config import Config as BotoConfig
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from agents.phase2.message_bus import MessageBus, ACLMessage
from agents.phase2.agent_beliefs import AgentBeliefStore
from memory.redis_client import RedisClient  # ephemeral agent state (beliefs, task output, ready signals)

logger = logging.getLogger(__name__)

BEDROCK_CONFIG = BotoConfig(
    read_timeout=300,
    connect_timeout=10,
    retries={"max_attempts": 0},
)

_shared_bedrock_client = None


def get_shared_bedrock_client():
    """Singleton Bedrock client shared across all child agents."""
    global _shared_bedrock_client
    if _shared_bedrock_client is None:
        _shared_bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
            config=BEDROCK_CONFIG,
        )
    return _shared_bedrock_client


class CircuitBreaker:
    """Trips after consecutive failures, failing fast during cooldown."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if (time.time() - self._last_failure_time) > self._cooldown_seconds:
                self._state = "half_open"
                return False
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        if self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            logger.warning(
                "Circuit breaker OPEN — %d consecutive failures, cooling down %ds",
                self._consecutive_failures, self._cooldown_seconds,
            )

    @property
    def state(self) -> str:
        return self._state


_bedrock_circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)


def get_circuit_breaker() -> CircuitBreaker:
    return _bedrock_circuit_breaker


class BaseChildAgent(ABC):
    """Abstract base for all Phase 2 section-producing agents.

    Primary transport: MessageBus (in-process async).
    Legacy fallback: SPADE/XMPP (if bus not provided and SPADE_AVAILABLE).
    """

    SYSTEM_PROMPT: str = ""
    AGENT_NAME: str = ""
    AGENT_ROLE: str = ""
    SECTION_NUMBER: str = ""
    MODEL_ENV: str = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT: str = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA: type = None
    OUTPUT_SCHEMA: type = None

    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BACKOFF: tuple = (2, 4, 8)
    LLM_MAX_TOKENS: int = 4096

    def __init__(self, bus: Optional[MessageBus] = None):
        self.redis = RedisClient()
        self.model_id = os.getenv(self.MODEL_ENV, self.MODEL_DEFAULT)
        self.bedrock = get_shared_bedrock_client()
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)
        self.beliefs = AgentBeliefStore(
            agent_name=self.AGENT_NAME, redis_client=self.redis
        )
        self._bus = bus
        from agents.phase2.knowledge_graph import get_knowledge_graph
        self._knowledge_graph = get_knowledge_graph()

    async def start(self, bus: Optional[MessageBus] = None, register_as: Optional[str] = None) -> None:
        """Register this agent on the message bus and signal readiness.

        `register_as` should be the agent's roster key (e.g. "environment_research").
        Falls back to a derived name from AGENT_NAME when not provided, but that
        derived name will NOT match snake_case roster keys for multi-word agents —
        callers dispatching via the roster (e.g. PipelineOrchestrator) must pass it.
        """
        if bus is not None:
            self._bus = bus
        agent_key = register_as or self.AGENT_NAME.lower().replace(" ", "_")
        if self._bus is not None:
            self._bus.register(agent_key, self._handle_bus_message)
        signal_ready(self.redis, agent_key)
        logger.info("[%s] Started as '%s' (bus=%s)", self.AGENT_NAME, agent_key, self._bus is not None)

    async def _handle_bus_message(self, message: ACLMessage) -> Any:
        """Route incoming bus messages to the appropriate handler."""
        if message.performative == "request":
            return await self.handle_request(
                message.task_id, message.session_id,
                message.pipeline_run_id, message.content,
            )
        elif message.performative == "revise":
            return await self.handle_revise(
                message.task_id, message.session_id,
                message.pipeline_run_id, message.content,
            )
        elif message.performative == "propose":
            return await self.handle_propose(
                message.task_id, message.session_id,
                message.pipeline_run_id, message.sender, message.content,
            )
        elif message.performative == "query":
            return await self.handle_query(message)
        return None

    async def handle_query(self, message: ACLMessage) -> Any:
        """Handle direct agent-to-agent queries. Override for custom logic."""
        return {"status": "not_implemented", "agent": self.AGENT_NAME}

    # ── Message sending (bus-first, SPADE fallback) ──────────────────────────

    async def _escalate(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        trigger: str,
        notes: str,
    ):
        content = {
            "trigger": trigger,
            "notes": notes,
            "section": self.SECTION_NUMBER,
            "gap_key": self._default_gap_key(),
        }
        await self._send_to_mother("escalate", task_id, session_id, pipeline_run_id, content)

    async def _send_inform(self, task_id: str, session_id: str, pipeline_run_id: str, output: dict):
        content = {"output": output, "section_number": self.SECTION_NUMBER}
        await self._send_to_mother("inform", task_id, session_id, pipeline_run_id, content)

    async def _send_response(self, task_id: str, session_id: str, pipeline_run_id: str, performative: str, content: dict):
        await self._send_to_mother(performative, task_id, session_id, pipeline_run_id, content)

    async def _send_to_mother(
        self, performative: str, task_id: str, session_id: str, pipeline_run_id: str, content: dict
    ):
        """Send via message bus (preferred) or SPADE fallback."""
        if self._bus is not None:
            msg = ACLMessage(
                sender=self.AGENT_NAME.lower().replace(" ", "_"),
                receiver="mother_agent",
                performative=performative,
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
            await self._bus.send(msg)
            return
        logger.error("[%s] No message bus — cannot send %s", self.AGENT_NAME, performative)

    async def query_agent(self, agent_name: str, query_content: dict, task_id: str, session_id: str, pipeline_run_id: str, timeout: float = 30.0) -> Any:
        """Direct agent-to-agent query via message bus."""
        if self._bus is None:
            logger.error("[%s] No bus — cannot query %s", self.AGENT_NAME, agent_name)
            return None
        msg = ACLMessage(
            sender=self.AGENT_NAME.lower().replace(" ", "_"),
            receiver=agent_name,
            performative="query",
            content=query_content,
            task_id=task_id,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
        )
        return await self._bus.request_response(msg, timeout=timeout)

    # ── LLM call with retry + backoff ────────────────────────────────────────

    async def _call_llm(self, user_message: str) -> tuple[Optional[str], dict]:
        """Call Bedrock with exponential backoff + circuit breaker. Returns (text, usage) or (None, {})."""
        circuit = get_circuit_breaker()
        if circuit.is_open:
            logger.warning("[%s] Circuit breaker OPEN — failing fast", self.AGENT_NAME)
            return None, {}

        for attempt in range(self.LLM_MAX_RETRIES):
            try:
                response = self.bedrock.converse(
                    modelId=self.model_id,
                    system=[{"text": self.SYSTEM_PROMPT}],
                    messages=[{"role": "user", "content": [{"text": user_message}]}],
                    inferenceConfig={"maxTokens": self.LLM_MAX_TOKENS},
                )
                usage = response.get("usage", {})
                text = response["output"]["message"]["content"][0]["text"]
                circuit.record_success()
                return text, {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                }
            except (
                self.bedrock.exceptions.ThrottlingException,
            ) as e:
                circuit.record_failure()
                wait = self.LLM_RETRY_BACKOFF[min(attempt, len(self.LLM_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "[%s] Throttled — retrying in %ds (attempt %d/%d)",
                    self.AGENT_NAME, wait, attempt + 1, self.LLM_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
            except Exception as e:
                circuit.record_failure()
                if "timeout" in str(e).lower() or "ReadTimeout" in type(e).__name__:
                    wait = self.LLM_RETRY_BACKOFF[min(attempt, len(self.LLM_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "[%s] Timeout — retrying in %ds (attempt %d/%d): %s",
                        self.AGENT_NAME, wait, attempt + 1, self.LLM_MAX_RETRIES, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("[%s] LLM call failed (non-retryable): %s", self.AGENT_NAME, e)
                    return None, {}

        logger.error("[%s] All %d LLM retries exhausted", self.AGENT_NAME, self.LLM_MAX_RETRIES)
        return None, {}

    # ── Standard request handling ────────────────────────────────────────────

    async def handle_request(self, task_id: str, session_id: str, pipeline_run_id: str, content: dict):
        """Enforced flow: validate → pre-check → IE reason → fallback → post-audit → inform."""
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            input_kwargs = self._extract_input(input_package, task)
            input_kwargs["task_id"] = task_id
            input_kwargs["session_id"] = session_id
            validated_input = self.INPUT_SCHEMA(**input_kwargs)
        except Exception as e:
            logger.error("[%s] Input validation failed: %s", self.AGENT_NAME, e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += (
                f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\n"
                "Fix these issues. Do NOT weaken your analysis — make it more rigorous."
            )

        # PRE-CHECK: Extract constraints from prior sections
        constraints = self._pre_check_consistency(cross_context)
        if constraints:
            learning_context += (
                "\n\nCONSTRAINTS FROM PRIOR SECTIONS (your output MUST be consistent with these):\n"
                + "\n".join(f"  - {c}" for c in constraints)
            )

        # BELIEFS: Inject agent's persistent beliefs into context
        belief_context = self.beliefs.get_beliefs_for_prompt()
        if belief_context:
            learning_context += f"\n\n{belief_context}"

        # BELIEFS: Check for conflicts with incoming cross-section data
        if cross_context:
            conflicts = self.beliefs.get_conflicts_with(cross_context)
            if conflicts:
                conflict_summary = "\n".join(
                    f"  - My belief '{c.get('my_belief', '')}' conflicts with "
                    f"incoming '{c.get('incoming', '')}'"
                    for c in conflicts[:3]
                )
                learning_context += (
                    f"\n\nBELIEF CONFLICTS (resolve these in your output):\n{conflict_summary}"
                )

        # RAG: Enrich with relevant knowledge from the vector store
        rag_context = self._rag_enrich()
        if rag_context:
            learning_context += f"\n\nCEO DATA & PRIOR KNOWLEDGE (from RAG):\n{rag_context}"

        # RAG GROUNDING (Task 4c): Store evidence for later verification
        rag_evidence = self._store_rag_evidence(rag_context)

        input_data = self._build_ie_input_data(input_package)

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=self.AGENT_ROLE,
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=self.reasoning_budget(revision_required),
            learning_context=learning_context,
        )

        if not parsed:
            # Structured failure handling — not template dump
            parsed, fallback_usage = await self._handle_llm_failure(
                task_id, session_id, pipeline_run_id, validated_input, token_usage
            )
            if parsed is None:
                return
            token_usage["input_tokens"] = token_usage.get("input_tokens", 0) + fallback_usage.get("input_tokens", 0)
            token_usage["output_tokens"] = token_usage.get("output_tokens", 0) + fallback_usage.get("output_tokens", 0)

        parsed = self._augment_parsed(parsed, input_package)

        try:
            parsed["task_id"] = task_id
            parsed["model_used"] = self.model_id
            parsed["input_tokens"] = token_usage.get("input_tokens", 0)
            parsed["output_tokens"] = token_usage.get("output_tokens", 0)
            validated_output = self.OUTPUT_SCHEMA(**parsed)
        except Exception as e:
            logger.error("[%s] Output validation failed: %s", self.AGENT_NAME, e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace

        # BELIEFS: Update agent's beliefs from produced output
        self.beliefs.update_from_output(result)

        # POST-AUDIT: Self-check for contradictions with prior sections
        audit_warnings = self._post_audit_consistency(result, cross_context)
        if audit_warnings:
            result["_self_audit_warnings"] = audit_warnings
            if result.get("confidence_score") == "high":
                result["confidence_score"] = "medium"
                result["_confidence_reason"] = "Self-audit found potential contradictions"

        # RAG GROUNDING (Task 4c): Verify claims against evidence + tag ASSUMPTION
        grounding_score, ungrounded_claims = await self._verify_rag_grounding(result, rag_evidence)
        result["_grounding_score"] = grounding_score
        result["_grounding_metadata"] = {
            "total_claims_checked": len(await self._extract_factual_claims(result)),
            "ungrounded_claims": ungrounded_claims[:5],
            "threshold": 0.6,
        }

        # Tag ungrounded claims as ASSUMPTION
        if ungrounded_claims:
            result["_tagged_assumptions"] = ungrounded_claims
            if result.get("confidence_score") in ("high", "medium"):
                result["confidence_score"] = "medium"
                result["_confidence_reason"] = f"{len(ungrounded_claims)} claim(s) lack RAG evidence"

        # Auto-revise if <50% grounded (Task 4c: Revision Trigger)
        if grounding_score < 0.5 and not revision_required:
            logger.warning(
                "[%s] Low grounding score (%.1f%%) — triggering automatic revision",
                self.AGENT_NAME, grounding_score * 100
            )
            # Create revision feedback
            revision_feedback = (
                f"Your output has insufficient grounding in the evidence provided. "
                f"Only {grounding_score*100:.0f}% of claims are supported by RAG data. "
                f"Ungrounded claims: {', '.join(ungrounded_claims[:3])}\n\n"
                f"Please revise: Ground EVERY claim in the CEO data/evidence provided, "
                f"or explicitly mark it as ASSUMPTION if you cannot ground it."
            )

            # Trigger revision by updating session with revision_required flag
            try:
                self.db.table("sessions").update({
                    "revision_required": True,
                    "revision_feedback": revision_feedback,
                }).eq("id", session_id).execute()
                logger.info("[%s] Revision triggered for low grounding", self.AGENT_NAME)
            except Exception as e:
                logger.error("[%s] Failed to trigger revision: %s", self.AGENT_NAME, e)

        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)

        # Publish key facts to the shared knowledge graph
        self._publish_to_knowledge_graph(result)

        await self._post_process(task_id, session_id, pipeline_run_id, validated_input, result)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    # ── Structured failure handling (replaces template fallback) ──────────────

    async def _handle_llm_failure(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        validated_input,
        token_usage: dict,
    ) -> tuple[Optional[dict], dict]:
        """Intelligent failure handling: retry simple → partial → refuse."""
        # Strategy 1: Retry with drastically simplified prompt
        simple_prompt = self._build_minimal_prompt(validated_input)
        llm_response, usage = await self._call_llm(simple_prompt)
        if llm_response:
            parsed = self._parse_llm_response(llm_response, validated_input)
            if parsed:
                parsed["_generation_mode"] = "simplified_retry"
                parsed["confidence_score"] = "low"
                logger.info("[%s] Recovered via simplified retry", self.AGENT_NAME)
                return parsed, usage

        # Strategy 2: Produce partial output from inputs
        partial = self._derive_from_inputs(validated_input)
        if partial and len([k for k in partial if not k.startswith("_")]) >= 3:
            partial["_generation_mode"] = "partial_from_inputs"
            partial["confidence_score"] = "low"
            partial["_missing_fields"] = self._identify_missing_fields(partial)
            logger.info("[%s] Produced partial output from inputs", self.AGENT_NAME)
            return partial, usage or {}

        # Strategy 3: Refuse — tell Mother this section cannot be produced
        logger.warning("[%s] All strategies exhausted — refusing task", self.AGENT_NAME)
        refusal = {
            "_status": "refused",
            "_reason": "LLM failed after simplified retry and partial derivation",
            "_available_inputs": list(vars(validated_input).keys()) if hasattr(validated_input, '__dict__') else [],
            "confidence_score": "none",
            "section_number": str(self.SECTION_NUMBER),
        }
        await self._escalate(
            task_id, session_id, pipeline_run_id,
            "weak_evidence",
            f"All generation strategies failed for section {self.SECTION_NUMBER}",
        )
        return None, usage or {}

    def _build_minimal_prompt(self, validated_input) -> str:
        """Ultra-simplified prompt for retry after IE failure."""
        input_dict = validated_input.model_dump() if hasattr(validated_input, 'model_dump') else vars(validated_input)
        key_fields = {k: v for k, v in input_dict.items() if v and k not in ("task_id", "session_id")}
        return (
            f"Given this input: {json.dumps(key_fields, default=str)[:2000]}\n\n"
            f"Produce a JSON output for business plan section {self.SECTION_NUMBER}.\n"
            f"Be specific with numbers. Use 'low' confidence if unsure.\n\n"
            f"{self._build_schema_prompt()}"
        )

    def _derive_from_inputs(self, validated_input) -> dict:
        """Extract what we can directly from inputs without LLM. Override per agent."""
        return {}

    def _identify_missing_fields(self, partial: dict) -> list:
        """Identify which schema fields are missing from partial output."""
        if not self.OUTPUT_SCHEMA:
            return []
        try:
            schema_fields = set(self.OUTPUT_SCHEMA.model_fields.keys())
            present_fields = set(k for k in partial.keys() if not k.startswith("_"))
            return list(schema_fields - present_fields)
        except Exception:
            return []

    # ── Knowledge Graph integration ────────────────────────────────────────────

    def _publish_to_knowledge_graph(self, result: dict) -> None:
        """Extract key facts from output and add to shared knowledge graph."""
        try:
            confidence_map = {"high": 0.9, "medium": 0.6, "low": 0.3, "none": 0.1}
            confidence = confidence_map.get(result.get("confidence_score", "medium"), 0.5)

            # Add main output as a fact
            summary = result.get("executive_summary", "") or result.get("summary", "") or ""
            if summary and len(summary) > 20:
                self._knowledge_graph.add_fact(
                    content=summary[:500],
                    provenance=self.AGENT_NAME.lower().replace(" ", "_"),
                    confidence=confidence,
                    section=str(self.SECTION_NUMBER),
                    category="section_output",
                )

            # Add quantitative claims as individual facts
            for key in ("revenue_assumptions", "pricing", "break_even_analysis",
                        "target_market_size", "headcount_plan", "icp_hypothesis"):
                if key in result and result[key]:
                    self._knowledge_graph.add_fact(
                        content=f"{key}: {json.dumps(result[key], default=str)[:300]}",
                        provenance=self.AGENT_NAME.lower().replace(" ", "_"),
                        confidence=confidence,
                        section=str(self.SECTION_NUMBER),
                        category=key,
                    )
        except Exception as e:
            logger.debug("[%s] Knowledge graph publish failed: %s", self.AGENT_NAME, e)

    # ── RAG retrieval ─────────────────────────────────────────────────────────

    def _rag_enrich(self) -> str:
        """Retrieve relevant context from the RAG knowledge base for this agent."""
        try:
            from agents.phase2.rag_mixin import rag_enrich
            query = f"{self.AGENT_ROLE} section {self.SECTION_NUMBER}"
            return rag_enrich(query, section=self.SECTION_NUMBER, top_k=5, max_chars=1200)
        except Exception:
            return ""

    # ── RAG Grounding (Task 4a: Storage & Claim Extraction) ──────────────────

    def _store_rag_evidence(self, rag_context: str) -> list[dict]:
        """Store RAG-retrieved evidence chunks for later verification."""
        if not rag_context:
            return []

        try:
            from services.rag_service import retrieve
            query = f"{self.AGENT_ROLE} section {self.SECTION_NUMBER}"
            chunks = retrieve(
                query,
                section=self.SECTION_NUMBER,
                top_k=10,
                threshold=0.35,
            )
            # Convert Chunk objects to dicts for storage
            evidence = []
            for chunk in chunks:
                evidence.append({
                    "id": chunk.id,
                    "content": chunk.content,
                    "source_type": chunk.source_type,
                    "similarity": chunk.similarity,
                    "epistemic_status": chunk.epistemic_status,
                })
            self._rag_evidence = evidence
            logger.debug(
                "[%s] Stored %d RAG evidence chunks for grounding verification",
                self.AGENT_NAME, len(evidence)
            )
            return evidence
        except Exception as e:
            logger.warning("[%s] Failed to store RAG evidence: %s", self.AGENT_NAME, e)
            self._rag_evidence = []
            return []

    async def _extract_factual_claims(self, output: dict) -> list[str]:
        """Extract all factual claims from agent output via LLM call."""
        try:
            output_str = json.dumps(output, default=str)[:2000]

            prompt = f"""Extract ONLY factual/quantitative claims from this output.
Return a JSON list of claims (max 10). Format:
{{"claims": ["claim 1", "claim 2", ...]}}

Output to analyze:
{output_str}"""

            response = await self._call_bedrock_simple(
                model_id=os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
                system_prompt="You are a claims extractor. Find only verifiable factual statements.",
                user_prompt=prompt,
                max_tokens=400,
            )

            result = json.loads(response)
            claims = result.get("claims", [])
            logger.debug("[%s] Extracted %d factual claims", self.AGENT_NAME, len(claims))
            return claims[:10]
        except Exception as e:
            logger.warning("[%s] Failed to extract claims: %s", self.AGENT_NAME, e)
            return []

    async def _call_bedrock_simple(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 500,
    ) -> str:
        """Simple Bedrock call without full agent machinery. Uses shared client."""
        try:
            response = self.bedrock.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
            return response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("[%s] Bedrock call failed: %s", self.AGENT_NAME, e)
            return "{}"

    # ── Cross-section consistency ────────────────────────────────────────────

    def _pre_check_consistency(self, cross_context: dict) -> list[str]:
        """Before producing, identify binding constraints from prior sections."""
        if not cross_context:
            return []

        constraints = []
        for section, data in cross_context.items():
            if not isinstance(data, dict):
                continue
            # Extract quantitative constraints
            for key in ("revenue_assumptions", "pricing", "target_market_size",
                        "headcount_plan", "break_even_analysis", "icp_hypothesis"):
                if key in data and data[key]:
                    constraints.append(
                        f"Section {section} established {key}: "
                        f"{json.dumps(data[key], default=str)[:200]}"
                    )
        return constraints[:10]

    async def _verify_rag_grounding(
        self,
        output: dict,
        rag_evidence: list[dict],
    ) -> tuple[float, list[str]]:
        """Verify claims are grounded in RAG evidence. Return (grounding_score, ungrounded_claims)."""
        if not rag_evidence:
            return 0.0, []

        try:
            # Extract claims from output
            claims = await self._extract_factual_claims(output)
            if not claims:
                return 1.0, []

            # Embed the evidence ONCE. It is identical for every claim, so doing it
            # inside the per-claim loop cost len(claims) x len(evidence) embed calls
            # (110 for the default 10x10) at ~800ms each — roughly 90s per section,
            # which alone exceeded the agent's timeout.
            evidence_embeddings = self._embed_evidence(rag_evidence)
            if not evidence_embeddings:
                return 1.0, []

            ungrounded = []
            for claim in claims:
                is_grounded = await self._check_claim_grounding(claim, evidence_embeddings)
                if not is_grounded:
                    ungrounded.append(claim)

            grounding_score = (len(claims) - len(ungrounded)) / len(claims)
            logger.info(
                "[%s] Grounding check: %d/%d claims grounded (%.1f%%)",
                self.AGENT_NAME, len(claims) - len(ungrounded), len(claims),
                grounding_score * 100
            )
            return grounding_score, ungrounded
        except Exception as e:
            logger.error("[%s] Grounding verification failed: %s", self.AGENT_NAME, e)
            return 1.0, []

    def _embed_evidence(self, rag_evidence: list[dict]) -> list[list[float]]:
        """Get one embedding per evidence chunk, for reuse across every claim.

        These chunks came out of the knowledge base, which already stores the
        embedding it indexed them by — re-deriving it costs a ~400ms Bedrock round
        trip per chunk to recompute a vector we already have. Fetch them in one
        query and only embed whatever is genuinely missing.
        """
        stored = self._fetch_stored_embeddings(
            [e["id"] for e in rag_evidence if e.get("id")]
        )

        embeddings = []
        for evidence in rag_evidence:
            content = evidence.get("content", "")
            if not content:
                continue

            cached = stored.get(evidence.get("id"))
            if cached:
                embeddings.append(cached)
                continue

            try:
                from services.rag_service import embed

                embeddings.append(embed(content, input_type="search_document"))
            except Exception as e:
                logger.warning(
                    "[%s] Failed to embed evidence chunk: %s", self.AGENT_NAME, e
                )
        return embeddings

    def _fetch_stored_embeddings(self, chunk_ids: list[str]) -> dict[str, list[float]]:
        """Read the embeddings the knowledge base already holds for these chunks."""
        if not chunk_ids:
            return {}

        try:
            from services.rag_service import _get_supabase, TABLE_NAME

            rows = (
                _get_supabase()
                .table(TABLE_NAME)
                .select("id,embedding")
                .in_("id", chunk_ids)
                .execute()
            )

            out = {}
            for row in rows.data or []:
                vec = row.get("embedding")
                # pgvector comes back as a JSON-ish string over PostgREST.
                if isinstance(vec, str):
                    try:
                        vec = json.loads(vec)
                    except ValueError:
                        continue
                if isinstance(vec, list) and vec:
                    out[row["id"]] = vec
            return out
        except Exception as e:
            logger.warning(
                "[%s] Could not read stored embeddings, will re-embed: %s",
                self.AGENT_NAME, e,
            )
            return {}

    async def _check_claim_grounding(
        self, claim: str, evidence_embeddings: list[list[float]]
    ) -> bool:
        """Check if a claim is supported by pre-embedded RAG evidence."""
        if not evidence_embeddings:
            return False

        try:
            from services.rag_service import embed

            # Embed the claim
            claim_embedding = embed(claim, input_type="search_query")

            # Check similarity against the pre-computed evidence embeddings
            best_similarity = 0.0
            for evidence_embedding in evidence_embeddings:
                similarity = self._cosine_similarity(claim_embedding, evidence_embedding)

                if similarity > best_similarity:
                    best_similarity = similarity

            # Threshold: 0.6 means 60% similarity required for grounding
            is_grounded = best_similarity >= 0.6
            logger.debug(
                "[%s] Claim grounding: '%s...' → similarity=%.2f (grounded=%s)",
                self.AGENT_NAME, claim[:50], best_similarity, is_grounded
            )
            return is_grounded
        except Exception as e:
            logger.warning("[%s] Claim grounding check failed: %s", self.AGENT_NAME, e)
            return False

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = sum(x ** 2 for x in vec_a) ** 0.5
        mag_b = sum(x ** 2 for x in vec_b) ** 0.5

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot_product / (mag_a * mag_b)

    def _post_audit_consistency(self, output: dict, cross_context: dict) -> list[str]:
        """After producing, check for contradictions with prior sections."""
        if not cross_context:
            return []

        warnings = []
        output_str = json.dumps(output, default=str).lower()

        for section, data in cross_context.items():
            if not isinstance(data, dict):
                continue
            # Check numeric contradictions
            for key, value in data.items():
                if isinstance(value, (int, float)) and value > 0:
                    if key in output and isinstance(output.get(key), (int, float)):
                        our_value = output[key]
                        if our_value > 0 and abs(our_value - value) / value > 0.5:
                            warnings.append(
                                f"Possible contradiction: our {key}={our_value} "
                                f"vs section {section} {key}={value} (>50% divergence)"
                            )

        return warnings[:5]

    # ── Proposal handling (default: always accept) ───────────────────────────

    async def handle_propose(self, task_id: str, session_id: str, pipeline_run_id: str, sender: str, content: dict):
        proposal = content.get("proposal", "")
        if self._evaluate_proposal(proposal):
            await self._send_response(
                task_id, session_id, pipeline_run_id,
                "inform", {"status": "accepted", "proposal": proposal},
            )
        else:
            await self._send_response(
                task_id, session_id, pipeline_run_id,
                "refuse", {
                    "original_proposer": sender,
                    "reason": "Proposal conflicts with validated assumptions",
                },
            )

    # ── Revision handling (override in council-gated agents) ─────────────────

    async def handle_revise(self, task_id: str, session_id: str, pipeline_run_id: str, content: dict):
        """Default: no-op. Override in agents that support council revision."""
        logger.warning("[%s] Received revise but no handler implemented", self.AGENT_NAME)

    # ── Parse with retry ─────────────────────────────────────────────────────

    def _parse_llm_response(self, raw: str, validated_input) -> dict:
        """Try parse_json_with_retry, then fall back to agent-specific defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=self.SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name=self.AGENT_NAME,
        )
        if result is not None:
            return result

        logger.warning("[%s] Both parse attempts failed, constructing fallback", self.AGENT_NAME)
        return self._fallback_defaults(validated_input)

    # ── Hooks for subclasses ─────────────────────────────────────────────────

    def reasoning_budget(self, revision_required: bool) -> int:
        """Override to customize reasoning depth. Default: 3 normal, 4 on revision."""
        return 4 if revision_required else 3

    def _evaluate_proposal(self, proposal: str) -> bool:
        """Override to add custom proposal evaluation logic."""
        return True

    async def _post_process(self, task_id: str, session_id: str, pipeline_run_id: str, validated_input, result: dict):
        """Hook called after output validation, before sending inform. Override for contradiction checks."""
        pass

    def _default_gap_key(self) -> str:
        """Default gap key for escalation messages. Override if needed."""
        return "input_data"

    def _augment_parsed(self, parsed: dict, input_package: dict) -> dict:
        """Override to inject extra computed fields (e.g. simulation results) into
        parsed output before schema validation. Default: no-op."""
        return parsed

    # ── Abstract methods (must be implemented by each child) ─────────────────

    @abstractmethod
    def _build_schema_prompt(self) -> str:
        """Return the JSON schema instruction for Intelligence Engine and fallback."""
        ...

    @abstractmethod
    def _build_prompt(self, validated_input) -> str:
        """Build the fallback direct-LLM prompt from validated input."""
        ...

    @abstractmethod
    def _extract_input(self, input_package: dict, task: dict) -> dict:
        """Extract and return kwargs for INPUT_SCHEMA from the raw input_package."""
        ...

    @abstractmethod
    def _build_ie_input_data(self, input_package: dict) -> dict:
        """Build the input_data dict passed to IntelligenceEngine.reason_and_produce()."""
        ...

    @abstractmethod
    def _fallback_defaults(self, validated_input) -> dict:
        """Return schema-valid default dict when LLM fails completely."""
        ...


async def run_child_agent(agent_class: type, jid_env: str = "", password_env: str = "", bus: Optional[MessageBus] = None):
    """Start a child agent on a message bus (preferred) or standalone for testing."""
    from dotenv import load_dotenv
    load_dotenv()
    agent = agent_class(bus=bus)
    await agent.start(bus=bus)
    return agent
