"""Build candidate shortlists to help hand-label a correct gold standard.

For each of ~40 real Alex facts, finds the top-8 most semantically similar BP
nodes by raw Titan embedding similarity across all 746 nodes — independent of
the classifier's domain-gating (the part being evaluated). A human then reads
each shortlist and picks the correct node. Writes evaluation/gold_shortlists.json.

Run with `python -m scripts.build_gold_shortlists` (makes ~40 Bedrock calls).
"""

import json
import logging
from pathlib import Path

logging.disable(logging.WARNING)

_CEO = Path(__file__).parent.parent / "ceo_data"
_OUT = Path(__file__).parent.parent / "evaluation" / "gold_shortlists.json"


def _load_facts() -> list[dict]:
    """Assemble a diverse fact set from Alex's source files + the pilot set."""
    facts: list[dict] = []

    def add(text: str, source: str) -> None:
        text = " ".join((text or "").split())
        if text:
            facts.append({"text": text, "source": source})

    reg = json.loads((_CEO / "assumptions_register.json").read_text())
    for it in reg.get("assumptions", [])[:6]:
        add(it.get("assumption", ""), "assumptions_register")

    pr = json.loads((_CEO / "pricing_model.json").read_text())
    for it in pr.get("items", [])[:5]:
        add(f"{it.get('item','')}: {it.get('data','')}", "pricing_model")

    comp = json.loads((_CEO / "competitors.json").read_text())
    for it in comp.get("items", [])[:4]:
        add(f"{it.get('item','')}: {it.get('confirmed_data','')}"[:180], "competitors")

    risks = json.loads((_CEO / "compliance_risks.json").read_text())
    for it in risks.get("risks", [])[:5]:
        add(it.get("risk", ""), "compliance_risks")

    vp = json.loads((_CEO / "value_proposition.json").read_text())
    for it in vp.get("facts", [])[:4]:
        add(it.get("proposition", ""), "value_proposition")

    bi = json.loads((_CEO / "buyers_icp.json").read_text())
    for it in bi.get("facts", [])[:2]:
        add(it.get("fact", ""), "buyers_icp")

    # The 15 clearly-labeled pilot facts (their OLD labels are known-bad; we
    # re-label from scratch here, but they are good, diverse test sentences).
    import ast

    pilot_src = (Path(__file__).parent.parent / "tests" / "test_precision_mapping_pilot.py").read_text()
    for node in ast.parse(pilot_src).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "PILOT_FACTS" for t in node.targets
        ):
            for f in ast.literal_eval(node.value):
                if f.get("expected_node_id"):
                    add(f["text"], "pilot")

    return facts


def _load_nodes() -> list[dict]:
    """Load all 746 BP nodes with their stored embeddings from Supabase."""
    from memory.supabase_client import supabase

    nodes: list[dict] = []
    start = 0
    while True:
        batch = (
            supabase.table("knowledge_base")
            .select("metadata,embedding")
            .eq("metadata->>layer", "bp_architecture")
            .range(start, start + 999)
            .execute()
            .data
        )
        for row in batch:
            emb = row.get("embedding")
            if isinstance(emb, str):
                emb = json.loads(emb)
            meta = row.get("metadata") or {}
            if emb and meta.get("node_id"):
                nodes.append(
                    {
                        "node_id": meta["node_id"],
                        "node_title": meta.get("node_title", ""),
                        "embedding": emb,
                    }
                )
        if len(batch) < 1000:
            break
        start += 1000
    return nodes


def main() -> None:
    import numpy as np

    from services.rag_service import embed

    facts = _load_facts()
    nodes = _load_nodes()
    print(f"{len(facts)} facts, {len(nodes)} node embeddings loaded")

    # Node purposes come from the architecture file (not stored on the embedding row).
    arch = {
        n["node_id"]: n
        for n in json.loads((_CEO / "bp_architecture.json").read_text()).get("nodes", [])
    }

    mat = np.array([n["embedding"] for n in nodes])
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)

    out = []
    for i, f in enumerate(facts):
        q = np.array(embed(f["text"], input_type="search_query"))
        q = q / np.linalg.norm(q)
        sims = mat @ q
        top = sims.argsort()[::-1][:8]
        candidates = []
        for idx in top:
            nid = nodes[idx]["node_id"]
            purpose = (arch.get(nid, {}).get("purpose") or "")[:120]
            candidates.append(
                {
                    "node_id": nid,
                    "title": nodes[idx]["node_title"],
                    "purpose": purpose,
                    "sim": round(float(sims[idx]), 3),
                }
            )
        out.append({"text": f["text"], "source": f["source"], "candidates": candidates})
        print(f"  [{i+1}/{len(facts)}] {f['text'][:55]}")

    _OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {_OUT}")


if __name__ == "__main__":
    main()
