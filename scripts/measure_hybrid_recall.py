#!/usr/bin/env python3
"""Does hybrid (BM25 + vector) retrieval beat vector-only?

Measured against the 78-fact labelled key, because that is the only fact set
with ground truth. The real-document key is not labelled yet, and section recall
without a correct-section column would be the classifier grading itself.

Reports, for every configuration:

    section recall  @1/@2/@3/@5   induced — rank leaves, map to parents, dedupe
    leaf recall     @1/@3/@5/@10  the answer the judge would be handed

on both fact sets, kept apart:

    bucket-a     54 written test facts
    paraphrase   10 oblique restatements — the harder, more realistic set

The paraphrase column is the validation gate. Any mechanism that lifts bucket-a
while paraphrase stays flat has learned the test set's phrasing, which is
exactly how the earlier node-enrichment attempt failed.

No LLM judge, no tuning. Writes nothing.

    python scripts/measure_hybrid_recall.py
"""

import csv
import json
import logging
import os
import sys

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import load_architecture, parent_of  # noqa: E402
from services.hybrid_retrieval import build_bm25, fuse  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
CACHE = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "hybrid_q.json")

SECTION_KS = (1, 2, 3, 5)
LEAF_KS = (1, 3, 5, 10)

PARAPHRASES = [
    ("BP.5.3.3", "deans have ~25k discretion, above that it's committee"),
    ("BP.5.1.2", "the dean is who actually buys, not the researcher"),
    ("BP.7.2.3", "we don't train on their papers. ever. no fine-tuning either"),
    ("BP.10.1.3", "3 depts, 12 wks, 40ish papers each"),
    ("BP.4.1.3", "no corporate labs. universities only"),
    ("BP.9.1.1", "point is the supervisor doesn't have to find the weak claims himself"),
    ("BP.5.1.4", "security guy has no budget but can kill a deal for months"),
    ("BP.8.1.5", "grammarly/elicit aren't competitors exactly but same money pot"),
    ("BP.12.1.4", "1-5 impact, 1-5 likelihood, that's the scoring"),
    ("BP.6.1.4", "same 12 qs every call so we can compare across unis"),
]


def load_facts() -> dict[str, list[tuple[list[str], str]]]:
    """Return the two labelled fact sets as {name: [(targets, text)]}."""
    rows = list(csv.DictReader(open(KEY)))
    bucket_a = [
        (r["correct_node_id"].split("|"), r["fact_text"])
        for r in rows
        if r["scoring_bucket"] == "a_classifier" and r["category"] in ("clear", "hard")
    ]
    return {
        "bucket-a": bucket_a,
        "paraphrase": [([t], f) for t, f in PARAPHRASES],
    }


def embed_all(facts: dict) -> dict[str, list[float]]:
    """Embed every fact once, cached across runs of this script."""
    cache: dict[str, list[float]] = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
    missing = [
        text for items in facts.values() for _, text in items if text not in cache
    ]
    if missing:
        print(f"embedding {len(missing)} facts ...")
        for text in missing:
            cache[text] = embed(text, input_type="search_query")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, "w"))
    return cache


def score_config(items, vectors, arch, bm25, mode, alpha):
    """Return (section hits, leaf hits, n) for one configuration."""
    section_hits = {k: 0 for k in SECTION_KS}
    leaf_hits = {k: 0 for k in LEAF_KS}
    n = 0

    for targets, text in items:
        present = [t for t in targets if t in arch.leaf_ids]
        if not present:
            continue
        n += 1

        vector = np.asarray(vectors[text], dtype=np.float32)
        vector /= np.linalg.norm(vector)
        vector_scores = arch.leaf_matrix @ vector
        lexical_scores = bm25.scores(text)

        if mode == "vector":
            fused = vector_scores
        elif mode == "lexical":
            fused = lexical_scores
        else:
            fused = fuse(vector_scores, lexical_scores, mode=mode, alpha=alpha)

        order = np.argsort(-fused)
        ranked = [arch.leaf_ids[int(i)] for i in order]

        best = min((ranked.index(t) + 1 for t in present), default=10**6)
        for k in LEAF_KS:
            if best <= k:
                leaf_hits[k] += 1

        want = {parent_of(t) for t in present}
        seen: list[str] = []
        section_rank = None
        for node_id in ranked:
            parent = parent_of(node_id)
            if parent not in seen:
                seen.append(parent)
                if parent in want and section_rank is None:
                    section_rank = len(seen)
                    break
        for k in SECTION_KS:
            if section_rank and section_rank <= k:
                section_hits[k] += 1

    return section_hits, leaf_hits, n


def main() -> None:
    """Measure every configuration on both fact sets."""
    arch = load_architecture()
    bm25 = build_bm25(arch)
    facts = load_facts()
    vectors = embed_all(facts)

    configs = [
        ("vector only (baseline)", "vector", 0.0),
        ("BM25 only", "lexical", 0.0),
        ("hybrid RRF", "rrf", 0.0),
        ("hybrid weighted a=0.3", "weighted", 0.3),
        ("hybrid weighted a=0.5", "weighted", 0.5),
        ("hybrid weighted a=0.7", "weighted", 0.7),
    ]

    for set_name, items in facts.items():
        print("\n" + "=" * 92)
        print(f"{set_name.upper()}  —  {len(items)} facts, {len(arch.leaf_ids)} leaves")
        print("=" * 92)
        header = f"{'config':<24}" + "".join(f"{'sec@' + str(k):>8}" for k in SECTION_KS)
        header += "  |" + "".join(f"{'leaf@' + str(k):>9}" for k in LEAF_KS)
        print(header)
        print("-" * len(header))
        for label, mode, alpha in configs:
            sec, leaf, n = score_config(items, vectors, arch, bm25, mode, alpha)
            line = f"{label:<24}"
            line += "".join(f"{100.0 * sec[k] / n:7.1f}%" for k in SECTION_KS)
            line += "  |"
            line += "".join(f"{100.0 * leaf[k] / n:8.1f}%" for k in LEAF_KS)
            print(line)

    print(
        "\nThe paraphrase table is the gate. A configuration that lifts bucket-a\n"
        "while paraphrase stays flat has fitted the written test phrasing."
    )
    print("Nothing written.")


if __name__ == "__main__":
    main()
