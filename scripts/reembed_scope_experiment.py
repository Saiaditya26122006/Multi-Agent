#!/usr/bin/env python3
"""Scope re-embedding experiment — does richer node text fix retrieval?

Builds two alternative embeddings for every clean-pool node and measures
recall@k against the same locked answer key:

    baseline   title + purpose + required_output          (what is in the table)
    variant A  ancestry + title + purpose + records + format   (no LLM)
    variant B  variant A + three generated "statements that belong here"

Variant A tests whether the fields were badly assembled. Variant B tests whether
the fact-to-node gap needs an explicit bridge — node text is governance prose,
facts are concrete business statements, and nothing lexical connects them.

Deliberately excluded from both variants:
  prohibited_claims_inference_patterns — negative space; "Must not claim value is
    perceived by users" would attract user-value facts into the node meant to
    exclude them. It is a judge signal, not a retrieval signal.
  evidence_requirement — source-family metadata, near-identical across hundreds
    of nodes, so it adds vocabulary without adding discrimination.

Writes only to local cache files. bp_architecture is never modified.

    python scripts/reembed_scope_experiment.py
"""

import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v2 import load_leaf_ids  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

CACHE = "/home/saiaditya26122006/.claude/jobs/bdcd467b/tmp"
KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
KS = (1, 3, 5, 10, 20, 40)
GEN_CALL_TIMEOUT = 120   # per node; a hung Bedrock call must not stall the pool
GEN_TOTAL_TIMEOUT = 3600  # whole generation phase
WATCH = ["BP.9.1.1", "BP.8.1.2", "BP.6.2.2", "BP.5.3.1", "BP.1.1.4", "BP.4.4.2"]

# Hand-written oblique restatements of 10 answer-key facts — the messy, clipped
# register a founder actually types, not the clean textbook phrasing in the key.
# If enrichment only memorised the obvious wording, recall drops on these.
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

# Set in main() from bp_architecture's own product-definition nodes. Never from
# the answer key — see the leakage note in the module docstring.
PRODUCT_CONTEXT = ""

SCOPE_PROMPT_TEMPLATE = """You write retrieval aids for a business-plan architecture.

PRODUCT DOMAIN (this is what the business plan is about):
{context}

Given one node, write THREE short statements of the kind this company's founder
would write that belong in this node. Concrete operational statements, not
definitions — the sort of sentence someone would actually type in a note.

Rules:
  - Each under 15 words.
  - Concrete: name the actor, the artifact, the number.
  - GROUND THEM IN THE PRODUCT DOMAIN ABOVE. Use its actors, artifacts and
    workflows. Do NOT write generic SaaS, CRM, supply-chain, hospital or
    enterprise-IT examples — those inject vocabulary from the wrong industry
    and make the node harder to find, not easier.
  - Do NOT restate the node's title or purpose back to me.
  - Invent no specific company names, customers or figures presented as real.

Return ONLY the three statements separated by " | ". No numbering, no commentary."""


def build_product_context(by_id: dict) -> str:
    """Compose the product domain from bp_architecture's own definition nodes.

    Sourced only from BP.1.1.* and BP.4.4.1 / BP.5.1.* — the architecture's own
    description of what the product is and who uses it. The answer key is not
    read anywhere in this function or its callers before generation completes.
    """
    picks = ["BP.1.1.1", "BP.1.1.3", "BP.1.1.4", "BP.1.1.5", "BP.4.4.1", "BP.5.1.1"]
    lines = []
    for nid in picks:
        n = by_id.get(nid)
        if not n:
            continue
        body = (n.get("required_output") or n.get("purpose") or "").strip()
        if body:
            lines.append(f"- {n['node_title']}: {body[:180]}")
    return "\n".join(lines)


def compose_a(node: dict, dom_title: str, sec_title: str) -> str:
    """Variant A text: ancestry + title + purpose + records + format."""
    path = " > ".join(x for x in (dom_title, sec_title, node["node_title"]) if x)
    parts = [path, f"{node['node_title']}. {node.get('purpose') or ''}".strip()]
    if node.get("required_output"):
        parts.append(f"Records: {node['required_output']}")
    if node.get("output_format"):
        parts.append(f"Format: {node['output_format']}")
    return "\n".join(parts)


_bedrock = None


def _client():
    """One shared Bedrock client. Building one per call re-resolves credentials
    on every request and stalls the whole thread pool."""
    global _bedrock
    if _bedrock is None:
        import boto3
        from botocore.config import Config

        _bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
            config=Config(
                read_timeout=45,
                connect_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
                max_pool_connections=16,
            ),
        )
    return _bedrock


def gen_scope(args) -> tuple[str, str]:
    """Generate the 'statements that belong here' line for one node."""
    node_id, text = args
    client = _client()
    model = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    for attempt in range(3):
        try:
            r = client.converse(
                modelId=model,
                system=[{"text": SCOPE_PROMPT_TEMPLATE.format(context=PRODUCT_CONTEXT)}],
                messages=[{"role": "user", "content": [{"text": text}]}],
                inferenceConfig={"maxTokens": 200},
            )
            return node_id, r["output"]["message"]["content"][0]["text"].strip()
        except Exception as exc:  # noqa: BLE001 — retried, then reported
            if attempt == 2:
                logging.error("[scope] %s failed: %s", node_id, str(exc)[:100])
                return node_id, ""
            time.sleep(2 ** attempt)
    return node_id, ""


def embed_all(texts: dict[str, str], label: str) -> dict[str, list[float]]:
    """Embed a dict of node_id -> text, caching to disk."""
    path = os.path.join(CACHE, f"scope_emb_{label}.json")
    if os.path.exists(path):
        print(f"  [{label}] using cache {path}")
        return json.load(open(path))
    out: dict[str, list[float]] = {}
    t0 = time.time()
    for i, (nid, text) in enumerate(texts.items(), 1):
        out[nid] = embed(text, input_type="search_document")
        if i % 100 == 0:
            print(f"  [{label}] {i}/{len(texts)}  ({time.time() - t0:.0f}s)", flush=True)
    json.dump(out, open(path, "w"))
    return out


def recall(vecs: dict[str, list[float]], facts, targets_ok) -> tuple[dict, dict]:
    """Return (hits by k, rank of the correct node per fact)."""
    ids = list(vecs)
    mat = np.vstack([np.asarray(vecs[i], dtype=np.float32) for i in ids])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    hits = {k: 0 for k in KS}
    ranks = {}
    for row, q in facts:
        sims = mat @ q
        order = np.argsort(-sims)
        ranked = [ids[int(i)] for i in order]
        tg = [t for t in row["correct_node_id"].split("|") if t in targets_ok]
        best = min((ranked.index(t) + 1 for t in tg if t in ids), default=10**6)
        ranks[row["correct_node_id"]] = best
        for k in KS:
            if best <= k:
                hits[k] += 1
    return hits, ranks



def contamination_guard(lines, facts, embed_fn, cut=0.75):
    """Flag test facts that have a near-duplicate among their node's synthetic facts.

    The generator never saw the answer key, but a node definition specific enough
    ("threshold range, approval requirements above threshold") forces every faithful
    exemplar into the same shape as the test fact. When that happens, retrieval
    matches by duplication rather than by understanding, and recall is inflated.

    Returns (near_dup_rows, similarity_by_row_id).
    """
    sims = {}
    near = set()
    for row, q in facts:
        best = 0.0
        for tgt in row["correct_node_id"].split("|"):
            line = lines.get(tgt) or ""
            for piece in [p.strip() for p in line.split("|") if p.strip()]:
                v = np.asarray(embed_fn(piece, input_type="search_document"), dtype=np.float32)
                v /= np.linalg.norm(v)
                best = max(best, float(q @ v))
        sims[row["fact_text"]] = round(best, 4)
        if best >= cut:
            near.add(row["fact_text"])
    return near, sims


def main() -> None:
    """Build both variants, measure, and report."""
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    leaf_ids = load_leaf_ids(sb)

    print("loading nodes ...")
    rows, start = [], 0
    while True:
        r = (
            sb.table("bp_architecture")
            .select("node_id,node_title,purpose,required_output,output_format,"
                    "degraded_target,embedding")
            .range(start, start + 99)
            .execute()
        )
        if not r.data:
            break
        rows += r.data
        start += 100
    by_id = {r["node_id"]: r for r in rows}

    global PRODUCT_CONTEXT
    PRODUCT_CONTEXT = build_product_context(by_id)
    print("product context (from bp_architecture, not the answer key):")
    for line in PRODUCT_CONTEXT.split("\n"):
        print("   " + line[:100])

    import re

    BP = re.compile(r"^Governs .+ without validating downstream commercial success\.?$", re.I)
    BR = re.compile(r"^.+ specification and governance\.?$", re.I)
    boiler = {
        r["node_id"] for r in rows
        if BP.match((r.get("purpose") or "").strip())
        and BR.match((r.get("required_output") or "").strip())
    }
    degraded = {r["node_id"] for r in rows if r["degraded_target"]}
    pool = [
        r for r in rows
        if r["node_id"] in leaf_ids
        and r["node_id"] not in boiler
        and r["node_id"] not in degraded
        and r["embedding"] is not None
    ]
    pool_ids = {r["node_id"] for r in pool}
    print(f"clean pool: {len(pool)} nodes")

    def title(nid):
        n = by_id.get(nid)
        return n["node_title"] if n else ""

    text_a = {
        r["node_id"]: compose_a(
            r,
            title(".".join(r["node_id"].split(".")[:2])),
            title(".".join(r["node_id"].split(".")[:3])),
        )
        for r in pool
    }

    scope_path = os.path.join(CACHE, "scope_lines.json")
    if os.path.exists(scope_path):
        print("using cached scope lines")
        lines = json.load(open(scope_path))
    else:
        print(f"generating scope lines for {len(text_a)} nodes (Haiku, 4 threads) ...")
        # as_completed + a per-future timeout: an ordered ex.map blocks the whole
        # pipeline behind one slow call, which turned a single hung Bedrock
        # request into a ~70-minute stall on two separate runs.
        lines = {}
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(gen_scope, item): item[0] for item in text_a.items()}
            done = 0
            for fut in as_completed(futures, timeout=GEN_TOTAL_TIMEOUT):
                nid = futures[fut]
                try:
                    _, out = fut.result(timeout=GEN_CALL_TIMEOUT)
                except (FutureTimeout, Exception) as exc:  # noqa: BLE001 — logged
                    logging.error("[scope] %s gave up: %s", nid, str(exc)[:90])
                    out = ""
                lines[nid] = out
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(text_a)}  ({time.time() - t0:.0f}s)", flush=True)
        blank = sum(1 for v in lines.values() if not v)
        if blank:
            print(f"  WARNING: {blank} nodes have no scope line (timed out or failed)")
        json.dump(lines, open(scope_path, "w"))

    text_b = {
        nid: (t + (f"\nStatements that belong here: {lines[nid]}" if lines.get(nid) else ""))
        for nid, t in text_a.items()
    }

    print("\nembedding variant A ...")
    vec_a = embed_all(text_a, "A")
    print("embedding variant B ...")
    vec_b = embed_all(text_b, "B")

    base = {
        r["node_id"]: (
            json.loads(r["embedding"]) if isinstance(r["embedding"], str) else r["embedding"]
        )
        for r in pool
    }

    key = list(csv.DictReader(open(KEY)))
    sel = [r for r in key
           if r["scoring_bucket"] == "a_classifier" and r["category"] in ("clear", "hard")]
    print(f"\nembedding {len(sel)} facts ...")
    facts = []
    for r in sel:
        q = np.asarray(embed(r["fact_text"], input_type="search_query"), dtype=np.float32)
        facts.append((r, q / np.linalg.norm(q)))

    n = len(facts)
    print("\n" + "=" * 78)
    print(f"RECALL@K — {n} bucket-a facts, clean pool of {len(pool)} nodes")
    print("=" * 78)
    print(f"{'embedding':<34}" + "".join(f"{'@' + str(k):>8}" for k in KS))
    print("-" * 78)
    allranks = {}
    for label, vecs in (("baseline (in table today)", base),
                        ("variant A (ancestry+fields)", vec_a),
                        ("variant B (A + scope lines)", vec_b)):
        hits, ranks = recall(vecs, facts, pool_ids)
        allranks[label] = ranks
        print(f"{label:<34}" + "".join(f"{100.0 * hits[k] / n:7.1f}%" for k in KS))

    print("\n" + "=" * 78)
    print("RANK OF THE CORRECT NODE — the deep misses")
    print("=" * 78)
    print(f"{'node':<12}{'baseline':>10}{'variant A':>11}{'variant B':>11}")
    print("-" * 46)
    for w in WATCH:
        r0 = next((v for k, v in allranks["baseline (in table today)"].items() if k == w), None)
        rA = next((v for k, v in allranks["variant A (ancestry+fields)"].items() if k == w), None)
        rB = next((v for k, v in allranks["variant B (A + scope lines)"].items() if k == w), None)
        if r0 is None:
            continue
        fmt = lambda x: ("-" if x is None else (">1000" if x > 1000 else str(x)))
        print(f"{w:<12}{fmt(r0):>10}{fmt(rA):>11}{fmt(rB):>11}")

    # ---- contamination guard -------------------------------------------
    print("\n" + "=" * 78)
    print("CONTAMINATION GUARD — test facts with a synthetic near-duplicate")
    print("=" * 78)
    near, sims = contamination_guard(lines, facts, embed)
    vals = sorted(sims.values(), reverse=True)
    print(f"  similarity(test fact, its node's synthetic facts):")
    print(f"    max={vals[0]}  p90={vals[int(len(vals)*0.1)]}  median={vals[len(vals)//2]}  min={vals[-1]}")
    print(f"  flagged as near-duplicate (>= 0.75): {len(near)} of {len(facts)}")
    for f, v in sorted(sims.items(), key=lambda kv: -kv[1])[:6]:
        mark = "NEAR-DUP" if f in near else "        "
        print(f"    {v:.3f} {mark} {f[:58]!r}")

    print("\n" + "=" * 78)
    print("RECALL@10 THREE WAYS — variant B (the enrichment being judged)")
    print("=" * 78)
    for label, subset in (
        ("all facts", facts),
        ("near-duplicate subset (inflated)", [(r, q) for r, q in facts if r["fact_text"] in near]),
        ("NON-duplicate subset (real signal)", [(r, q) for r, q in facts if r["fact_text"] not in near]),
    ):
        if not subset:
            print(f"  {label:<36} n=0")
            continue
        h, _ = recall(vec_b, subset, pool_ids)
        print(f"  {label:<36} n={len(subset):<3} "
              + "  ".join(f"@{k}={100.0*h[k]/len(subset):.1f}%" for k in (1, 5, 10)))

    # ---- oblique paraphrase generalisation test -------------------------
    print("\n" + "=" * 78)
    print("OBLIQUE PARAPHRASE TEST — messy phrasing, same targets")
    print("=" * 78)
    para = []
    for tgt, text in PARAPHRASES:
        if tgt not in pool_ids:
            continue
        q = np.asarray(embed(text, input_type="search_query"), dtype=np.float32)
        para.append(({"correct_node_id": tgt, "fact_text": text}, q / np.linalg.norm(q)))
    for label, vecs in (("baseline", base), ("variant A", vec_a), ("variant B", vec_b)):
        h, rk = recall(vecs, para, pool_ids)
        print(f"  {label:<12} n={len(para)}  "
              + "  ".join(f"@{k}={100.0*h[k]/len(para):.1f}%" for k in (1, 5, 10, 40)))
    print("\n  per-paraphrase rank under variant B:")
    _, rkB = recall(vec_b, para, pool_ids)
    for tgt, text in PARAPHRASES:
        if tgt in rkB:
            r = rkB[tgt]
            print(f"    rank {('>1000' if r > 1000 else r):>5}  {tgt:<11} {text[:52]!r}")

    print("\nSample variant B text:")
    ex = WATCH[0]
    if ex in text_b:
        for line in text_b[ex].split("\n"):
            print("   " + line[:96])

    print("\nNothing written to bp_architecture. Cache only.")


if __name__ == "__main__":
    main()
