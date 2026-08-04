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

**Facts are stored verbatim.** A card's ``fact`` is an exact substring of the
source document, never a rewrite of it — see the semantic_chunker docstring for
why. This module owns the last two links in that chain: ``verify_card_spans``
asserts ``text[start:end] == fact`` on the objects that actually reach storage,
and ``confirm_card`` refuses to write a card whose span did not verify.

Because segments are verbatim, they are not self-contained: a card may read "It
replaced the per-seat model". ``_passage_for`` hands the classifier the
surrounding source so it can resolve that internally. The resolution decides the
node and never enters the stored text.

**The two-signal contract holds, with signal 1 redefined.** ``needs_review`` and
``degraded_target`` stay separate fields with separate reasons. They route
nothing — everything goes to the human anyway — so they tell the reviewer *what
to check*:

    needs_review=T  ->  check the FACT   (the span did not verify)
    degraded=T      ->  check the NODE   (Alex must author the missing field)
    both            ->  two separate problems, both shown

Signal 1 used to mean "the LLM audit judged this rewrite unfaithful". With
nothing rewritten there is no rewrite to be unfaithful, so it now means "this
text could not be shown to be the author's", which is decided by string
comparison rather than by a model.

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
from services.fact_dedupe import dedupe
from services.feed_classifier_v3 import (
    NO_PROPOSAL,
    PROPOSED,
    SUBSUMED,
    Architecture,
    Proposal,
    load_architecture,
    propose,
    propose_group,
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
STATUS_SUBSUMED = "subsumed"

# What the reviewer has to check on this card. Independent of each other and of
# whether a shortlist exists.
CHECK_FACT = "check_extraction"
CHECK_NODE = "check_node_degraded"
CHECK_NO_MATCH = "no_candidate_matched"

# Characters of source either side of a segment handed to the classifier as the
# passage. Facts are stored verbatim, so the referent of "It" or the frame a
# fragment depends on lives in the source, not in the fact. 600 covers the
# paragraph a segment was cut from in the reference documents without pushing the
# candidate node descriptions out of the prompt.
PASSAGE_WINDOW = 600

# How many facts either side of the target go into the classifier as context.
# One is enough for the two outcomes it enables: subsumption is between adjacent
# partials of one sentence, and primary-claim selection reads the target itself.
# Wider context measurably dilutes the prompt without adding a decision.
SIBLING_WINDOW = 1


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

    # True when source_text[start_char:end_char] == fact was checked and held.
    # Facts are stored verbatim, so this is the claim that the stored text is the
    # author's text. confirm_card refuses to write a card without it.
    span_verified: bool = False

    # --- argument structure (chunker) ---
    # What this fact does in its passage, and which other facts of the same
    # passage it relates to (by `index`). Metadata for retrieval: it never
    # affects which node this card is filed at, and every linked fact is a
    # separate card filed separately.
    role: Optional[str] = None
    relationships: dict[str, list[int]] = field(default_factory=dict)

    # --- collapsed and grouped facts ---
    merged_spans: list[dict[str, Any]] = field(default_factory=list)
    group_id: Optional[str] = None
    group_label: Optional[str] = None
    subsumed_by: Optional[str] = None

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
    # Set only when USE_CASCADE is on: which rung the fact committed at and the
    # judge ratings it was decided from. Both travel to the stored record so an
    # audit can ask which rung a given fact landed on.
    cascade_level: Optional[str] = None
    cascade_ratings: list[str] = field(default_factory=list)

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
    """The result of processing one document: a stack of review cards.

    ``source_text`` carries the extracted text the cards came from. Without it a
    run is not reproducible: cards record only each fact's verbatim quote and its
    character span, and reconstructing an input from 27 spans — which is what
    re-running the reference paste actually required — recovers the separator
    characters between spans by inference, not by reading them. It costs little:
    a document's text is a fraction of the JSON of the cards derived from it
    (~400KB of cards for a 170-fact document), so it does not move the batch
    meaningfully against feed_batch_store.MAX_PAYLOAD_BYTES.
    """

    run_id: str
    source_document: str
    total_facts: int
    cards: list[ReviewCard]
    proposed: int
    no_proposal: int
    flagged_extraction: int
    flagged_degraded: int
    seconds: float
    source_text: str = ""
    duplicates_collapsed: int = 0
    subsumed: int = 0
    groups: int = 0
    classification_calls: int = 0

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
    card.cascade_level = proposal.cascade_level
    card.cascade_ratings = list(proposal.cascade_ratings)
    card.shortlist = [asdict(c) for c in proposal.candidates]
    card.candidate_sections = [asdict(s) for s in proposal.sections]
    card.section_margin = proposal.section_margin
    card.proposal_reason = proposal.reason

    top = proposal.candidates[0] if proposal.candidates else None
    card.degraded_target = bool(top and top.degraded)
    card.degraded_reason = top.degraded_reason if top else None

    # A subsumed fact is not a filing decision the reviewer has to make: its
    # content is already on another card. It carries no checks for that reason —
    # adding one would put it back in the queue it was removed from.
    if proposal.decision == SUBSUMED:
        card.status = STATUS_SUBSUMED
        card.subsumed_by = proposal.subsumed_by
        card.checks = []
        return card

    checks: list[str] = []
    if card.needs_review:
        checks.append(CHECK_FACT)
    if card.degraded_target:
        checks.append(CHECK_NODE)
    if proposal.decision == NO_PROPOSAL or card.cascade_level == "review":
        if CHECK_NO_MATCH not in checks:
            checks.append(CHECK_NO_MATCH)
    card.checks = checks
    return card


def verify_card_spans(cards: list[ReviewCard], text: str) -> list[ReviewCard]:
    """Set ``span_verified`` on every card, and return the ones that failed.

    The single assertion the verbatim design rests on::

        text[card.start_char:card.end_char] == card.fact

    Checked here rather than trusted from the chunker because this is the last
    point that holds both the cards and the text they came from, and because the
    property has to be true of the object that reaches storage, not of an earlier
    one. A card that fails is kept and shown — the reviewer should see that the
    extraction broke — but ``confirm_card`` will not write it.

    Args:
        cards: The cards for one document.
        text: The source text they were cut from.

    Returns:
        The cards whose text is not an exact substring. Empty is expected.
    """
    failed: list[ReviewCard] = []
    for card in cards:
        if card.start_char is None or card.end_char is None:
            card.span_verified = False
            failed.append(card)
            continue
        card.span_verified = text[card.start_char : card.end_char] == card.fact
        if not card.span_verified:
            failed.append(card)
            logger.error(
                "[FeedPipeline] card %d is not verbatim: source[%d:%d]=%r fact=%r",
                card.index,
                card.start_char,
                card.end_char,
                text[card.start_char : card.end_char][:100],
                card.fact[:100],
            )
    return failed


def _redirect_collapsed_links(facts: list[Any], drops: list[dict[str, Any]]) -> None:
    """Point relationships at the survivor after dedupe collapsed their target.

    dedupe keeps each fact's index as the chunker assigned it and does not
    renumber, so a link into a collapsed fact would name an index no card
    carries. The claim itself survives on the fact it merged into, so the link
    follows it there; a target that resolves to nothing live is dropped rather
    than left dangling.

    Args:
        facts: The surviving facts.
        drops: dedupe's drop records, each with ``dropped_index``/``kept_index``.
    """
    if not drops:
        return
    redirect = {d["dropped_index"]: d["kept_index"] for d in drops}
    live = {f.index for f in facts}

    for fact in facts:
        if not fact.relationships:
            continue
        rewired: dict[str, list[int]] = {}
        for relation, targets in fact.relationships.items():
            picked: list[int] = []
            for target in targets:
                mapped = redirect.get(target, target)
                if mapped != fact.index and mapped in live and mapped not in picked:
                    picked.append(mapped)
            if picked:
                rewired[relation] = picked
        fact.relationships = rewired


def _passage_for(
    cards: list[ReviewCard], text: str, window: int = PASSAGE_WINDOW
) -> Optional[str]:
    """The stretch of source around one or more segments, for comprehension.

    Facts are stored verbatim, so a card's own text may be "It replaced the
    per-seat model" with the referent one sentence earlier. This is what the
    classifier reads to resolve that. It is never stored and never rewritten into
    the fact.

    The window is trimmed to whitespace so it does not begin or end mid-word,
    and a document shorter than the window is returned whole.

    Args:
        cards: The card(s) being classified. Spans are unioned.
        text: The full source document.
        window: Characters of context either side.

    Returns:
        The passage, or None when no card carries a span to centre it on.
    """
    spans = [
        (c.start_char, c.end_char)
        for c in cards
        if c.start_char is not None and c.end_char is not None
    ]
    if not spans or not text:
        return None
    if len(text) <= window:
        return text

    start = max(0, min(s for s, _ in spans) - window)
    end = min(len(text), max(e for _, e in spans) + window)

    # Advance/retreat only when the clip landed INSIDE a word. Trimming a cut
    # that already sits on a boundary would eat the first or last token, which
    # at window=0 is the segment itself.
    if start > 0 and not text[start - 1].isspace():
        space = text.find(" ", start)
        if 0 <= space < end:
            start = space + 1
    if end < len(text) and not text[end].isspace():
        space = text.rfind(" ", start, end)
        if space > start:
            end = space
    return text[start:end].strip() or None


def _propose_one(
    card: ReviewCard,
    arch: Architecture,
    siblings: Optional[list[str]] = None,
    passage: Optional[str] = None,
    **kwargs: Any,
) -> ReviewCard:
    """Build the shortlist for a single card. Runs on a worker thread."""
    return _apply_proposal(
        card, propose(card.fact, arch, siblings=siblings, passage=passage, **kwargs)
    )


def _propose_group(
    cards: list[ReviewCard], arch: Architecture, **kwargs: Any
) -> list[ReviewCard]:
    """Classify one group with a single call and apply the result to every member.

    Runs on a worker thread. The proposal describes the list, so every member
    gets the same shortlist; the per-card fields that are not about the target
    node (fidelity verdict, provenance) are untouched.

    Args:
        cards: The group's cards, in source order. Must be non-empty.
        arch: A loaded Architecture.
        **kwargs: Passed through to propose_group.

    Returns:
        The same cards, each carrying the group's proposal.
    """
    proposal = propose_group(
        [c.fact for c in cards], arch, group_id=cards[0].group_id, **kwargs
    )
    for card in cards:
        _apply_proposal(card, proposal)
    return cards


def _partition(
    cards: list[ReviewCard],
) -> tuple[list[list[ReviewCard]], list[ReviewCard]]:
    """Split cards into groups to classify together and singletons.

    Group membership comes from the chunker. A group of one is not a group and
    falls through to the singleton path, where it still gets sibling context.

    Args:
        cards: All cards for the document, in source order.

    Returns:
        (groups, singletons) — groups in first-appearance order, each with its
        members in source order.
    """
    order: list[str] = []
    grouped: dict[str, list[ReviewCard]] = {}
    for card in cards:
        if not card.group_id:
            continue
        if card.group_id not in grouped:
            grouped[card.group_id] = []
            order.append(card.group_id)
        grouped[card.group_id].append(card)

    groups = [grouped[g] for g in order if len(grouped[g]) > 1]
    kept = {id(c) for members in groups for c in members}
    singletons = [c for c in cards if id(c) not in kept]
    return groups, singletons


def _siblings_of(
    card: ReviewCard, cards: list[ReviewCard], window: int = SIBLING_WINDOW
) -> list[str]:
    """The facts adjacent to a card, as classification context.

    Adjacency is by position in the document plus span overlap: a fact whose
    quote covers this card's quote is a neighbour however far apart their indices
    ended up, and that is exactly the shape a subsuming fact has.

    Args:
        card: The card being classified.
        cards: All cards for the document, in source order.
        window: How many positions either side to include.

    Returns:
        Neighbour fact texts in source order, excluding the card itself.
    """
    position = cards.index(card)
    picked: list[ReviewCard] = []
    for other in cards[max(0, position - window) : position + window + 1]:
        if other is not card:
            picked.append(other)

    if card.start_char is not None and card.end_char is not None:
        for other in cards:
            if other is card or other in picked:
                continue
            if other.start_char is None or other.end_char is None:
                continue
            if other.start_char <= card.start_char and other.end_char >= card.end_char:
                picked.append(other)

    return [c.fact for c in sorted(picked, key=lambda c: c.index)]


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
    if card.status == STATUS_SUBSUMED:
        return "subsumed"
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
        "group_id": card.group_id,
        "group_label": card.group_label,
        "subsumed_by": card.subsumed_by,
        "merged_count": len(card.merged_spans or []),
        "role": card.role,
        "relationships": card.relationships,
    }


def process_document(
    text: str,
    source_document: str,
    arch: Optional[Architecture] = None,
    supabase_client: Any = None,
    run_id: Optional[str] = None,
    max_workers: int = MAX_WORKERS,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    collapse_duplicates: bool = True,
    group_facts: bool = True,
    sibling_context: bool = True,
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
        collapse_duplicates: Collapse near-identical facts before classifying.
        group_facts: Classify a list the chunker grouped with one call and one
            node, instead of once per member.
        sibling_context: Show the classifier each fact's neighbours, enabling the
            SUBSUMED outcome and primary-claim selection.
        **proposer_kwargs: Passed through to propose (sections_to_consider etc.).

    The three flags above exist to be turned OFF for measurement — an ablation
    run on a fixed input isolates what a fix changed from what the LLM would have
    changed anyway between two runs. Production leaves them on; there is no
    caller that should be setting them.

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

    extracted = chunk_text(text)
    if collapse_duplicates:
        facts, duplicate_drops = dedupe(extracted)
        _redirect_collapsed_links(facts, duplicate_drops)
    else:
        facts, duplicate_drops = list(extracted), []
    if not group_facts:
        for fact in facts:
            fact.group_id = None
    emit(
        "extracted",
        words=len(text.split()),
        chars=len(text),
        facts=len(facts),
        flagged=sum(1 for f in facts if f.needs_review),
        duplicates_collapsed=len(duplicate_drops),
        groups=len({f.group_id for f in facts if f.group_id}),
    )
    logger.info(
        "[FeedPipeline] %s: %d facts extracted, %d collapsed as duplicates, "
        "%d flagged by the fidelity audit, %d group(s)",
        source_document,
        len(extracted),
        len(duplicate_drops),
        sum(1 for f in facts if f.needs_review),
        len({f.group_id for f in facts if f.group_id}),
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
            merged_spans=list(f.merged_spans),
            group_id=f.group_id,
            group_label=f.group_label,
            role=f.role,
            relationships=dict(f.relationships),
        )
        for f in facts
    ]

    unverified = verify_card_spans(cards, text)
    if unverified:
        logger.error(
            "[FeedPipeline] %s: %d card(s) are NOT exact substrings of the "
            "source and will be refused at confirm: %s",
            source_document,
            len(unverified),
            [c.index for c in unverified],
        )

    for card in cards:
        emit(
            "chunked",
            index=card.index,
            fact=card.fact,
            span_verified=card.span_verified,
            source_document=card.source_document,
            source_quote=card.source_quote,
            start_char=card.start_char,
            end_char=card.end_char,
            needs_review=card.needs_review,
            verdict=card.verdict,
            role=card.role,
            relationships=card.relationships,
            total=len(cards),
        )

    groups: list[list[ReviewCard]] = []
    singletons: list[ReviewCard] = []
    if cards:
        groups, singletons = _partition(cards)
        logger.info(
            "[FeedPipeline] %s: %d classification call(s) for %d cards "
            "(%d group(s), %d singletons)",
            source_document,
            len(groups) + len(singletons),
            len(cards),
            len(groups),
            len(singletons),
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Any, list[ReviewCard]] = {}
            for members in groups:
                job = pool.submit(
                    _propose_group,
                    members,
                    arch,
                    passage=_passage_for(members, text),
                    **proposer_kwargs,
                )
                futures[job] = members
            for card in singletons:
                futures[
                    pool.submit(
                        _propose_one,
                        card,
                        arch,
                        siblings=_siblings_of(card, cards) if sibling_context else None,
                        passage=_passage_for([card], text),
                        **proposer_kwargs,
                    )
                ] = [card]

            for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
                targets = futures[future]
                try:
                    future.result(timeout=CALL_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 — logged, cards preserved
                    logger.error(
                        "[FeedPipeline] proposal failed for fact(s) %s: %s",
                        [t.index for t in targets],
                        str(exc)[:200],
                    )
                    for target in targets:
                        target.checks = [CHECK_NO_MATCH]
                        target.proposal_reason = f"proposer raised: {str(exc)[:160]}"
                for target in targets:
                    emit("classified", **_card_event(target))

    live = [c for c in cards if c.status != STATUS_SUBSUMED]
    batch = FeedBatch(
        run_id=run_id,
        source_document=source_document,
        total_facts=len(cards),
        cards=cards,
        # Subsumed cards are excluded from both proposal counts: they are not
        # awaiting a filing decision, so counting them as "no proposal" would
        # inflate the review queue with cards the reviewer never sees.
        proposed=sum(1 for c in live if c.proposed_node_id),
        no_proposal=sum(1 for c in live if not c.proposed_node_id),
        flagged_extraction=sum(1 for c in live if c.needs_review),
        flagged_degraded=sum(1 for c in live if c.degraded_target),
        seconds=round(time.perf_counter() - started, 2),
        source_text=text,
        duplicates_collapsed=len(duplicate_drops),
        subsumed=sum(1 for c in cards if c.status == STATUS_SUBSUMED),
        groups=len({c.group_id for c in cards if c.group_id}),
        classification_calls=len(groups) + len(singletons),
    )
    emit(
        "done",
        total=batch.total_facts,
        proposed=batch.proposed,
        no_proposal=batch.no_proposal,
        flagged_extraction=batch.flagged_extraction,
        flagged_degraded=batch.flagged_degraded,
        duplicates_collapsed=batch.duplicates_collapsed,
        subsumed=batch.subsumed,
        groups=batch.groups,
        classification_calls=batch.classification_calls,
        seconds=batch.seconds,
    )
    logger.info(
        "[FeedPipeline] %s: %d cards in %.1fs — %d with a shortlist, %d without, "
        "%d collapsed as duplicates, %d subsumed, %d classification call(s) "
        "(run_id=%s). Nothing stored; awaiting confirmation.",
        source_document,
        batch.total_facts,
        batch.seconds,
        batch.proposed,
        batch.no_proposal,
        batch.duplicates_collapsed,
        batch.subsumed,
        batch.classification_calls,
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

    Raises:
        ValueError: The card was marked subsumed. Its content is already on
            another card, so filing it would write the same claim twice.
        ValueError: The card's span was not verified, i.e. its text was not shown
            to be an exact substring of the source. Storing it would put words in
            Alex's mouth, which is the one thing this pipeline may not do.
    """
    from datetime import datetime, timezone

    if card.status == STATUS_SUBSUMED:
        raise ValueError(
            f"fact {card.index} is subsumed by another fact and must not be filed"
        )

    if not card.span_verified:
        raise ValueError(
            f"fact {card.index} is not a verified verbatim span of its source "
            f"(start={card.start_char}, end={card.end_char}) and must not be "
            f"stored: {card.fact[:80]!r}"
        )

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
            # The content is a verbatim substring of the source document at this
            # span. Anything reading this row can reproduce it from the source.
            "verbatim": True,
            "span_verified": card.span_verified,
            # Every other place in the document this same claim was made, from
            # the pre-classification dedupe. Provenance for a collapsed fact
            # must survive the collapse.
            "merged_spans": card.merged_spans,
            "group_id": card.group_id,
            "group_label": card.group_label,
            # argument structure, from the chunker. `relationships` holds the
            # fact indices of this run, so it is only meaningful alongside
            # `fact_index` and `source_document`.
            "role": card.role,
            "relationships": card.relationships,
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
            # which rung the cascade committed at, and the judge ratings behind
            # it. Both are None/[] when USE_CASCADE was off for this run.
            "cascade_level": card.cascade_level,
            "cascade_ratings": card.cascade_ratings,
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
