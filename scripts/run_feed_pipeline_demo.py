#!/usr/bin/env python3
"""End-to-end Feed run on one real document — review-assist.

Chunker -> section-first retrieval -> sibling judge ranks -> review cards.

**Nothing is stored.** Facts reach knowledge_base only through
`feed_pipeline.confirm_card()`, which a human drives. This script shows the
cards a reviewer would be handed, including the ranked shortlist and both
independent check flags.

    python scripts/run_feed_pipeline_demo.py [FILE] [--full]
"""

import argparse
import logging
import os
import sys
from collections import Counter

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.feed_pipeline import process_document  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_DOC = os.path.join(PROJECT_ROOT, "ceo_data", "deck.txt")


def main() -> None:
    """Run one document through the pipeline and print the review cards."""
    parser = argparse.ArgumentParser()
    parser.add_argument("document", nargs="?", default=DEFAULT_DOC)
    parser.add_argument("--full", action="store_true", help="print every card")
    args = parser.parse_args()

    with open(args.document) as handle:
        text = handle.read()
    name = os.path.basename(args.document)
    print(f"document: {name}  ({len(text)} chars)\n")

    batch = process_document(text, name)

    print("\n" + "=" * 100)
    print(f"RUN {batch.run_id} — {batch.total_facts} review cards in {batch.seconds}s")
    print("=" * 100)
    print(
        f"  {batch.proposed} with a suggested node · "
        f"{batch.no_proposal} with no match · "
        f"{batch.flagged_extraction} extraction-flagged · "
        f"{batch.flagged_degraded} pointing at an incomplete node"
    )
    print("  NOTHING STORED — each fact is written only when a human confirms it.")

    shown = batch.cards if args.full else batch.cards[:8]
    for card in shown:
        span = (
            f"{card.start_char}:{card.end_char}"
            if card.start_char is not None
            else "no span"
        )
        print("\n" + "-" * 100)
        print(f"  #{card.index}  {card.fact}")
        print(f"      source: {card.source_document} [{span}]")
        if card.checks:
            print(f"      CHECKS: {', '.join(card.checks)}"
                  f"{'  verdict=' + str(card.verdict) if card.needs_review else ''}"
                  f"{'  degraded=' + str(card.degraded_reason) if card.degraded_target else ''}")
        if not card.shortlist:
            print(f"      no candidate matched — {card.proposal_reason[:70]}")
            continue
        for entry in card.shortlist:
            mark = "  [DEGRADED]" if entry["degraded"] else ""
            print(f"      {entry['rank']}. {entry['node_id']:<11} "
                  f"{str(entry['title'])[:38]:<40}{mark}")
            print(f"         {entry['note'][:88]}")

    if not args.full and len(batch.cards) > len(shown):
        print(f"\n  ... {len(batch.cards) - len(shown)} more cards (--full to see all)")

    ranks = Counter(len(c.shortlist) for c in batch.cards)
    print(f"\n  shortlist sizes: {dict(sorted(ranks.items()))}")
    print("Nothing written to knowledge_base or bp_architecture.")


if __name__ == "__main__":
    main()
