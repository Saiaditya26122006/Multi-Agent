"""
Base class for all Phase 2 child agents.

Eliminates duplicated boilerplate across 10 agents by providing:
- Bedrock LLM calls with retry + exponential backoff
- SPADE message handling (send, escalate, inform)
- Intelligence Engine integration
- Standard handle_request flow (validate → reason → fallback → validate output → inform)
- ListenBehaviour that routes performatives to handler methods

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
- _check_contradictions() — for cross-section validation
- _evaluate_proposal() — custom proposal evaluation logic
- reasoning_budget(revision_required: bool) -> int
"""

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine

logger = logging.getLogger(__name__)

BEDROCK_CONFIG = BotoConfig(
    read_timeout=180,
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


class ChildListenBehaviour(CyclicBehaviour):
    """Standard listener that routes SPADE messages to agent handler methods."""

    async def run(self):
        msg = await self.receive(timeout=5)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        task_id = msg.get_metadata("task_id")
        session_id = msg.get_metadata("session_id")
        pipeline_run_id = msg.get_metadata("pipeline_run_id")
        content = json.loads(msg.body)
        sender = str(msg.sender)

        if performative == "request":
            await self.agent.handle_request(task_id, session_id, pipeline_run_id, content)
        elif performative == "revise":
            await self.agent.handle_revise(task_id, session_id, pipeline_run_id, content)
        elif performative == "propose":
            await self.agent.handle_propose(task_id, session_id, pipeline_run_id, sender, content)


class BaseChildAgent(Agent, ABC):
    """Abstract base for all Phase 2 section-producing agents."""

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

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv(self.MODEL_ENV, self.MODEL_DEFAULT)
        self.bedrock = get_shared_bedrock_client()
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[%s] Starting", self.AGENT_NAME)
        self.add_behaviour(ChildListenBehaviour())
        signal_ready(self.redis, self.AGENT_NAME.lower().replace(" ", "_"))

    # ── Message sending ──────────────────────────────────────────────────────

    async def _send_msg(self, msg: Message):
        class _Send(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _Send()
        self.add_behaviour(b)
        await b.join(timeout=10)

    async def _escalate(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        trigger: str,
        notes: str,
    ):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "trigger": trigger,
            "notes": notes,
            "section": self.SECTION_NUMBER,
            "gap_key": self._default_gap_key(),
        })
        await self._send_msg(msg)

    async def _send_inform(self, task_id: str, session_id: str, pipeline_run_id: str, output: dict):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": self.SECTION_NUMBER})
        await self._send_msg(msg)

    async def _send_response(self, task_id: str, session_id: str, pipeline_run_id: str, performative: str, content: dict):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", performative)
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps(content)
        await self._send_msg(msg)

    # ── LLM call with retry + backoff ────────────────────────────────────────

    async def _call_llm(self, user_message: str) -> tuple[Optional[str], dict]:
        """Call Bedrock with exponential backoff. Returns (text, usage) or (None, {})."""
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
                return text, {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                }
            except (
                self.bedrock.exceptions.ThrottlingException,
            ) as e:
                wait = self.LLM_RETRY_BACKOFF[min(attempt, len(self.LLM_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "[%s] Throttled — retrying in %ds (attempt %d/%d)",
                    self.AGENT_NAME, wait, attempt + 1, self.LLM_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
            except Exception as e:
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
        """Standard flow: validate input → IE reason → fallback LLM → validate output → inform."""
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
            user_message = self._build_prompt(validated_input)
            llm_response, fallback_usage = await self._call_llm(user_message)
            if not llm_response:
                await self._escalate(
                    task_id, session_id, pipeline_run_id,
                    "weak_evidence", "Intelligence engine and fallback both failed",
                )
                return
            parsed = self._parse_llm_response(llm_response, validated_input)
            token_usage["input_tokens"] = token_usage.get("input_tokens", 0) + fallback_usage.get("input_tokens", 0)
            token_usage["output_tokens"] = token_usage.get("output_tokens", 0) + fallback_usage.get("output_tokens", 0)

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
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)

        await self._post_process(task_id, session_id, pipeline_run_id, validated_input, result)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

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


async def run_child_agent(agent_class: type, jid_env: str, password_env: str):
    """Standard main() for running a child agent standalone."""
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv(jid_env)
    password = os.getenv(password_env)
    if not jid or not password:
        raise ValueError(f"{jid_env} and {password_env} must be set")
    agent = agent_class(jid=jid, password=password)
    await agent.start(auto_register=True)
    try:
        while agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()
