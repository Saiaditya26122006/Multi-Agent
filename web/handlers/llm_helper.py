"""
Lightweight LLM helper for workspace handlers.

Calls Claude Haiku via Bedrock to synthesize answers from RAG chunks.
"""

import os
import logging
import json

import boto3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_bedrock_client = None


def _get_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
    return _bedrock_client


def generate_answer(question: str, chunks: list, system_prompt: str = "") -> str:
    """Call Claude Haiku to synthesize an answer from RAG chunks.

    Args:
        question: The user's question.
        chunks: List of RAG chunk objects with .content, .epistemic_status, .source_type.
        system_prompt: Optional system prompt override.

    Returns:
        Claude's synthesized answer string.
    """
    if not chunks:
        return "No relevant data found in the knowledge base for that question."

    context_parts = []
    for i, c in enumerate(chunks, 1):
        status = f"[{c.epistemic_status}]" if c.epistemic_status else "[UNKNOWN]"
        source = c.source_type or "unknown"
        context_parts.append(f"{i}. {status} (source: {source}) {c.content[:300]}")

    context_block = "\n".join(context_parts)

    if not system_prompt:
        system_prompt = (
            "You are the EpistemicOS assistant helping Alex (CEO) understand his business plan. "
            "Answer based ONLY on the knowledge base context provided. "
            "Be concise, direct, and specific. Use 2-5 sentences max. "
            "Never invent data not in the context. "
            "When referencing epistemic status or data quality, translate internal tags into plain language. "
            "For example: [ASSUMPTION] becomes 'this is an assumption, not confirmed'; "
            "[CONFIRMED] becomes 'this is confirmed'; "
            "[CONTRADICTION] becomes 'this conflicts with other information'; "
            "[MISSING] becomes 'there is no evidence for this yet'; "
            "[LOW CONFIDENCE] becomes 'confidence is low on this'. "
            "Never output raw tags like [ASSUMPTION] or [MISSING] in your response. "
            "Always write in clear, natural sentences."
        )

    user_message = (
        f"KNOWLEDGE BASE CONTEXT:\n{context_block}\n\n"
        f"CEO'S QUESTION: {question}\n\n"
        "Answer naturally and concisely based on the context above."
    )

    try:
        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "anthropic.claude-haiku-4-5-20251001")

        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 512},
        )

        answer = response["output"]["message"]["content"][0]["text"]
        logger.info(
            "[LLMHelper] Generated answer (%d tokens in, %d tokens out)",
            response.get("usage", {}).get("inputTokens", 0),
            response.get("usage", {}).get("outputTokens", 0),
        )
        return answer

    except Exception as e:
        logger.error("[LLMHelper] Bedrock call failed: %s", e)
        return _fallback_response(chunks)


def _fallback_response(chunks: list) -> str:
    """Return raw chunks if LLM call fails."""
    lines = ["Here's what the knowledge base contains (LLM unavailable):"]
    for i, c in enumerate(chunks[:5], 1):
        status = f"[{c.epistemic_status}]" if c.epistemic_status else ""
        lines.append(f"  {i}. {status} {c.content[:150]}")
    return "\n".join(lines)
