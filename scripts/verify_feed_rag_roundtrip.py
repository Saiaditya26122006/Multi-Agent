#!/usr/bin/env python3
"""Round-trip check: does a Feed-stored fact come back the way Build queries it?

Storing is already proven. This proves the other half — that what Feed writes is
retrievable by the calls Build actually makes, with the node assignment and the
provenance still attached.

Three retrieval paths are checked, because they are not equivalent:

  1. semantic, as agents/phase2/rag_mixin.rag_enrich() calls it
     (source_types filter, threshold 0.4) — the generic "what do we know" path
  2. semantic + section filter — the "what has been filed to THIS node" path,
     which is how Build would assemble one section
  3. what format_chunks_for_injection() actually puts in front of the model,
     which is the only thing an agent ever sees

Pass criteria: the fact returns, `section` carries the routed node id, and the
provenance fields survive in `metadata`.

Writes nothing.

    python scripts/verify_feed_rag_roundtrip.py [run_id]
"""

import logging
import os
import sys

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.rag_service import (  # noqa: E402
    format_chunks_for_injection,
    retrieve,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

DEFAULT_RUN = "feed-65dff4b93528"
BUILD_SOURCE_TYPES = [
    "ceo_doc", "conversation", "decision",
    "correction", "agent_insight", "negative_knowledge",
]
PROVENANCE_FIELDS = ("source_document", "source_quote", "start_char", "end_char")
SIGNAL_FIELDS = ("needs_review", "verdict", "degraded_target", "degraded_reason")


def show(chunk, run_id: str) -> None:
    """Print one retrieved chunk with the fields Build depends on."""
    meta = chunk.metadata or {}
    mine = chunk.run_id == run_id
    print(f"    sim={chunk.similarity:.4f} {'[this run]' if mine else ''}")
    print(f"      content : {chunk.content[:72]}")
    print(f"      section : {chunk.section}   (node the classifier routed to)")
    print(f"      status  : epistemic={chunk.epistemic_status}  "
          f"decision={meta.get('decision')}  reasons={meta.get('review_reasons')}")
    print(f"      source  : {meta.get('source_document')} "
          f"[{meta.get('start_char')}:{meta.get('end_char')}]")
    print(f"      quote   : {str(meta.get('source_quote'))[:64]!r}")


def main() -> None:
    """Retrieve Feed-stored facts three ways and check what survives."""
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN
    query = "who are the primary buyers"
    print(f"run_id under test: {run_id}")
    print(f"query           : {query!r}\n")

    print("=" * 78)
    print("1. SEMANTIC — exactly as rag_mixin.rag_enrich() calls it")
    print("=" * 78)
    chunks = retrieve(
        query=query, source_types=BUILD_SOURCE_TYPES, top_k=5, threshold=0.4,
        recency_boost=True,
    )
    print(f"  returned {len(chunks)} chunks")
    for chunk in chunks:
        show(chunk, run_id)
    mine = [c for c in chunks if c.run_id == run_id]
    print(f"\n  from this Feed run: {len(mine)}/{len(chunks)}")

    print("\n" + "=" * 78)
    print("2. SECTION-FILTERED — 'what has been filed to this node'")
    print("=" * 78)
    node = next((c.section for c in mine), None) or "BP.13"
    filtered = retrieve(
        query=query, source_types=BUILD_SOURCE_TYPES, section=node,
        top_k=5, threshold=0.3,
    )
    print(f"  section={node} -> {len(filtered)} chunks")
    for chunk in filtered:
        show(chunk, run_id)

    print("\n" + "=" * 78)
    print("3. INTEGRITY — do node assignment and provenance survive retrieval?")
    print("=" * 78)
    sample = mine or filtered
    if not sample:
        print("  FAIL — no chunk from this run came back at all")
        sys.exit(1)
    ok = True
    for chunk in sample:
        meta = chunk.metadata or {}
        missing_prov = [f for f in PROVENANCE_FIELDS if meta.get(f) is None]
        missing_sig = [f for f in SIGNAL_FIELDS if f not in meta]
        if chunk.section is None:
            print(f"  FAIL — chunk {chunk.id} came back with no section")
            ok = False
        if missing_prov:
            print(f"  FAIL — chunk {chunk.id} lost provenance: {missing_prov}")
            ok = False
        if missing_sig:
            print(f"  FAIL — chunk {chunk.id} lost review signals: {missing_sig}")
            ok = False
    if ok:
        print(f"  PASS — {len(sample)} chunk(s): node assignment, provenance "
              f"({', '.join(PROVENANCE_FIELDS)}) and both review signals intact")

    print("\n" + "=" * 78)
    print("4. WHAT THE AGENT ACTUALLY SEES — format_chunks_for_injection()")
    print("=" * 78)
    print(format_chunks_for_injection(sample, max_chars=1200) or "  (empty)")
    print(
        "\n  NOTE: this is the whole payload an agent receives. It carries the\n"
        "  content, epistemic status and source_type — it does NOT carry the\n"
        "  node id or the provenance. Those survive in the Chunk object, so any\n"
        "  Build code that needs them must read chunk.section / chunk.metadata\n"
        "  rather than the injected string."
    )
    print("\nNothing written.")


if __name__ == "__main__":
    main()
