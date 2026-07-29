#!/usr/bin/env python3
"""Map Alex's Source-of-Truth docx onto architecture nodes. PROPOSE ONLY.

Writes nothing to any table. Produces `evaluation/sot_node_mapping.csv` for
human review, because an automated mapping is the *input* to enrichment: a wrong
match does not just fail to help, it poisons the node it lands on.

Extraction
----------
Two kinds of content unit, both preserved with their structure:

  table row   The 22 tables carry most of the operational content and their
              columns are meaningful ("Buyer persona | Need | Buying power |
              Budget source | Adoption trigger | Procurement barrier | ...").
              Each row becomes one unit rendered as `header: value` pairs, so
              the column semantics survive into the text.
  prose block A heading plus the lines under it from the narrative preamble.

Matching
--------
Retrieval proposes, an LLM disposes — the same shape as the Feed classifier,
for the same reason: pure vector matching onto governance-worded nodes measured
25-40% and is not good enough to build a mapping on. Each unit gets the top
candidate SECTIONS by hybrid (BM25 + vector) retrieval, and the model picks one
leaf from those sections' children or answers "none".

"none" is a first-class answer. The document does not cover 912 nodes and a
mapping that pretends otherwise is worse than a sparse one.

    python scripts/map_sot_docx_to_nodes.py
"""

import csv
import json
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import docx  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.table import Table  # noqa: E402
from docx.text.paragraph import Paragraph  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import (  # noqa: E402
    _call_llm,
    _parse_json_object,
    load_architecture,
    parent_of,
)
from services.hybrid_retrieval import build_bm25, fuse  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

DOCX = os.path.join(
    PROJECT_ROOT, "database",
    "EpistemicOS — Structured Source-of-Truth Dataset (2).docx",
)
TERMS = os.path.join(
    os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "operational_terms_all.json"
)
OUT_CSV = os.path.join(PROJECT_ROOT, "evaluation", "sot_node_mapping.csv")
OUT_JSON = os.path.join(
    os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "sot_node_mapping.json"
)

SECTIONS_TO_OFFER = 4
MAX_WORKERS = 8
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 5400

MATCH_PROMPT = """You are mapping a company's real source-of-truth document onto \
a business-plan architecture, so that each node can be enriched with the real \
operational text that belongs to it.

You are given ONE unit of document content and a list of candidate nodes. Pick \
the single node whose REQUIRED OUTPUT this content would actually populate.

=== "none" IS OFTEN THE RIGHT ANSWER ===
The document does not cover every node, and the candidate list is produced by
imperfect retrieval — the right node is frequently absent. Answer "none" when:
  - nothing here is what the content would populate
  - the content is administrative (a task, an open question, a status marker)
    rather than substantive business content
  - you would have to stretch the node's purpose to make it fit

A wrong match actively damages the node it lands on. A missing match costs
nothing. When in doubt, answer "none".

=== HOW TO DECIDE ===
Read each node's required_output first. Ask: if someone wrote this node
properly, would THIS content be part of what they wrote? Shared topic is not
enough — a fact about pricing is not automatically the pricing-governance node.

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<one sentence: what this content actually is>",
 "node_id": "<node id, or the string \\"none\\">",
 "confidence": "<high or low>"}

Write "reason" first and let the choice follow from it."""


def iter_blocks(document):
    """Yield paragraphs and tables in document order."""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def extract_units(path: str) -> list[dict]:
    """Pull table rows and prose blocks out of the docx as content units."""
    document = docx.Document(path)
    units: list[dict] = []
    heading = "(preamble)"
    prose: list[str] = []
    table_index = 0

    def flush_prose() -> None:
        if prose:
            text = " ".join(prose).strip()
            if len(text) > 40:
                units.append(
                    {"kind": "prose", "section": heading, "row_label": "",
                     "text": text}
                )
            prose.clear()

    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            line = block.text.strip()
            if not line:
                continue
            is_heading = len(line) < 90 and (
                line[0].isdigit() or block.style.name.startswith("Heading 1")
            )
            if is_heading:
                flush_prose()
                heading = line
            else:
                prose.append(line)
            continue

        flush_prose()
        table_index += 1
        rows = block.rows
        if len(rows) < 2:
            continue
        headers = [c.text.strip() for c in rows[0].cells]
        for row in rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            pairs = [
                f"{h}: {v}"
                for h, v in zip(headers, cells)
                if v and v.lower() not in ("none.", "none", "n/a", "-")
            ]
            if len(pairs) < 2:
                continue
            units.append(
                {
                    "kind": f"table{table_index}",
                    "section": heading,
                    "row_label": cells[0] if cells else "",
                    "text": " | ".join(pairs),
                }
            )
    flush_prose()
    return units


def candidate_sections(unit_vector, arch, bm25, unit_text: str, n: int) -> list[str]:
    """Top sections for one unit, by hybrid retrieval over leaves."""
    vector = np.asarray(unit_vector, dtype=np.float32)
    vector /= np.linalg.norm(vector)
    fused = fuse(arch.leaf_matrix @ vector, bm25.scores(unit_text), mode="rrf")
    order = np.argsort(-fused)
    seen: list[str] = []
    for i in order:
        section = parent_of(arch.leaf_ids[int(i)])
        if section not in seen:
            seen.append(section)
            if len(seen) >= n:
                break
    return seen


def describe(node: dict) -> str:
    """Render a candidate node for the matcher."""
    def field(key: str) -> str:
        value = (node.get(key) or "").strip()
        return value if value and value != "None" else "(not specified)"

    return (
        f"node_id: {node['node_id']}\n"
        f"  title: {field('node_title')}\n"
        f"  purpose: {field('purpose')}\n"
        f"  required_output: {field('required_output')}"
    )


def match_unit(unit: dict, arch, bm25) -> dict:
    """Retrieve candidate sections for a unit, then have the model pick a node."""
    vector = embed(unit["text"][:2000], input_type="search_query")
    sections = candidate_sections(vector, arch, bm25, unit["text"], SECTIONS_TO_OFFER)
    candidates: list[str] = []
    for section in sections:
        candidates += arch.siblings.get(section, [])
    if not candidates:
        return {**unit, "node_id": "none", "confidence": "low",
                "reason": "no candidate sections", "sections": sections}

    payload = (
        f"DOCUMENT SECTION: {unit['section']}\n"
        f"CONTENT UNIT:\n{unit['text'][:2500]}\n\n"
        f"CANDIDATE NODES ({len(candidates)}):\n\n"
        + "\n\n".join(describe(arch.nodes[n]) for n in candidates)
    )
    parsed = _parse_json_object(
        _call_llm(MATCH_PROMPT, payload,
                  os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6"))
    )
    node_id = str(parsed.get("node_id", "none")).strip()
    if node_id != "none" and node_id not in arch.nodes:
        node_id = "none"
    return {
        **unit,
        "node_id": node_id,
        "confidence": str(parsed.get("confidence", "low")).strip().lower(),
        "reason": str(parsed.get("reason", "")).strip(),
        "sections": sections,
    }


def main() -> None:
    """Extract, match, and write the proposed mapping for review."""
    units = extract_units(DOCX)
    kinds = Counter(u["kind"] for u in units)
    print(f"content units extracted: {len(units)}")
    print(f"  prose blocks: {kinds.get('prose', 0)}")
    print(f"  table rows  : {sum(v for k, v in kinds.items() if k != 'prose')}"
          f" across {len([k for k in kinds if k != 'prose'])} tables\n")

    arch = load_architecture()
    extra = None
    if os.path.exists(TERMS):
        extra = json.load(open(TERMS))["curated"]
        print(f"using operational-term enrichment for retrieval "
              f"({sum(len(v) for v in extra.values())} terms)")
    bm25 = build_bm25(arch, extra_terms=extra)

    print(f"matching {len(units)} units at {MAX_WORKERS} workers ...")
    results: list[dict] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(match_unit, u, arch, bm25) for u in units]
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                results.append(future.result(timeout=CALL_TIMEOUT))
            except Exception as exc:  # noqa: BLE001 — counted and reported
                failures += 1
                logging.error("match failed: %s", str(exc)[:180])
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(units)}")

    matched = [r for r in results if r["node_id"] != "none"]
    by_node: dict[str, list[dict]] = {}
    for r in matched:
        by_node.setdefault(r["node_id"], []).append(r)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["node_id", "node_title", "confidence", "doc_section", "row_label",
             "matched_text", "match_reason", "verdict_ok"]
        )
        for node_id in sorted(by_node):
            for r in by_node[node_id]:
                writer.writerow([
                    node_id,
                    (arch.nodes[node_id].get("node_title") or ""),
                    r["confidence"], r["section"], r["row_label"],
                    r["text"][:1200], r["reason"][:220], "",
                ])
    json.dump(results, open(OUT_JSON, "w"), indent=1)

    leaves = set(arch.leaf_ids)
    matched_leaves = {n for n in by_node if n in leaves}
    degraded = {
        n for n in by_node if arch.nodes[n].get("degraded_target")
    }
    print("\n" + "=" * 78)
    print("COVERAGE")
    print("=" * 78)
    print(f"  units matched to a node : {len(matched)}/{len(results)} "
          f"({100.0 * len(matched) / max(len(results), 1):.1f}%)")
    print(f"  units the doc-to-node matcher rejected: {len(results) - len(matched)}")
    print(f"  failures                : {failures}")
    print(f"\n  distinct nodes covered  : {len(by_node)} of {len(arch.nodes)} "
          f"({100.0 * len(by_node) / len(arch.nodes):.1f}%)")
    print(f"  of which leaves         : {len(matched_leaves)} of {len(leaves)} "
          f"({100.0 * len(matched_leaves) / len(leaves):.1f}%)")
    print(f"  NOT covered by the doc  : {len(arch.nodes) - len(by_node)} nodes")
    print(f"  degraded nodes reached  : {len(degraded)} "
          f"(these are the ones enrichment would most help)")
    print(f"  high-confidence matches : "
          f"{sum(1 for r in matched if r['confidence'] == 'high')}/{len(matched)}")

    print("\n  by BP domain:")
    domains = Counter(".".join(n.split(".")[:2]) for n in by_node)
    for domain, count in sorted(domains.items(), key=lambda t: -t[1]):
        print(f"    {domain:<8} {count:>3} nodes")

    print(f"\nwrote {OUT_CSV}")
    print("PROPOSE ONLY — nothing written to bp_architecture or knowledge_base.")


if __name__ == "__main__":
    main()
