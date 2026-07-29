#!/usr/bin/env python3
"""Cost and latency of Feed classifier v3 — per fact and per document.

Independent of whether the classifier is accurate enough to ship: a judge call
costs the same whether it is right or wrong. This answers whether the live
upload experience is viable at all.

Measures, in order:

  1. architecture load (once per process, not per fact)
  2. embedding latency per fact
  3. judge latency and token usage, top-1 (~9 candidates) vs top-2 (~18)
  4. wall-clock for a realistic 20-fact document at several concurrency levels,
     which is what decides whether facts can be judged in parallel

Bedrock throttling is the reason concurrency is swept rather than assumed —
PROJECT_STATE records a previous classification path capped at 3 concurrent
calls for throttle headroom. Throttle retries are counted and reported.

Writes nothing.

    python scripts/measure_classifier_v3_cost.py
"""

import csv
import logging
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import (  # noqa: E402
    JUDGE_PROMPT,
    _build_judge_input,
    _get_bedrock,
    classify,
    load_architecture,
    rank_sections,
)

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
DOC_SIZE = 20
CONCURRENCIES = (1, 4, 8)
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 1800

# Anthropic first-party rates for Sonnet, USD per 1M tokens. Bedrock is
# partner-priced and may differ — confirm against the AWS price list before
# quoting these to Alex as a bill.
USD_IN_PER_M = 3.00
USD_OUT_PER_M = 15.00

_throttles = 0


def timed_judge(fact: str, arch, n_sections: int) -> dict:
    """Run exactly the call classify() would make, capturing latency and tokens."""
    global _throttles
    vector = embed(fact, input_type="search_query")
    sections = rank_sections(vector, arch, top_n=max(n_sections, 2))[:n_sections]
    candidate_ids: list[str] = []
    for section in sections:
        candidate_ids += arch.siblings.get(section.section_id, [])

    payload = _build_judge_input(fact, candidate_ids, arch)
    model = os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6")
    client = _get_bedrock()

    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model,
            system=[{"text": JUDGE_PROMPT}],
            messages=[{"role": "user", "content": [{"text": payload}]}],
            inferenceConfig={"maxTokens": 2048},
        )
    except Exception as exc:  # noqa: BLE001 — counted, then surfaced
        _throttles += 1
        logging.error("judge call failed: %s", str(exc)[:160])
        raise
    elapsed = time.perf_counter() - start
    usage = response["usage"]
    return {
        "seconds": elapsed,
        "candidates": len(candidate_ids),
        "in_tokens": usage["inputTokens"],
        "out_tokens": usage["outputTokens"],
    }


def summarise(label: str, samples: list[dict]) -> None:
    """Print latency and token statistics for one batch of judge calls."""
    times = sorted(s["seconds"] for s in samples)
    cands = statistics.mean(s["candidates"] for s in samples)
    tin = statistics.mean(s["in_tokens"] for s in samples)
    tout = statistics.mean(s["out_tokens"] for s in samples)
    cost = tin / 1e6 * USD_IN_PER_M + tout / 1e6 * USD_OUT_PER_M
    print(
        f"  {label:<26} n={len(times):<3} "
        f"p50={times[len(times) // 2]:5.2f}s  "
        f"p90={times[int(len(times) * 0.9) - 1]:5.2f}s  "
        f"max={times[-1]:5.2f}s  "
        f"cands={cands:4.1f}  in={tin:6.0f} out={tout:5.0f}  "
        f"${cost:.4f}/fact"
    )


def main() -> None:
    """Measure load, embed, judge, and whole-document latency."""
    start = time.perf_counter()
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    arch = load_architecture(sb)
    load_seconds = time.perf_counter() - start
    print(f"architecture load (once per process): {load_seconds:.2f}s "
          f"for {len(arch.leaf_ids)} leaves\n")

    rows = [
        r["fact_text"]
        for r in csv.DictReader(open(KEY))
        if r["scoring_bucket"] == "a_classifier"
    ]
    doc = rows[:DOC_SIZE]

    # ---- 1. embedding ---------------------------------------------------
    embed_times = []
    for fact in doc[:8]:
        t0 = time.perf_counter()
        embed(fact, input_type="search_query")
        embed_times.append(time.perf_counter() - t0)
    embed_times.sort()
    print("EMBEDDING (Titan v2, one call per fact)")
    print(f"  p50={embed_times[len(embed_times) // 2]:.2f}s  "
          f"max={embed_times[-1]:.2f}s\n")

    # ---- 2. judge latency, sequential, both configurations ---------------
    print("JUDGE CALL (Sonnet, sequential — no contention)")
    for n_sections in (1, 2):
        samples = [timed_judge(fact, arch, n_sections) for fact in doc[:6]]
        summarise(f"top-{n_sections} section(s)", samples)

    # ---- 3. whole document, swept over concurrency -----------------------
    print(f"\nDOCUMENT OF {DOC_SIZE} FACTS — wall clock end to end")
    print("  (embed + section rank + judge, exactly as the pipeline runs it)")
    baseline = None
    for workers in CONCURRENCIES:
        failures = 0
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(classify, fact, arch, sections_to_consider=1)
                for fact in doc
            ]
            for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
                try:
                    future.result(timeout=CALL_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 — counted and reported
                    failures += 1
                    logging.error("classify failed: %s", str(exc)[:160])
        elapsed = time.perf_counter() - t0
        if baseline is None:
            baseline = elapsed
        speedup = baseline / elapsed
        print(
            f"  workers={workers:<3} {elapsed:6.1f}s total  "
            f"{elapsed / DOC_SIZE:5.2f}s/fact  speedup={speedup:4.1f}x  "
            f"failures={failures}"
        )

    print(
        "\nNote: top-2 fallback doubles the candidate set, not the call count — "
        "it is still ONE judge call per fact."
    )
    print("Nothing written.")


if __name__ == "__main__":
    main()
