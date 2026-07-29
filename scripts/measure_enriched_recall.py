#!/usr/bin/env python3
"""The last engineering test: does operational-term enrichment lift PARAPHRASE recall?

Paraphrase section recall is the gate, for the reason the earlier enrichment
attempt failed: any mechanism can lift the written test facts by fitting their
phrasing. Only the oblique restatements show whether something generalises.

Compares four configurations on the same labelled key:

    vector only                 the original baseline
    hybrid RRF                  BM25 over raw node text, fused (current best)
    hybrid RRF + terms          BM25 over node text PLUS curated operational terms
    lexical only + terms        isolates what the terms did on their own

All 801 leaves are enriched, not just the answer nodes — enriching only the
correct targets would lift recall by construction.

Writes nothing.

    python scripts/measure_enriched_recall.py
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.feed_classifier_v3 import load_architecture  # noqa: E402
from services.hybrid_retrieval import build_bm25  # noqa: E402

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from measure_hybrid_recall import (  # noqa: E402
    LEAF_KS,
    SECTION_KS,
    embed_all,
    load_facts,
    score_config,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

TERMS = os.path.join(
    os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "operational_terms_all.json"
)


def main() -> None:
    """Measure enriched vs unenriched lexical signals on both fact sets."""
    if not os.path.exists(TERMS):
        print(f"missing {TERMS} — run scripts/generate_operational_terms_all.py first")
        sys.exit(1)

    payload = json.load(open(TERMS))
    curated: dict[str, list[str]] = payload["curated"]
    arch = load_architecture()

    covered = sum(1 for n in arch.leaf_ids if curated.get(n))
    term_count = sum(len(v) for v in curated.values())
    print(
        f"enrichment: {covered}/{len(arch.leaf_ids)} leaves carry terms, "
        f"{term_count} terms total, {term_count / max(covered, 1):.1f} per node\n"
    )

    plain = build_bm25(arch)
    enriched = build_bm25(arch, extra_terms=curated)

    facts = load_facts()
    vectors = embed_all(facts)

    configs = [
        ("vector only", plain, "vector", 0.0),
        ("hybrid RRF (raw nodes)", plain, "rrf", 0.0),
        ("hybrid RRF + TERMS", enriched, "rrf", 0.0),
        ("hybrid weighted a=0.5 + TERMS", enriched, "weighted", 0.5),
        ("lexical only + TERMS", enriched, "lexical", 0.0),
    ]

    for set_name, items in facts.items():
        print("=" * 96)
        print(f"{set_name.upper()}  —  {len(items)} facts")
        print("=" * 96)
        header = f"{'config':<31}" + "".join(f"{'sec@' + str(k):>8}" for k in SECTION_KS)
        header += "  |" + "".join(f"{'leaf@' + str(k):>9}" for k in LEAF_KS)
        print(header)
        print("-" * len(header))
        for label, index, mode, alpha in configs:
            sec, leaf, n = score_config(items, vectors, arch, index, mode, alpha)
            line = f"{label:<31}"
            line += "".join(f"{100.0 * sec[k] / n:7.1f}%" for k in SECTION_KS)
            line += "  |"
            line += "".join(f"{100.0 * leaf[k] / n:8.1f}%" for k in LEAF_KS)
            print(line)
        print()

    print(
        "GATE: paraphrase sec@2. Hybrid RRF on raw nodes scored 40.0%.\n"
        "A meaningful lift there means operational vocabulary is the mechanism.\n"
        "Flat means vocabulary is not the gap either."
    )
    print("\nNothing written.")


if __name__ == "__main__":
    main()
