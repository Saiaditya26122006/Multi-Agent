#!/usr/bin/env python3
"""Generate curated operational vocabulary for EVERY leaf node.

The 10-node sample was for inspection. This is the real index, and it must cover
all 801 leaves: enriching only the nodes that happen to be the correct answers
would lift recall by construction and measure nothing.

Two defects found in the sample are fixed here:

1. **Wrong-domain vocabulary.** With only governance text to work from, the
   generator invented terms for other industries — K-12 (`teacher`, `lesson
   planning`, `ferpa`), healthcare (`clinician vs cfo`, `hipaa`), infosec
   incident response (`downtime hours`, `breach`) for a node about business-plan
   risk scoring. Those create false lexical matches. Fixed by giving the
   generator the product's actual domain, drawn from the architecture's own
   BP.1.1.x / BP.4.x nodes — never from the test facts.

2. **Circular terms.** Terms that merely restate the node's own governance words
   can only test-fit. Measured at 1.9% in the sample, but filtered regardless.

Both filters run after generation and are reported, so the curation is
inspectable rather than implicit.

Writes a JSON file. Touches no table, embeds nothing.

    python scripts/generate_operational_terms_all.py
"""

import json
import logging
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.feed_classifier_v3 import (  # noqa: E402
    _call_llm,
    _parse_json_object,
    load_architecture,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

OUT = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "operational_terms_all.json")
MAX_WORKERS = 8
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 5400

# Nodes whose own text describes what the product IS. Used to ground the
# generator so it stops inventing K-12 and healthcare vocabulary. These are
# architecture rows, not test facts — no answer can leak through them.
CONTEXT_NODES = ("BP.1.1.1", "BP.1.1.3", "BP.1.1.4", "BP.1.1.5", "BP.4.4.1", "BP.5.1.1")

# Vocabulary from domains this product is not in. Observed in the 10-node
# sample; any generated term containing one of these is dropped.
FOREIGN_DOMAIN = frozenset(
    """teacher teachers student-teacher classroom lesson lessons grading gradebook
    k-12 k12 ferpa principal superintendent curriculum pupil
    clinician clinical patient patients nurse nurses hipaa ehr emr physician
    hospital diagnosis prescription
    retail ecommerce e-commerce shopper checkout basket sku warehouse inventory
    logistics shipment freight
    downtime outage breach incident-response pagerduty sla uptime latency
    crm salesforce hubspot pipeline-stage quota-attainment
    restaurant hotel booking airline passenger""".split()
)

TERMS_PROMPT = """You are given one node of a business-plan architecture for a \
specific product, written in formal governance register. List the words and \
phrases someone would actually TYPE when stating a fact that belongs at this node.

Return one JSON object, nothing else:

{"reason": "<one sentence: what a person filing a fact here is actually talking about>",
 "terms": ["<term>", ...]}

=== STAY INSIDE THIS PRODUCT'S WORLD — hard rule ===

The PRODUCT CONTEXT below tells you what this business actually is, who it sells
to, and what its users do. Every term must belong to THAT world.

Do not import vocabulary from other industries because the node's abstract
wording could apply to them. A node about "excluded segments" for a university
product is about universities and research institutions — not schools, not
hospitals, not retailers. A node about "risk scoring" in a business plan is about
business risk — not server outages.

=== WHAT COUNTS AS AN OPERATIONAL TERM ===

Concrete things that appear in real sentences:
  - job titles people actually say, in THIS market
  - artifacts and documents this business really produces or receives
  - units, currencies and quantities
  - named systems, standards and categories common in THIS market
  - the verbs of the activity ("signs off", "stalls", "renews")
  - informal synonyms for the formal concept ("who actually pays" for
    "economic buyer")

=== WHAT DOES NOT COUNT ===

Do NOT return the node's own governance vocabulary or a tidy paraphrase of it
("buyer definition", "authority scope", "evidence tier", "value logic",
"capture logic", "specification", "governance"). Those already appear in the
node text, so they add nothing. A useful term appears in the FACT and NOT in the
node text.

Write "reason" first, then let the terms follow from it.

Return 12-20 terms, lowercase, no duplicates."""


def build_product_context(arch) -> str:
    """Describe the product from the architecture's own definitional nodes."""
    parts = []
    for node_id in CONTEXT_NODES:
        node = arch.nodes.get(node_id)
        if not node:
            continue
        text = " ".join(
            str(node.get(k) or "").strip()
            for k in ("node_title", "purpose", "required_output")
            if node.get(k) and node.get(k) != "None"
        )
        if text:
            parts.append(f"- {text}")
    return "PRODUCT CONTEXT:\n" + "\n".join(parts)


def build_input(node: dict, context: str) -> str:
    """Render one node plus the shared product context."""
    def field(key: str) -> str:
        value = (node.get(key) or "").strip()
        return value if value and value != "None" else "(not specified)"

    return (
        f"{context}\n\n"
        f"NODE TO DESCRIBE:\n"
        f"node_id: {node['node_id']}\n"
        f"title: {field('node_title')}\n"
        f"purpose: {field('purpose')}\n"
        f"required_output: {field('required_output')}\n"
        f"evidence_requirement: {field('evidence_requirement')}"
    )


def generate(node: dict, context: str) -> tuple[str, dict]:
    """Generate operational terms for one node."""
    raw = _call_llm(
        TERMS_PROMPT,
        build_input(node, context),
        os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6"),
    )
    return node["node_id"], _parse_json_object(raw)


def node_haystack(node: dict) -> str:
    """The node's own text, for the circularity filter."""
    return " ".join(
        str(node.get(k) or "")
        for k in ("node_title", "purpose", "required_output", "evidence_requirement")
    ).lower()


def curate(terms: list[str], node: dict) -> tuple[list[str], Counter]:
    """Drop circular and foreign-domain terms. Returns (kept, why-dropped)."""
    haystack = node_haystack(node)
    kept: list[str] = []
    dropped: Counter = Counter()
    seen: set[str] = set()

    for raw in terms:
        term = str(raw).strip().lower()
        if not term or term in seen:
            dropped["duplicate_or_empty"] += 1
            continue
        seen.add(term)
        if term in haystack:
            dropped["circular"] += 1
            continue
        words = set(re.findall(r"[a-z0-9\-]+", term))
        if words & FOREIGN_DOMAIN:
            dropped["foreign_domain"] += 1
            continue
        kept.append(term)
    return kept, dropped


def main() -> None:
    """Generate, curate and save operational terms for every leaf."""
    arch = load_architecture()
    context = build_product_context(arch)
    print(context + "\n")

    leaves = [arch.nodes[n] for n in arch.leaf_ids]
    print(f"generating for {len(leaves)} leaves at {MAX_WORKERS} workers ...")

    raw_results: dict[str, dict] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(generate, n, context) for n in leaves]
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                node_id, payload = future.result(timeout=CALL_TIMEOUT)
                raw_results[node_id] = payload
            except Exception as exc:  # noqa: BLE001 — counted and reported
                failures += 1
                logging.error("generation failed: %s", str(exc)[:160])
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(leaves)}")

    curated: dict[str, list[str]] = {}
    totals: Counter = Counter()
    raw_total = 0
    for node_id, payload in raw_results.items():
        terms = payload.get("terms") or []
        raw_total += len(terms)
        kept, dropped = curate(terms, arch.nodes[node_id])
        curated[node_id] = kept
        totals.update(dropped)
        totals["kept"] += len(kept)

    json.dump(
        {"curated": curated, "raw": raw_results}, open(OUT, "w"), indent=1
    )

    print(f"\nnodes generated : {len(raw_results)}/{len(leaves)}  (failures: {failures})")
    print(f"terms generated : {raw_total}")
    print(f"  kept          : {totals['kept']} ({100.0 * totals['kept'] / max(raw_total, 1):.1f}%)")
    print(f"  circular      : {totals['circular']}")
    print(f"  foreign domain: {totals['foreign_domain']}")
    print(f"  dup/empty     : {totals['duplicate_or_empty']}")
    print(f"\nwrote {OUT}")
    print("NOTHING embedded, NOTHING written to any table.")


if __name__ == "__main__":
    main()
