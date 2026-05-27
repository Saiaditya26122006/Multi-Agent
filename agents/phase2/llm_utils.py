"""Shared LLM utilities for Phase 2 child agents."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def signal_ready(redis_client, agent_name: str, ttl: int = 300) -> None:
    """Set a Redis readiness key so the Mother Agent knows this child is alive."""
    try:
        redis_client.client.set(f"agent_ready:{agent_name}", "1", ex=ttl)
    except Exception as e:
        logger.warning("[%s] Failed to signal readiness: %s", agent_name, e)

RETRY_SYSTEM_PROMPT = (
    "Your previous response was not valid JSON. "
    "Return ONLY a raw JSON object. No markdown code blocks, no explanation, "
    "no text before or after. Start with { and end with }."
)


def strip_markdown_json(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else 3
        text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def parse_json_with_retry(
    raw: str,
    bedrock_client,
    model_id: str,
    system_prompt: str,
    user_message: str,
    agent_name: str,
    max_tokens: int = 4096,
) -> Optional[dict]:
    """Try to parse LLM JSON response; on failure, retry once with a stricter prompt.

    Returns parsed dict on success, None if both attempts fail.
    """
    text = strip_markdown_json(raw)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    if bedrock_client is None:
        logger.warning("[%s] JSON parse failed and no bedrock client for retry", agent_name)
        return None

    logger.warning("[%s] First JSON parse failed — retrying with correction prompt", agent_name)

    retry_message = (
        f"{RETRY_SYSTEM_PROMPT}\n\n"
        f"Here is your previous invalid response (fix it):\n{raw[:3000]}\n\n"
        f"Original request (for context):\n{user_message[:2000]}"
    )

    try:
        response = bedrock_client.converse(
            modelId=model_id,
            system=[{"text": system_prompt + "\n\nCRITICAL: Respond with ONLY a valid JSON object. No markdown."}],
            messages=[{"role": "user", "content": [{"text": retry_message}]}],
            inferenceConfig={"maxTokens": max_tokens},
        )
        retry_raw = response["output"]["message"]["content"][0]["text"]
        retry_text = strip_markdown_json(retry_raw)
        result = json.loads(retry_text)
        logger.info("[%s] Retry parse succeeded", agent_name)
        return result
    except (json.JSONDecodeError, ValueError):
        logger.warning("[%s] Retry JSON parse also failed", agent_name)
        return None
    except Exception as e:
        logger.error("[%s] Retry LLM call failed: %s", agent_name, e)
        return None
