"""
Embedding Service — Amazon Titan Embeddings v2 via AWS Bedrock.

Replaces the old all-MiniLM-L6-v2 local model with Titan's
1024-dimensional embeddings for accurate BP node retrieval.

Titan v2 is a first-party AWS model — no Marketplace subscription needed.
It supports asymmetric embedding via the "inputText" + normalize flag.
"""

import json
import logging
import os
from typing import Literal

import boto3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"

_bedrock_client = None


InputType = Literal["search_document", "search_query", "classification", "clustering"]


def _get_client():
    """Lazy-load Bedrock client (singleton)."""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        logger.info("[Embedding] Bedrock client initialized (region=%s)", os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
    return _bedrock_client


def embed(text: str, input_type: InputType = "search_query") -> list[float]:
    """Embed a single text using Amazon Titan Embeddings v2 via Bedrock.

    Args:
        text: The text to embed.
        input_type: Kept for API compatibility. Titan v2 doesn't distinguish
                    input types but we normalize all outputs for cosine similarity.

    Returns:
        List of 1024 floats representing the embedding.
    """
    if not text or not text.strip():
        raise ValueError("[Embedding] Cannot embed empty text")

    client = _get_client()

    body = json.dumps({
        "inputText": text,
        "dimensions": EMBEDDING_DIM,
        "normalize": True,
    })

    response = client.invoke_model(
        modelId=TITAN_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    result = json.loads(response["body"].read())
    embedding = result["embedding"]

    if len(embedding) != EMBEDDING_DIM:
        logger.warning(
            "[Embedding] Unexpected dimension: got %d, expected %d",
            len(embedding),
            EMBEDDING_DIM,
        )

    return embedding


def embed_batch(
    texts: list[str],
    input_type: InputType = "search_query",
    batch_size: int = 25,
) -> list[list[float]]:
    """Embed multiple texts by calling Titan one at a time (no batch API).

    Titan v2 doesn't have a native batch endpoint, so we loop.
    batch_size controls how many are logged per progress message.

    Args:
        texts: List of texts to embed.
        input_type: Kept for API compatibility.
        batch_size: Log progress every N texts.

    Returns:
        List of embedding vectors (same order as input texts).
    """
    if not texts:
        return []

    all_embeddings = []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            all_embeddings.append([0.0] * EMBEDDING_DIM)
            continue

        embedding = embed(text, input_type=input_type)
        all_embeddings.append(embedding)

        if (i + 1) % batch_size == 0:
            logger.debug(
                "[Embedding] Progress: %d/%d texts embedded",
                i + 1,
                len(texts),
            )

    return all_embeddings
