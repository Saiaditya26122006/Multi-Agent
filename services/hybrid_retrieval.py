"""Hybrid retrieval — lexical (BM25) fused with vector similarity.

Every failure in the vector-only measurements is a surface-term failure in one
direction or the other:

  * a fact is pulled to a node that merely repeats its words
    ("unsupported claims" -> *Unsupported Claim Controls*, while the correct
    *Value Proposition Definition* sat at rank 641 of 801)
  * a fact carrying an exact operational term ("expert-labelled manuscripts",
    "programme funding", a euro figure, a persona name) finds nothing, because
    Titan blurs the term into a topic

BM25 is the complementary signal: it scores exact term overlap, weights rare
terms up, and is unaffected by how governance-worded the node prose is. Neither
signal is trusted alone — they are fused.

Two fusion modes, both measured rather than assumed:

    rrf        reciprocal rank fusion — combines RANKS, so the two scales never
               have to be made commensurable. Robust default.
    weighted   min-max normalise both score vectors, then blend. Sharper when
               it works, more sensitive to outliers.

BM25 runs in-process over the loaded architecture (912 rows), so this needs no
migration, no tsvector column, and no DDL.

Reads nothing, writes nothing — pure functions over an Architecture.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# BM25 parameters. Standard defaults; k1 controls term-frequency saturation,
# b controls length normalisation.
BM25_K1 = 1.5
BM25_B = 0.75

RRF_K = 60  # standard RRF damping constant

TOKEN_RE = re.compile(r"[a-z0-9]+")

# Terms that appear in most nodes' governance boilerplate carry no signal and
# actively hurt BM25 — they are the lexical equivalent of the blur that breaks
# the vector side.
STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    to with without shall must node section define defines definition governs
    governance specification required output purpose""".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords.

    Hyphenated compounds split into parts ("expert-labelled" -> expert,
    labelled) so a fact matches whether or not it hyphenates the same way.
    """
    return [t for t in TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS]


class BM25:
    """BM25 over a fixed set of documents, built once and queried many times."""

    def __init__(self, doc_ids: list[str], docs: list[str]) -> None:
        """Index the documents.

        Args:
            doc_ids: Stable ids, index-aligned with docs.
            docs: The text to index, one string per document.
        """
        self.doc_ids = doc_ids
        self.tokens = [tokenize(d) for d in docs]
        self.lengths = np.asarray([len(t) for t in self.tokens], dtype=np.float32)
        self.avg_length = float(self.lengths.mean()) if len(self.lengths) else 0.0

        self.term_frequency: list[Counter] = [Counter(t) for t in self.tokens]
        document_frequency: Counter = Counter()
        for tokens in self.tokens:
            document_frequency.update(set(tokens))

        n = len(docs)
        self.idf = {
            term: math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }
        logger.info(
            "[Hybrid] BM25 indexed %d docs, %d unique terms, avg length %.1f",
            n,
            len(self.idf),
            self.avg_length,
        )

    def scores(self, query: str) -> np.ndarray:
        """Score every document against the query. Returns a float32 array."""
        out = np.zeros(len(self.doc_ids), dtype=np.float32)
        terms = tokenize(query)
        if not terms or not self.avg_length:
            return out

        for term in set(terms):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, frequencies in enumerate(self.term_frequency):
                tf = frequencies.get(term, 0)
                if not tf:
                    continue
                denominator = tf + BM25_K1 * (
                    1.0 - BM25_B + BM25_B * self.lengths[i] / self.avg_length
                )
                out[i] += idf * (tf * (BM25_K1 + 1.0)) / denominator
        return out


def node_text(node: dict[str, Any]) -> str:
    """The text of a node that a fact could plausibly share terms with.

    Deliberately excludes the governance/process fields (executor, controller,
    architecture_status, decision_implication). They are identical across
    hundreds of nodes and would only add noise.
    """
    parts = [
        node.get("node_title"),
        node.get("purpose"),
        node.get("required_output"),
        node.get("evidence_requirement"),
    ]
    return " ".join(p for p in parts if p and p != "None")


def build_bm25(
    arch: Any, extra_terms: Optional[dict[str, list[str]]] = None
) -> BM25:
    """Build a BM25 index over an Architecture's leaf nodes.

    Args:
        arch: A loaded Architecture.
        extra_terms: Optional {node_id: [operational term, ...]} appended to
            that node's indexed text. This is the enrichment lever: the nodes
            are governance-worded and share almost no vocabulary with the way
            facts are actually written, so BM25 has nothing to match on. Terms
            must be curated before they get here — foreign-domain vocabulary
            creates false lexical matches and makes retrieval worse.
    """
    ids = list(arch.leaf_ids)
    docs = []
    for node_id in ids:
        text = node_text(arch.nodes[node_id])
        if extra_terms:
            added = extra_terms.get(node_id) or []
            if added:
                text = f"{text} {' '.join(added)}"
        docs.append(text)
    return BM25(ids, docs)


def _minmax(values: np.ndarray) -> np.ndarray:
    """Scale to [0, 1]. A flat vector maps to zeros, not to NaN."""
    low = float(values.min())
    high = float(values.max())
    if high - low < 1e-9:
        return np.zeros_like(values)
    return (values - low) / (high - low)


def fuse(
    vector_scores: np.ndarray,
    lexical_scores: np.ndarray,
    mode: str = "rrf",
    alpha: float = 0.5,
) -> np.ndarray:
    """Combine a vector and a lexical score vector into one ranking score.

    Args:
        vector_scores: Cosine similarities, index-aligned with lexical_scores.
        lexical_scores: BM25 scores.
        mode: "rrf" (fuse ranks) or "weighted" (fuse normalised scores).
        alpha: Weight on the VECTOR side. Only used when mode="weighted";
            alpha=1.0 is vector-only, alpha=0.0 is lexical-only.

    Returns:
        A score array where higher is better.
    """
    if mode == "weighted":
        return alpha * _minmax(vector_scores) + (1.0 - alpha) * _minmax(lexical_scores)

    if mode != "rrf":
        raise ValueError(f"unknown fusion mode {mode!r}")

    fused = np.zeros_like(vector_scores, dtype=np.float32)
    for scores in (vector_scores, lexical_scores):
        order = np.argsort(-scores)
        ranks = np.empty(len(scores), dtype=np.float32)
        ranks[order] = np.arange(1, len(scores) + 1, dtype=np.float32)
        fused += 1.0 / (RRF_K + ranks)
    return fused


def search(
    query: str,
    query_vector: list[float],
    arch: Any,
    bm25: BM25,
    leaf_matrix: Optional[np.ndarray] = None,
    mode: str = "rrf",
    alpha: float = 0.5,
) -> list[tuple[str, float]]:
    """Rank every leaf for one query, fusing vector and lexical signals.

    Returns (node_id, fused_score) sorted best first.
    """
    matrix = leaf_matrix if leaf_matrix is not None else arch.leaf_matrix
    vector = np.asarray(query_vector, dtype=np.float32)
    vector /= np.linalg.norm(vector)

    fused = fuse(matrix @ vector, bm25.scores(query), mode=mode, alpha=alpha)
    order = np.argsort(-fused)
    return [(arch.leaf_ids[int(i)], float(fused[int(i)])) for i in order]
