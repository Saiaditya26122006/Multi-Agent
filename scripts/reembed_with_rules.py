#!/usr/bin/env python3
"""Do Alex's rule columns add discriminating signal to SECTION retrieval?

The current `bp_architecture.embedding` was built from
`node_title . purpose . required_output` (see the bulk-embed pass in
PROJECT_STATE). So `required_output` is ALREADY in the vector — the untested
fields are the rules proper:

    evidence_requirement                  739/801 leaves, median +377 chars,
                                          734 distinct values
    prohibited_claims_inference_patterns  739/801 leaves, median  +87 chars,
                                          705 distinct values
    proof_burden                          11 distinct values across 912 rows,
                                          543 of them "descriptive"

Adding the first two roughly triples the embedded text per node, and both are
highly distinct, so there is real signal to test. `proof_burden` is a
low-cardinality type tag and is tested separately rather than folded in — a
field that is identical across 543 nodes can only pull them together.

Three variants, all held in a local cache. Nothing is written to
bp_architecture: a new column is only worth a migration if this wins.

    A  title + purpose + required_output                    (current baseline)
    B  A + evidence_requirement + prohibited_claims         (the rules)
    C  B + proof_burden                                     (isolates the tag)

Measured on the two labelled fact sets. The real-document facts are NOT included
because they have no ground-truth labels yet — section recall needs a correct
section to score against, and scoring against the classifier's own guess would
be circular.

    python scripts/reembed_with_rules.py
"""

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import load_architecture, parent_of  # noqa: E402

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from measure_hybrid_recall import (  # noqa: E402
    LEAF_KS,
    SECTION_KS,
    embed_all,
    load_facts,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

CACHE = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "rule_embeddings.json")
MAX_WORKERS = 8
CALL_TIMEOUT = 120
TOTAL_TIMEOUT = 3600

RULE_COLUMNS = (
    "node_id,node_title,purpose,required_output,evidence_requirement,"
    "prohibited_claims_inference_patterns,proof_burden"
)


def clean(value) -> str:
    """Trim a field, treating the literal string 'None' as absent."""
    text = (value or "").strip()
    return "" if text == "None" else text


def variant_text(row: dict, variant: str) -> str:
    """Compose the text to embed for one variant."""
    parts = [clean(row.get(k)) for k in ("node_title", "purpose", "required_output")]
    if variant in ("B", "C"):
        parts.append(clean(row.get("evidence_requirement")))
        parts.append(clean(row.get("prohibited_claims_inference_patterns")))
    if variant == "C":
        parts.append(clean(row.get("proof_burden")))
    return ". ".join(p for p in parts if p)


def fetch_rows(supabase) -> dict[str, dict]:
    """Every node with its rule columns."""
    rows, start = [], 0
    while True:
        resp = (
            supabase.table("bp_architecture")
            .select(RULE_COLUMNS)
            .range(start, start + 999)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 1000
    return {r["node_id"]: r for r in rows}


def build_variant(rows: dict, leaf_ids: list[str], variant: str) -> dict[str, list[float]]:
    """Embed every leaf under one variant, in parallel."""
    out: dict[str, list[float]] = {}
    failures = 0

    def one(node_id: str):
        return node_id, embed(variant_text(rows[node_id], variant),
                              input_type="search_document")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(one, n) for n in leaf_ids if rows.get(n)]
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                node_id, vector = future.result(timeout=CALL_TIMEOUT)
                out[node_id] = vector
            except Exception as exc:  # noqa: BLE001 — counted and reported
                failures += 1
                logging.error("embed failed: %s", str(exc)[:150])
            done += 1
            if done % 200 == 0:
                print(f"    {variant}: {done}/{len(leaf_ids)}")
    if failures:
        print(f"    !! {failures} embeddings failed in variant {variant}")
    return out


def matrix_for(vectors: dict[str, list[float]], leaf_ids: list[str]):
    """Stack and L2-normalise, returning (ids, matrix) for the ids present."""
    keep = [n for n in leaf_ids if n in vectors]
    mat = np.vstack([np.asarray(vectors[n], dtype=np.float32) for n in keep])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    return keep, mat


def score(items, fact_vectors, ids, mat):
    """Section and leaf recall for one embedding set."""
    sec = {k: 0 for k in SECTION_KS}
    leaf = {k: 0 for k in LEAF_KS}
    n = 0
    for targets, text in items:
        present = [t for t in targets if t in ids]
        if not present:
            continue
        n += 1
        q = np.asarray(fact_vectors[text], dtype=np.float32)
        q /= np.linalg.norm(q)
        order = np.argsort(-(mat @ q))
        ranked = [ids[int(i)] for i in order]

        best = min((ranked.index(t) + 1 for t in present), default=10**6)
        for k in LEAF_KS:
            if best <= k:
                leaf[k] += 1

        want = {parent_of(t) for t in present}
        seen, rank = [], None
        for node_id in ranked:
            p = parent_of(node_id)
            if p not in seen:
                seen.append(p)
                if p in want:
                    rank = len(seen)
                    break
        for k in SECTION_KS:
            if rank and rank <= k:
                sec[k] += 1
    return sec, leaf, n


def main() -> None:
    """Build the variants (cached) and report recall side by side."""
    supabase = create_client(
        os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    arch = load_architecture(supabase)
    rows = fetch_rows(supabase)
    leaf_ids = list(arch.leaf_ids)

    sample = rows[leaf_ids[0]]
    print("text length per variant (first leaf, chars): "
          + "  ".join(f"{v}={len(variant_text(sample, v))}" for v in "ABC") + "\n")

    cache: dict[str, dict] = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
    for variant in ("B", "C"):
        if variant not in cache:
            print(f"  embedding variant {variant} ({len(leaf_ids)} leaves) ...")
            cache[variant] = build_variant(rows, leaf_ids, variant)
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            json.dump(cache, open(CACHE, "w"))
    print()

    facts = load_facts()
    fact_vectors = embed_all(facts)

    variants = [
        ("A  current (title+purpose+req_output)", None),
        ("B  + evidence_req + prohibited_claims", cache["B"]),
        ("C  B + proof_burden", cache["C"]),
    ]

    for set_name, items in facts.items():
        print("=" * 94)
        print(f"{set_name.upper()}  —  {len(items)} facts")
        print("=" * 94)
        header = f"{'variant':<40}" + "".join(f"{'sec@' + str(k):>8}" for k in SECTION_KS)
        header += "  |" + "".join(f"{'leaf@' + str(k):>9}" for k in LEAF_KS)
        print(header)
        print("-" * len(header))
        for label, vectors in variants:
            if vectors is None:
                ids, mat = leaf_ids, arch.leaf_matrix
            else:
                ids, mat = matrix_for(vectors, leaf_ids)
            sec, leaf, n = score(items, fact_vectors, ids, mat)
            line = f"{label:<40}"
            line += "".join(f"{100.0 * sec[k] / n:7.1f}%" for k in SECTION_KS)
            line += "  |"
            line += "".join(f"{100.0 * leaf[k] / n:8.1f}%" for k in LEAF_KS)
            print(line)
        print()

    print("Nothing written to bp_architecture. Cache: " + CACHE)


if __name__ == "__main__":
    main()
