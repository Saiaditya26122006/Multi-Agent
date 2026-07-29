#!/usr/bin/env python3
"""Generate operational vocabulary per architecture node — SAMPLE ONLY, for inspection.

The lexical side of hybrid retrieval underperforms because architecture nodes
are written in governance register ("Define the buyer in one bounded statement,
distinguishing buyer from user...") while Alex writes in operational register
("the dean is who actually buys, not the researcher"). BM25 has nothing to match.

This generates the missing vocabulary. It writes NOTHING and embeds NOTHING —
it prints terms for a sample of nodes so a human can judge whether they are real
operational vocabulary or governance paraphrase wearing a different coat.

⚠️ **The circularity risk is structural and cannot be engineered away here.**
The generator sees only the node's own `purpose` and `required_output`. If it
merely restates them with shorter words, the terms will match governance
phrasing that BM25 already matches, and the whole exercise is circular. The
prompt pushes hard against that — concrete nouns, roles, artifacts, units, the
words a person would actually type — but the ONLY real check is the inspection
this script exists to enable. Nothing downstream should run until a human has
read the output and said it is not circular.

Deliberately NOT grounded in ceo_data or any real document: the paraphrase test
facts describe the same business, so mining real text for node vocabulary would
leak the answers into the index and reproduce the earlier memorisation failure
by a different route.

    python scripts/generate_operational_terms.py
"""

import json
import logging
import os
import sys
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

OUT = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "operational_terms.json")
MAX_WORKERS = 5
CALL_TIMEOUT = 180

# The ten paraphrase targets. Chosen because they ARE the validation gate — if
# the generated terms would not help these facts match, nothing else matters.
SAMPLE_NODES = [
    "BP.5.3.3", "BP.5.1.2", "BP.7.2.3", "BP.10.1.3", "BP.4.1.3",
    "BP.9.1.1", "BP.5.1.4", "BP.8.1.5", "BP.12.1.4", "BP.6.1.4",
]

TERMS_PROMPT = """You are given one node of a business-plan architecture, written \
in formal governance register. Your job is to list the words and phrases a busy \
founder would actually TYPE when stating a fact that belongs at this node.

Return one JSON object, nothing else:

{"reason": "<one sentence: what a person filing a fact here is actually talking about>",
 "terms": ["<term>", ...]}

=== WHAT COUNTS AS AN OPERATIONAL TERM ===

Concrete things that appear in real sentences:
  - job titles and roles people actually say ("dean", "procurement", "IT security")
  - artifacts and documents ("purchase order", "DPA", "invoice", "pilot report")
  - units, currencies and quantities ("euros", "seats", "per-seat", "headcount")
  - systems and categories by their common name ("SSO", "GDPR", "Turnitin")
  - the verbs of the activity ("signs off", "stalls", "renews", "churns")
  - informal synonyms for the formal concept ("who actually pays" for "economic
    buyer"; "gets blocked" for "procurement barrier")

=== WHAT DOES NOT COUNT — this is the failure mode ===

Do NOT return the node's own governance vocabulary or a tidy paraphrase of it.
These are worthless because they already appear in the node text:

  "buyer definition", "authority scope", "evidence tier", "governance",
  "specification", "bounded statement", "decision implication",
  "validation requirement", "assessment criteria"

A useful term is one that appears in the FACT and NOT in the node text. If you
find yourself reusing a phrase from the purpose or required_output, drop it.

Write "reason" first, then let the terms follow from it.

Return 12-20 terms, lowercase, no duplicates, no explanations inside the list."""


def build_input(node: dict) -> str:
    """Render one node for the term generator. Governance fields only."""
    def field(key: str) -> str:
        value = (node.get(key) or "").strip()
        return value if value and value != "None" else "(not specified)"

    return (
        f"node_id: {node['node_id']}\n"
        f"title: {field('node_title')}\n"
        f"purpose: {field('purpose')}\n"
        f"required_output: {field('required_output')}\n"
        f"evidence_requirement: {field('evidence_requirement')}"
    )


def generate(node: dict) -> tuple[str, dict]:
    """Generate operational terms for one node."""
    raw = _call_llm(
        TERMS_PROMPT,
        build_input(node),
        os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6"),
    )
    return node["node_id"], _parse_json_object(raw)


def overlap(terms: list[str], node: dict) -> list[str]:
    """Terms that already appear in the node's own text — the circular ones."""
    haystack = " ".join(
        str(node.get(k) or "")
        for k in ("node_title", "purpose", "required_output", "evidence_requirement")
    ).lower()
    return [t for t in terms if t.lower() in haystack]


def main() -> None:
    """Generate and print terms for the sample nodes. Embeds nothing."""
    arch = load_architecture()
    nodes = [arch.nodes[n] for n in SAMPLE_NODES if n in arch.nodes]
    print(f"generating operational terms for {len(nodes)} sample nodes ...\n")

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(generate, n) for n in nodes]
        for future in as_completed(futures, timeout=600):
            try:
                node_id, payload = future.result(timeout=CALL_TIMEOUT)
                results[node_id] = payload
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed
                logging.error("term generation failed: %s", str(exc)[:200])

    total_terms = 0
    total_circular = 0
    for node_id in SAMPLE_NODES:
        payload = results.get(node_id)
        node = arch.nodes.get(node_id, {})
        print("=" * 88)
        print(f"{node_id}  {node.get('node_title')}")
        print("=" * 88)
        print(f"  purpose        : {str(node.get('purpose'))[:150]}")
        print(f"  required_output: {str(node.get('required_output'))[:150]}")
        if not payload:
            print("  !! generation failed")
            continue
        terms = [str(t).strip().lower() for t in payload.get("terms", []) if str(t).strip()]
        circular = overlap(terms, node)
        total_terms += len(terms)
        total_circular += len(circular)
        print(f"\n  reason         : {payload.get('reason', '')[:200]}")
        print(f"  TERMS ({len(terms)}):")
        for term in terms:
            mark = "  <-- already in node text" if term in circular else ""
            print(f"     {term}{mark}")
        print()

    if total_terms:
        print("=" * 88)
        print(
            f"CIRCULARITY CHECK: {total_circular}/{total_terms} generated terms "
            f"({100.0 * total_circular / total_terms:.1f}%) already appear verbatim "
            f"in their own node's text."
        )
        print(
            "  A high number means the generator paraphrased governance prose and\n"
            "  the exercise is circular. A low number means it produced vocabulary\n"
            "  the node did not already have — which is the whole point.\n"
            "  This check is necessary, not sufficient: read the terms."
        )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(results, open(OUT, "w"), indent=1)
    print(f"\nsample written to {OUT}")
    print("NOTHING embedded, NOTHING indexed, NOTHING written to any table.")


if __name__ == "__main__":
    main()
