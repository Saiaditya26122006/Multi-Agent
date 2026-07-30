#!/usr/bin/env python3
"""Calibrate the dedupe threshold against the recorded 27-fact reference run.

Prints every fact pair scoring above a floor, so the gap between the real
duplicate pair and the highest-scoring non-duplicate is visible rather than
assumed. No LLM call: it reads the facts already recorded on the batch.

    python scripts/calibrate_fact_dedupe.py [run_id]
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services import feed_batch_store  # noqa: E402
from services.fact_dedupe import (  # noqa: E402
    DEFAULT_THRESHOLD,
    dedupe,
    pairwise_report,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REFERENCE_RUN = "feed-73eb3e7baa5b"


@dataclass
class _Fact:
    """Minimal stand-in for a chunker Fact, built from a recorded card."""

    fact: str
    source_quote: str
    start_char: Optional[int]
    end_char: Optional[int]
    index: int
    merged_spans: list[dict[str, Any]] = field(default_factory=list)


def main() -> None:
    """Print the pairwise similarity table and the resulting collapse."""
    run_id = sys.argv[1] if len(sys.argv) > 1 else REFERENCE_RUN
    payload = feed_batch_store.get_batch(run_id)
    if not payload:
        raise SystemExit(f"batch {run_id} not found")

    facts = [
        _Fact(
            fact=c["fact"],
            source_quote=c.get("source_quote", ""),
            start_char=c.get("start_char"),
            end_char=c.get("end_char"),
            index=c["index"],
        )
        for c in payload["cards"]
    ]

    print(f"{run_id}: {len(facts)} facts, threshold={DEFAULT_THRESHOLD}\n")
    print("pairs scoring >= 0.50:")
    print("  sim   merge  reason                      facts")
    for score, merge, reason, left, right in pairwise_report(facts, floor=0.5):
        print(
            "  %.2f  %-5s  %-26s  %s\n%*s%s"
            % (score, "YES" if merge else "no", reason, left[:70], 44, "", right[:70])
        )

    survivors, drops = dedupe(facts)
    print(f"\n{len(facts)} facts -> {len(survivors)} survivors, {len(drops)} dropped")
    for d in drops:
        print(
            "  dropped fact %d (sim %.2f): %r\n     into fact %d: %r"
            % (
                d["dropped_index"] + 1,
                d["similarity"],
                d["dropped_fact"],
                d["kept_index"] + 1,
                d["kept_fact"],
            )
        )


if __name__ == "__main__":
    main()
