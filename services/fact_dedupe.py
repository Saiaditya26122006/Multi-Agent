"""Collapse near-duplicate facts before classification.

Sits between the chunker and the classifier. A long paste often states the same
claim twice in different words — "The faculty pricing tier is twenty thousand"
in a pricing list, then "The faculty tier is twenty thousand" three paragraphs
later. Classified separately those two land on different nodes, because the judge
sees two independent facts and has no way to know they are one claim. The
divergence is then indistinguishable from a real disagreement in the source.

Two rules make this safe to run before an LLM ever sees the facts:

1. **Quantities gate the merge, similarity only proposes it.** Two facts merge
   only when their quantity signature — every number, currency and unit word —
   is identical. Wording may differ; measurements may not. This is what stops
   "eight thousand" collapsing into "twenty thousand" and euros into dollars,
   and it holds regardless of how the similarity threshold is tuned.
2. **Nothing is discarded.** The survivor is the fuller phrasing, and it carries
   the dropped fact's quote and span in ``merged_spans``, so provenance still
   points at every place in the document the claim was made.

Subsumption is deliberately NOT handled here. "EpistemicOS evaluates manuscript
claims" and "EpistemicOS evaluates manuscript claims before external review" are
one claim and a strictly weaker partial of it, not two phrasings of one claim —
collapsing them on token overlap would silently drop the qualifier. That case is
the classifier's, which sees siblings and can say SUBSUMED.

Standalone: no LLM call, no datastore, no architecture.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

# Jaccard overlap on content tokens above which two facts are considered the
# same claim. Calibrated on the 27-fact reference paste, across two independent
# chunker runs that worded the same claims differently: the one true duplicate
# pair measures 1.00 in both, and the highest-scoring non-duplicate measures 0.67
# ("the company charges twelve thousand" vs "the twelve thousand charge is not
# validated" — one claim and a claim about its evidence, correctly kept apart).
# 0.75 sits inside that gap. See scripts/calibrate_fact_dedupe.py.
DEFAULT_THRESHOLD = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9%]+")

# Dropped before comparison: function words carry no claim content, and leaving
# them in inflates the overlap of any two English sentences.
_STOPWORDS = frozenset(
    """
    a an the this that these those it its they them their we our us i my me you your
    is are was were be been being am do does did has have had will would can could
    should may might must of for to in on at by with from as and or but not no than
    then there here which who whom whose what when where how all any both each
    """.split()
)

# Generic heads: they name the KIND of thing being quantified, not the claim.
# Dropped because the chunker picks a different one on every run for the same
# claim — the reference paste's "the faculty pricing tier is twenty thousand"
# came back as "the faculty subscription price is twenty thousand" on a re-run,
# and "the faculty tier is twenty thousand" elsewhere. All three assert one
# thing. Keeping these tokens made the similarity a measure of which synonym the
# model happened to choose (0.80 against one phrasing, 0.50 against another),
# which is not a property of the facts. Removing them leaves the subject and the
# quantity, which is what actually identifies the claim.
_GENERIC_HEADS = frozenset(
    """
    tier tiers price prices pricing cost costs charge charges fee fees rate rates
    figure figures amount amounts subscription subscriptions licence license
    level levels band bands point points value values
    """.split()
)

# Any difference in this set blocks a merge outright. Number words, currency,
# units of measure and time units — everything that makes a claim a measurement.
_QUANTITY_WORDS = frozenset(
    """
    zero one two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
    sixty seventy eighty ninety hundred thousand million billion
    half quarter third double triple dozen
    percent pct euro euros eur dollar dollars usd pound pounds gbp
    year years month months week weeks day days quarter quarters hour hours
    """.split()
)


@dataclass
class MergedSpan:
    """Provenance of a fact that was collapsed into another."""

    fact: str
    source_quote: str
    start_char: Optional[int]
    end_char: Optional[int]
    index: int


def content_tokens(text: str) -> set[str]:
    """Return the comparable content tokens of a fact.

    Lowercased, split on non-alphanumerics, with function words and generic
    quantity heads removed. What survives is the subject and the measurement —
    the part of a claim that is stable across rephrasings.

    Args:
        text: The fact text.

    Returns:
        The set of content tokens.
    """
    return {
        t
        for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and t not in _GENERIC_HEADS
    }


def quantity_signature(text: str) -> Counter:
    """Return the multiset of quantity-bearing tokens in a fact.

    Anything containing a digit, plus number, currency, unit and time words. Two
    facts with different signatures are never merged, whatever their similarity.

    Args:
        text: The fact text.

    Returns:
        A Counter over quantity tokens.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    return Counter(
        t for t in tokens if any(c.isdigit() for c in t) or t in _QUANTITY_WORDS
    )


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of two facts' content tokens, in [0.0, 1.0].

    Args:
        left: One fact text.
        right: The other fact text.

    Returns:
        Intersection over union of content tokens; 0.0 if either side is empty.
    """
    a, b = content_tokens(left), content_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate_pair(
    left: str, right: str, threshold: float = DEFAULT_THRESHOLD
) -> tuple[bool, float, str]:
    """Decide whether two facts are the same claim.

    Args:
        left: One fact text.
        right: The other fact text.
        threshold: Minimum content-token overlap.

    Returns:
        (is_duplicate, similarity, reason) — reason explains a refusal.
    """
    score = similarity(left, right)
    if score < threshold:
        return False, score, "similarity below threshold"
    if quantity_signature(left) != quantity_signature(right):
        return False, score, "quantity signatures differ"
    return True, score, "same claim"


def _as_span(fact: Any) -> MergedSpan:
    """Capture a fact's provenance before it is dropped."""
    return MergedSpan(
        fact=fact.fact,
        source_quote=fact.source_quote,
        start_char=fact.start_char,
        end_char=fact.end_char,
        index=fact.index,
    )


def dedupe(
    facts: Sequence[Any], threshold: float = DEFAULT_THRESHOLD
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Collapse near-identical facts, keeping the fuller phrasing of each claim.

    Mutates nothing on the dropped facts and does not renumber the survivors —
    ``index`` stays as the chunker assigned it, so a card's index still points at
    the fact the fidelity audit reported on. Renumbering happens downstream if at
    all.

    Args:
        facts: Facts from the chunker, in source order. Each needs ``fact``,
            ``source_quote``, ``start_char``, ``end_char``, ``index`` and a
            writable ``merged_spans`` attribute.
        threshold: Minimum content-token overlap to merge.

    Returns:
        (survivors, drops) — survivors in source order, and one record per
        dropped fact describing what was merged into what and why.
    """
    survivors: list[Any] = []
    drops: list[dict[str, Any]] = []

    for candidate in facts:
        match = None
        match_score = 0.0
        for kept in survivors:
            duplicate, score, _ = is_duplicate_pair(
                kept.fact, candidate.fact, threshold
            )
            if duplicate and score > match_score:
                match, match_score = kept, score

        if match is None:
            survivors.append(candidate)
            continue

        # The fuller phrasing survives. Longer text is the proxy: between two
        # phrasings of one claim, the longer one carries the qualifier the
        # shorter one dropped ("faculty PRICING tier" over "faculty tier").
        loser = candidate
        if len(candidate.fact) > len(match.fact):
            survivors[survivors.index(match)] = candidate
            candidate.merged_spans = list(match.merged_spans)
            loser = match
            winner = candidate
        else:
            winner = match

        winner.merged_spans.append(_as_span(loser).__dict__)

        # Group membership survives the collapse. A list member and a later
        # ungrouped restatement of it are one claim, and the restatement is
        # usually the longer phrasing — so the survivor is the ungrouped one and
        # the list silently loses a member, which then files alone. That is what
        # scattered the pricing list across BP.9.5.1 and BP.9.2.2 after facts
        # became verbatim: the group is a property of the CLAIM, not of the
        # wording that survived.
        if getattr(winner, "group_id", None) is None:
            winner.group_id = getattr(loser, "group_id", None)
            winner.group_label = getattr(loser, "group_label", None)
            if winner.group_id:
                logger.info(
                    "[Dedupe] fact %d inherited group %s from the fact it "
                    "replaced (%d)",
                    winner.index,
                    winner.group_id,
                    loser.index,
                )

        drops.append(
            {
                "dropped_index": loser.index,
                "dropped_fact": loser.fact,
                "kept_index": winner.index,
                "kept_fact": winner.fact,
                "similarity": round(match_score, 4),
            }
        )
        logger.info(
            "[Dedupe] fact %d collapsed into fact %d (similarity %.2f): %r",
            loser.index,
            winner.index,
            match_score,
            loser.fact[:70],
        )

    if drops:
        logger.info(
            "[Dedupe] %d fact(s) -> %d after collapsing %d duplicate(s)",
            len(facts),
            len(survivors),
            len(drops),
        )
    return survivors, drops


def pairwise_report(
    facts: Iterable[Any], floor: float = 0.5
) -> list[tuple[float, bool, str, str, str]]:
    """Every fact pair scoring at or above ``floor``, for threshold calibration.

    Args:
        facts: Facts to compare.
        floor: Minimum similarity to report.

    Returns:
        (similarity, would_merge, reason, left_fact, right_fact) tuples, highest
        similarity first.
    """
    items = list(facts)
    rows = []
    for i, left in enumerate(items):
        for right in items[i + 1 :]:
            duplicate, score, reason = is_duplicate_pair(left.fact, right.fact)
            if score >= floor:
                rows.append((round(score, 4), duplicate, reason, left.fact, right.fact))
    return sorted(rows, key=lambda r: -r[0])
