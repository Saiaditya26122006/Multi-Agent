#!/usr/bin/env python3
"""Re-run the 27-card reference paste and diff against the recorded baseline.

The baseline is the live run feed-73eb3e7baa5b, whose 27 cards are still in the
batch store. The paste itself was never persisted — only each card's verbatim
quote and character span — so evaluation/feed_27_card_paste.txt is a
reconstruction from those spans, validated by re-locating all 27 quotes at their
recorded offsets. See ``--verify-fixture``.

Reports the five tracked errors explicitly, then every card's before/after node,
then the flagged-card count so the remaining content debt is quantified.

    python scripts/rerun_feed_27.py                  # full re-run (~1-3 min)
    python scripts/rerun_feed_27.py --control        # same, all three fixes off
    python scripts/rerun_feed_27.py --verify-fixture # spans only, no LLM calls

The --control run exists because neither the chunker nor the judge is
deterministic: two runs of the same text disagree on a handful of cards on their
own. Comparing the fixed run against the recorded baseline alone cannot tell a
fix-caused change from that drift; comparing it against a control run on the same
day, same text, fixes off, can.
"""

import json
import logging
import os
import sys
from typing import Any, Optional

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services import feed_batch_store  # noqa: E402
from services.fact_dedupe import similarity  # noqa: E402
from services.feed_pipeline import process_document  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

BASELINE_RUN = "feed-73eb3e7baa5b"
FIXTURE = os.path.join(PROJECT_ROOT, "evaluation", "feed_27_card_paste.txt")

# The five measured errors this run is checking, keyed by the baseline card
# numbers (1-based, as shown in the UI) the user reported them against.
TRACKED = [
    {
        "name": "E1 subsumed partial filed as its own fact",
        "cards": [9],
        "baseline": "card 09 'EpistemicOS evaluates manuscript claims' -> BP.1.1.2",
        "want": "dropped as SUBSUMED by card 10",
    },
    {
        "name": "E2 same fact, two different nodes",
        "cards": [3, 13],
        "baseline": "card 03 -> BP.9.2.2 and card 13 -> BP.9.2.10",
        "want": "one card, one node (duplicate collapsed)",
    },
    {
        "name": "E3 pricing list scattered",
        "cards": [2, 3, 4],
        "baseline": "BP.9.2.10 / BP.9.2.2 / BP.9.2.10",
        "want": "all three on one node",
    },
    {
        "name": "E4 cost list scattered",
        "cards": [23, 24, 25],
        "baseline": "BP.9.5.1 / BP.9.5.12 / BP.9.5.1",
        "want": "all three on one node",
    },
    {
        "name": "E5 merged contrast filed on the subordinate clause",
        "cards": [20],
        "baseline": "card 20 -> BP.1.2.1 (Writing Assistance Exclusion)",
        "want": "the problem-statement node, not the exclusion node",
    },
]


def load_fixture() -> str:
    """Read the reconstructed paste, without its trailing newline."""
    with open(FIXTURE, encoding="utf-8") as handle:
        return handle.read().rstrip("\n")


def verify_fixture(text: str, baseline_cards: list[dict]) -> int:
    """Check every baseline quote sits at its recorded offset in the fixture.

    Args:
        text: The reconstructed paste.
        baseline_cards: Cards from the baseline batch.

    Returns:
        The number of mismatched quotes; 0 means the reconstruction is exact.
    """
    bad = 0
    for card in sorted(baseline_cards, key=lambda c: c["start_char"]):
        found = text.find(card["source_quote"])
        if found != card["start_char"]:
            bad += 1
            print(
                "  card %02d MISMATCH recorded=%d found=%d %r"
                % (
                    card["index"] + 1,
                    card["start_char"],
                    found,
                    card["source_quote"][:48],
                )
            )
    print(
        "  fixture %d chars, %d/%d quotes at their recorded offset"
        % (len(text), len(baseline_cards) - bad, len(baseline_cards))
    )
    return bad


def _overlap(a: dict, b: dict) -> int:
    """Characters shared by two cards' source spans."""
    if None in (a.get("start_char"), a.get("end_char")):
        return 0
    if None in (b.get("start_char"), b.get("end_char")):
        return 0
    return max(
        0, min(a["end_char"], b["end_char"]) - max(a["start_char"], b["start_char"])
    )


def match_new(origin: dict, cards: list[dict]) -> Optional[dict]:
    """Find the card in the new run that carries a given baseline card's claim.

    Exact span first, then largest span overlap, then content similarity, and
    finally the dedupe record — a collapsed fact's claim lives on its survivor,
    which is reachable through ``merged_spans``. The fallbacks matter because
    neither the chunker nor the judge is deterministic: a re-run splits "the core
    problem is not X, it is Y" into one fact or two, so spans shift, and exact
    matching alone reports a card as vanished when it has only moved.

    Args:
        origin: A card from the baseline batch.
        cards: Cards from the new run.

    Returns:
        The corresponding new card, or None if nothing plausibly carries it.
    """
    for card in cards:
        if (
            card["start_char"] == origin["start_char"]
            and card["end_char"] == origin["end_char"]
        ):
            return card

    for card in cards:
        for span in card.get("merged_spans") or []:
            if span.get("start_char") == origin["start_char"]:
                return card

    best, best_overlap = None, 0
    for card in cards:
        shared = _overlap(origin, card)
        if shared > best_overlap:
            best, best_overlap = card, shared
    if best is not None:
        return best

    best, best_score = None, 0.0
    for card in cards:
        score = similarity(origin["fact"], card["fact"])
        if score > best_score:
            best, best_score = card, score
    return best if best_score >= 0.4 else None


def node_of(card: Optional[dict]) -> str:
    """Render a card's outcome: its node, or why it has none."""
    if card is None:
        return "(absent)"
    if card.get("status") == "subsumed":
        return "SUBSUMED"
    return card.get("proposed_node_id") or "(no match)"


def main() -> None:
    """Re-run the reference paste and print the before/after report."""
    baseline = feed_batch_store.get_batch(BASELINE_RUN)
    if not baseline:
        raise SystemExit(f"baseline batch {BASELINE_RUN} not found")
    baseline_cards = baseline["cards"]

    # Batches recorded after source_text was added carry their own input, so
    # there is nothing to reconstruct. The fixture path stays for this baseline,
    # which predates the field.
    if baseline.get("source_text"):
        text = baseline["source_text"]
        print(f"=== using source_text persisted on {BASELINE_RUN} ===")
        if "--verify-fixture" in sys.argv:
            return
    else:
        text = load_fixture()
        print(f"=== fixture check against {BASELINE_RUN} (no source_text) ===")
        if verify_fixture(text, baseline_cards):
            raise SystemExit("fixture does not match the recorded spans — aborting")
        if "--verify-fixture" in sys.argv:
            return

    control = "--control" in sys.argv
    label = "CONTROL (all three fixes off)" if control else "WITH FIXES"
    print(f"\n=== re-running the pipeline — {label} ===")
    batch = process_document(
        text,
        "27-card reference paste",
        run_id=None,
        collapse_duplicates=not control,
        group_facts=not control,
        sibling_context=not control,
    )
    after = batch.to_dict()
    cards = after["cards"]

    # Attribution runs baseline -> new, not new -> baseline. Going the other way
    # lets two new cards claim one baseline card and leaves others unattributed,
    # which reads as a card having vanished when it has not.
    by_baseline: dict[int, dict] = {}
    for origin in baseline_cards:
        match = match_new(origin, cards)
        if match is not None:
            by_baseline[origin["index"] + 1] = match

    print("\n=== the five tracked errors ===")
    for error in TRACKED:
        print(f"\n{error['name']}")
        print(f"  before: {error['baseline']}")
        print(f"  wanted: {error['want']}")
        for number in error["cards"]:
            card = by_baseline.get(number)
            origin = next(b for b in baseline_cards if b["index"] + 1 == number)
            note = ""
            if card is not None and card.get("subsumed_by"):
                note = f"  (subsumed by: {card['subsumed_by'][:56]!r})"
            elif card is not None and card["fact"] != origin["fact"]:
                note = f"  (now carried by: {card['fact'][:56]!r})"
            print(
                "  card %02d: %-12s -> %-12s%s"
                % (number, node_of(origin), node_of(card), note)
            )

    print("\n=== every card, before -> after ===")
    for number in sorted(b["index"] + 1 for b in baseline_cards):
        origin = next(b for b in baseline_cards if b["index"] + 1 == number)
        card = by_baseline.get(number)
        same = card is not None and node_of(card) == node_of(origin)
        group = f" [{card['group_id']}]" if card and card.get("group_id") else ""
        print(
            "  %02d  %-12s -> %-12s %s%s  %s"
            % (
                number,
                node_of(origin),
                node_of(card),
                "same" if same else "CHANGED",
                group,
                origin["fact"][:62],
            )
        )

    print("\n=== totals ===")
    for key in (
        "total_facts",
        "proposed",
        "no_proposal",
        "flagged_extraction",
        "flagged_degraded",
        "duplicates_collapsed",
        "subsumed",
        "groups",
        "classification_calls",
        "seconds",
    ):
        print("  %-22s %s" % (key, after.get(key)))
    print(
        "  %-22s %s"
        % (
            "baseline cards",
            "%d cards, %d flagged_degraded, 27 classification calls"
            % (len(baseline_cards), baseline.get("flagged_degraded", 0)),
        )
    )

    name = "feed_27_control.json" if control else "feed_27_rerun.json"
    out = os.path.join(PROJECT_ROOT, "evaluation", name)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(after, handle, indent=2)
    print(f"\nfull batch written to {out}")


if __name__ == "__main__":
    main()
