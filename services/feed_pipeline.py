"""Feed pipeline — raw document in, review cards out.

    semantic_chunker  ->  feed_classifier_v3.propose  ->  ReviewCard
                                                              |
                                          human confirms a node
                                                              v
                                                   rag_service.store

**Nothing auto-files.** Seven retrieval mechanisms were measured against the
labelled key; the best leaf recall@10 is 57.4%, so committing to a node is wrong
for roughly half of Alex's facts. What retrieval *can* do is find the right
neighbourhood — the correct section is in the top 5 about 70% of the time — so
every fact becomes a card carrying a ranked shortlist, and a human picks.

That inverts where the value sits. Each confirmation writes a
``(fact, proposed_node, confirmed_node, rank_of_confirmed)`` pair: the labelled
real-language ground truth this project has never had, and the only honest basis
for deciding whether auto-file is ever viable.

**The two-signal contract is unchanged; it now applies to every card.**
``needs_review`` (extraction fidelity, from the chunker) and ``degraded_target``
(node completeness, from the architecture) stay separate fields with separate
reasons. They no longer route anything — everything goes to the human anyway —
so they are pure annotations telling the reviewer *what to check*:

    needs_review=T  ->  check the FACT   (re-read the source quote)
    degraded=T      ->  check the NODE   (Alex must author the missing field)
    both            ->  two separate problems, both shown

Facts are proposed in parallel; measured 7.2x at 8 workers with no throttling.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from services import rag_service
from services.feed_classifier_v3 import (
    NO_PROPOSAL,
    PROPOSED,
    Architecture,
    Proposal,
    load_architecture,
    propose,
)
from services.semantic_chunker import chunk_text

logger = logging.getLogger(__name__)

MAX_WORKERS = 8
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 1800

SOURCE_TYPE = "ceo_doc"

STATUS_AWAITING = "awaiting_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

# What the reviewer has to check on this card. Independent of each other and of
# whether a shortlist exists.
CHECK_FACT = "check_extraction"
CHECK_NODE = "check_node_degraded"
CHECK_NO_MATCH = "no_candidate_matched"


@dataclass
class ReviewCard:
    """One fact awaiting human confirmation, with everything needed to decide."""

    fact: str
    index: int
    status: str = STATUS_AWAITING

    # --- provenance: where this came from ---
    source_document: str = ""
    source_quote: str = ""
    start_char: Optional[int] = None
    end_char: Optional[int] = None

    # --- signal 1: extraction fidelity (chunker) ---
    needs_review: bool = False
    verdict: Optional[str] = None
    review_reason: Optional[str] = None

    # --- signal 2: target node completeness (architecture) ---
    degraded_target: bool = False
    degraded_reason: Optional[str] = None

    # --- the proposal ---
    proposed_node_id: Optional[str] = None
    shortlist: list[dict[str, Any]] = field(default_factory=list)
    candidate_sections: list[dict[str, Any]] = field(default_factory=list)
    section_margin: Optional[float] = None
    proposal_reason: str = ""
    checks: list[str] = field(default_factory=list)

    # --- the human's answer ---
    confirmed_node_id: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    rank_of_confirmed: Optional[int] = None
    stored_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Return the card as a plain dict."""
        return asdict(self)


@dataclass
class FeedBatch:
    """The result of processing one document: a stack of review cards."""

    run_id: str
    source_document: str
    total_facts: int
    cards: list[ReviewCard]
    proposed: int
    no_proposal: int
    flagged_extraction: int
    flagged_degraded: int
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        """Return the batch as a plain dict."""
        payload = asdict(self)
        payload["cards"] = [c.to_dict() for c in self.cards]
        return payload


def _apply_proposal(card: ReviewCard, proposal: Proposal) -> ReviewCard:
    """Attach a proposal to a card and record what the reviewer must check.

    The two signals are recorded independently and neither suppresses the other.
    A distorted extraction pointing at a degraded node produces both checks,
    because they are two different repairs by two different people.
    """
    card.proposed_node_id = proposal.proposed_node_id
    card.shortlist = [asdict(c) for c in proposal.candidates]
    card.candidate_sections = [asdict(s) for s in proposal.sections]
    card.section_margin = proposal.section_margin
    card.proposal_reason = proposal.reason

    top = proposal.candidates[0] if proposal.candidates else None
    card.degraded_target = bool(top and top.degraded)
    card.degraded_reason = top.degraded_reason if top else None

    checks: list[str] = []
    if card.needs_review:
        checks.append(CHECK_FACT)
    if card.degraded_target:
        checks.append(CHECK_NODE)
    if proposal.decision == NO_PROPOSAL:
        checks.append(CHECK_NO_MATCH)
    card.checks = checks
    return card


def _propose_one(card: ReviewCard, arch: Architecture, **kwargs: Any) -> ReviewCard:
    """Build the shortlist for a single card. Runs on a worker thread."""
    return _apply_proposal(card, propose(card.fact, arch, **kwargs))


def bucket_of(card: ReviewCard) -> str:
    """Which review bucket a card falls in.

    The single definition of the split. Both the results view and the review
    queue read from this via the emitted events, so the counts on one screen
    always equal the queue length on the other.

    There is deliberately no "auto-filed" bucket: nothing is written without a
    human confirming it, so `filed` is a state a card reaches later, not one it
    can be born in.
    """
    if card.status == STATUS_CONFIRMED:
        return "confirmed"
    if card.status == STATUS_REJECTED or not card.shortlist:
        return "nofit"
    return "look" if card.checks else "ready"


def _card_event(card: ReviewCard) -> dict[str, Any]:
    """The payload the live view needs to draw one classified card."""
    top = (card.shortlist or [{}])[0]
    return {
        "index": card.index,
        "fact": card.fact,
        "bucket": bucket_of(card),
        "node_id": card.proposed_node_id,
        "node_title": top.get("title"),
        "why": top.get("note") or card.proposal_reason,
        "checks": card.checks,
        "verdict": card.verdict,
        "degraded_reason": card.degraded_reason,
        "shortlist_size": len(card.shortlist or []),
        "source_document": card.source_document,
        "start_char": card.start_char,
        "end_char": card.end_char,
    }


def process_document(
    text: str,
    source_document: str,
    arch: Optional[Architecture] = None,
    supabase_client: Any = None,
    run_id: Optional[str] = None,
    max_workers: int = MAX_WORKERS,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    **proposer_kwargs: Any,
) -> FeedBatch:
    """Chunk a document and build a review card for every fact.

    Writes nothing. Facts reach knowledge_base only through confirm_card().

    Args:
        text: The raw document text.
        source_document: Filename or identifier, stored as provenance.
        arch: A pre-loaded Architecture. Loaded here if omitted (~7.5s).
        supabase_client: Only needed when `arch` is not supplied.
        run_id: Batch id. Generated if omitted.
        max_workers: Parallel proposal workers. 8 measured clean.
        on_event: Called as each stage completes, for live progress. Runs on
            whichever worker thread finished the work, so the callback must be
            thread-safe — the server hands it a `run_coroutine_threadsafe` shim.
            Exceptions from it are logged and swallowed: a broken viewer must
            never take down an ingest.
        **proposer_kwargs: Passed through to propose (sections_to_consider etc.).

    Returns:
        A FeedBatch of cards, every one awaiting confirmation.
    """
    started = time.perf_counter()
    run_id = run_id or f"feed-{uuid.uuid4().hex[:12]}"

    def emit(event: str, **payload: Any) -> None:
        if on_event is None:
            return
        try:
            on_event({"event": event, "run_id": run_id, **payload})
        except Exception as exc:  # noqa: BLE001 — a viewer must not break ingest
            logger.warning("[FeedPipeline] event sink failed: %s", str(exc)[:120])

    emit("started", source_document=source_document, chars=len(text))

    if arch is None:
        arch = load_architecture(supabase_client)

    facts = chunk_text(text)
    emit(
        "extracted",
        words=len(text.split()),
        chars=len(text),
        facts=len(facts),
        flagged=sum(1 for f in facts if f.needs_review),
    )
    logger.info(
        "[FeedPipeline] %s: %d facts extracted, %d flagged by the fidelity audit",
        source_document,
        len(facts),
        sum(1 for f in facts if f.needs_review),
    )

    cards = [
        ReviewCard(
            fact=f.fact,
            index=f.index,
            source_document=source_document,
            source_quote=f.source_quote,
            start_char=f.start_char,
            end_char=f.end_char,
            needs_review=f.needs_review,
            verdict=f.verdict,
            review_reason=f.review_reason,
        )
        for f in facts
    ]

    for card in cards:
        emit(
            "chunked",
            index=card.index,
            fact=card.fact,
            source_document=card.source_document,
            source_quote=card.source_quote,
            start_char=card.start_char,
            end_char=card.end_char,
            needs_review=card.needs_review,
            verdict=card.verdict,
            total=len(cards),
        )

    if cards:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_propose_one, c, arch, **proposer_kwargs): c for c in cards
            }
            for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
                target = futures[future]
                try:
                    future.result(timeout=CALL_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 — logged, card preserved
                    logger.error(
                        "[FeedPipeline] proposal failed for fact %d: %s",
                        target.index,
                        str(exc)[:200],
                    )
                    target.checks = [CHECK_NO_MATCH]
                    target.proposal_reason = f"proposer raised: {str(exc)[:160]}"
                emit("classified", **_card_event(target))

    batch = FeedBatch(
        run_id=run_id,
        source_document=source_document,
        total_facts=len(cards),
        cards=cards,
        proposed=sum(1 for c in cards if c.proposed_node_id),
        no_proposal=sum(1 for c in cards if not c.proposed_node_id),
        flagged_extraction=sum(1 for c in cards if c.needs_review),
        flagged_degraded=sum(1 for c in cards if c.degraded_target),
        seconds=round(time.perf_counter() - started, 2),
    )
    emit(
        "done",
        total=batch.total_facts,
        proposed=batch.proposed,
        no_proposal=batch.no_proposal,
        flagged_extraction=batch.flagged_extraction,
        flagged_degraded=batch.flagged_degraded,
        seconds=batch.seconds,
    )
    logger.info(
        "[FeedPipeline] %s: %d cards in %.1fs — %d with a shortlist, %d without "
        "(run_id=%s). Nothing stored; awaiting confirmation.",
        source_document,
        batch.total_facts,
        batch.seconds,
        batch.proposed,
        batch.no_proposal,
        run_id,
    )
    return batch


def confirm_card(
    card: ReviewCard,
    confirmed_node_id: str,
    confirmed_by: str,
    run_id: str,
    session_id: Optional[str] = None,
) -> ReviewCard:
    """Store a fact at the node a human chose. The only write path in Feed.

    `section` is written here and nowhere else. A proposal is not a filing: if
    the proposed node were written to `section` at ingestion, Build's
    section-filtered retrieval would read unconfirmed guesses as though they had
    been placed deliberately.

    Args:
        card: The reviewed card.
        confirmed_node_id: The node the human chose — the proposal, a different
            shortlist entry, or anything else they browsed to.
        confirmed_by: Who confirmed it.
        run_id: The batch this card came from.
        session_id: Session to attribute the fact to.

    Returns:
        The card, updated with the stored id and confirmation metadata.
    """
    from datetime import datetime, timezone

    rank = next(
        (
            entry["rank"]
            for entry in card.shortlist
            if entry.get("node_id") == confirmed_node_id
        ),
        None,
    )

    result = rag_service.store(
        content=card.fact,
        source_type=SOURCE_TYPE,
        section=confirmed_node_id,
        # Left NULL deliberately. `needs_review` is a claim about extraction
        # fidelity, not about the truth status of the content — a faithfully
        # extracted fact can still be an assumption, and several of Alex's say
        # so in their own text. Epistemic classification is a separate job.
        epistemic_status=None,
        session_id=session_id,
        run_id=run_id,
        agent_name="feed_pipeline",
        metadata={
            # provenance
            "source_document": card.source_document,
            "source_quote": card.source_quote,
            "start_char": card.start_char,
            "end_char": card.end_char,
            "fact_index": card.index,
            # signal 1 — extraction fidelity
            "needs_review": card.needs_review,
            "verdict": card.verdict,
            "review_reason": card.review_reason,
            # signal 2 — target node completeness
            "degraded_target": card.degraded_target,
            "degraded_reason": card.degraded_reason,
            # the human's decision, and what the machine had suggested
            "status": STATUS_CONFIRMED,
            "confirmed_node_id": confirmed_node_id,
            "confirmed_by": confirmed_by,
            "proposed_node_id": card.proposed_node_id,
            "rank_of_confirmed": rank,
            "accepted_proposal": confirmed_node_id == card.proposed_node_id,
            "shortlist": [e.get("node_id") for e in card.shortlist],
            "pipeline": "feed_v4_review_assist",
        },
    )

    card.status = STATUS_CONFIRMED
    card.confirmed_node_id = confirmed_node_id
    card.confirmed_by = confirmed_by
    card.confirmed_at = datetime.now(timezone.utc).isoformat()
    card.rank_of_confirmed = rank
    card.stored_id = result.id

    logger.info(
        "[FeedPipeline] confirmed fact %d -> %s by %s (proposed %s, rank %s, id=%s)",
        card.index,
        confirmed_node_id,
        confirmed_by,
        card.proposed_node_id,
        rank,
        result.id,
    )
    return card
