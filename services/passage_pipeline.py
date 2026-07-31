"""Passage pipeline — document in, verbatim passages attached to nodes out.

    passage_chunker.split_passages  ->  passage_classifier.attach  ->  PassageCard
                                                                          |
                                                     human confirms attachments
                                                                          v
                                                              rag_service.store
                                                          (one row per attachment)

The fact pipeline (``services/feed_pipeline.py``) is still here and still works;
this is the alternative unit of ingest, selected with ``unit=`` on
``services/feed_entry.process``. Both write through a human confirmation.

**Two things differ from the fact pipeline, and they are the point.**

1. **Nothing is split into claims and nothing is rewritten.** A passage is a
   paragraph or labelled block, sliced from the source. ``verify_passage_spans``
   asserts ``source[start:end] == text`` on the cards that reach storage, and
   ``confirm_passage`` refuses to write one that did not verify.
2. **A passage attaches to several nodes.** Each attachment carries the span of
   the passage that earned it, so "which part of this paragraph belongs here" has
   an answer at every node it lands on.

**Storage is one row per attachment, each holding the WHOLE passage.** Retrieval
filters on ``section``, so a row is what makes a passage visible at a node; and
the content is the entire paragraph rather than the span, because the reason for
storing passages is that the surrounding sentences are what make the claim
readable. The span travels in metadata and is what ``highlight()`` marks.

``rag_service.store`` deduplicates on a global content hash that ignores
``section``, so the second attachment of one passage would be swallowed as a
duplicate of the first. The natural key for an attachment is (passage, node), so
this module dedupes on that pair itself and passes ``deduplicate=False``.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from services import rag_service
from services.feed_classifier_v3 import Architecture, load_architecture
from services.passage_chunker import Passage, split_passages
from services.passage_classifier import (
    ATTACHED,
    MAX_ATTACHMENTS,
    NO_ATTACHMENT,
    Attachment,
    PassageProposal,
    attach,
    highlight,
)

logger = logging.getLogger(__name__)

MAX_WORKERS = 8
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 1800

SOURCE_TYPE = "ceo_doc"

STATUS_AWAITING = "awaiting_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

# What the reviewer has to check on this card.
CHECK_SPAN = "check_span_not_verbatim"
CHECK_NODE = "check_node_degraded"
CHECK_NO_MATCH = "no_node_matched"
CHECK_OVERFLOW = "more_nodes_qualified_than_cap"


@dataclass
class PassageCard:
    """One verbatim passage awaiting confirmation of where it attaches."""

    text: str
    index: int
    status: str = STATUS_AWAITING

    # --- provenance ---
    source_document: str = ""
    start_char: int = 0
    end_char: int = 0
    label: Optional[str] = None
    span_verified: bool = False

    # --- the proposal ---
    attachments: list[dict[str, Any]] = field(default_factory=list)
    candidate_sections: list[dict[str, Any]] = field(default_factory=list)
    # Every node the judge was shown. Recorded because "the right node was never
    # a candidate" and "the judge rejected the right node" are different failures
    # and are indistinguishable without it.
    considered_node_ids: list[str] = field(default_factory=list)
    summary: str = ""
    overflow: int = 0
    dropped: list[dict[str, str]] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    # --- the human's answer ---
    confirmed_node_ids: list[str] = field(default_factory=list)
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    stored_ids: list[str] = field(default_factory=list)

    @property
    def node_ids(self) -> list[str]:
        """Proposed nodes, best first."""
        return [a["node_id"] for a in self.attachments]

    def to_dict(self) -> dict[str, Any]:
        """Return the card as a plain dict."""
        return asdict(self)


@dataclass
class PassageBatch:
    """The result of processing one document into passages."""

    run_id: str
    source_document: str
    total_passages: int
    cards: list[PassageCard]
    attached: int
    unattached: int
    total_attachments: int
    seconds: float
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the batch as a plain dict."""
        payload = asdict(self)
        payload["cards"] = [c.to_dict() for c in self.cards]
        return payload


def verify_passage_spans(cards: list[PassageCard], text: str) -> list[PassageCard]:
    """Set ``span_verified`` on every card, and return the ones that failed.

    The assertion the design rests on, checked on the objects that reach storage
    rather than on an earlier copy of them::

        text[card.start_char:card.end_char] == card.text

    A failing card is kept and shown — the reviewer should see that ingest broke —
    but ``confirm_passage`` will not write it.

    Args:
        cards: The cards for one document.
        text: The source document.

    Returns:
        The cards whose text is not an exact substring. Empty is expected.
    """
    failed: list[PassageCard] = []
    for card in cards:
        card.span_verified = text[card.start_char : card.end_char] == card.text
        if not card.span_verified:
            failed.append(card)
            logger.error(
                "[PassagePipeline] card %d is not verbatim: source[%d:%d]=%r text=%r",
                card.index,
                card.start_char,
                card.end_char,
                text[card.start_char : card.end_char][:100],
                card.text[:100],
            )
    return failed


def _apply(card: PassageCard, proposal: PassageProposal) -> PassageCard:
    """Attach a proposal to a card and record what the reviewer must check."""
    card.attachments = [a.to_dict() for a in proposal.attachments]
    card.candidate_sections = [asdict(s) for s in proposal.sections]
    card.considered_node_ids = list(proposal.considered_node_ids)
    card.summary = proposal.summary
    card.overflow = proposal.overflow
    card.dropped = list(proposal.dropped)

    checks: list[str] = []
    if not card.span_verified:
        checks.append(CHECK_SPAN)
    if proposal.decision == NO_ATTACHMENT:
        checks.append(CHECK_NO_MATCH)
    if any(a.degraded for a in proposal.attachments):
        checks.append(CHECK_NODE)
    if proposal.overflow:
        checks.append(CHECK_OVERFLOW)
    card.checks = checks
    return card


def _attach_one(card: PassageCard, arch: Architecture, **kwargs: Any) -> PassageCard:
    """Propose attachments for one card. Runs on a worker thread."""
    return _apply(card, attach(card.text, arch, **kwargs))


def bucket_of(card: PassageCard) -> str:
    """Which review bucket a card falls in. The single definition of the split."""
    if card.status == STATUS_CONFIRMED:
        return "confirmed"
    if card.status == STATUS_REJECTED or not card.attachments:
        return "nofit"
    return "look" if card.checks else "ready"


def process_document(
    text: str,
    source_document: str,
    arch: Optional[Architecture] = None,
    supabase_client: Any = None,
    run_id: Optional[str] = None,
    max_workers: int = MAX_WORKERS,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    max_attachments: int = MAX_ATTACHMENTS,
    **classifier_kwargs: Any,
) -> PassageBatch:
    """Split a document into passages and propose attachments for each.

    Writes nothing. Passages reach knowledge_base only through
    ``confirm_passage()``.

    Args:
        text: The raw document text.
        source_document: Filename or identifier, stored as provenance.
        arch: A pre-loaded Architecture. Loaded here if omitted (~7.5s).
        supabase_client: Only needed when `arch` is not supplied.
        run_id: Batch id. Generated if omitted.
        max_workers: Parallel attachment workers.
        on_event: Called as each stage completes, for live progress. Runs on the
            worker thread that finished the work, so it must be thread-safe.
            Exceptions from it are logged and swallowed.
        max_attachments: Ceiling on attachments per passage.
        **classifier_kwargs: Passed through to ``attach``.

    Returns:
        A PassageBatch of cards, every one awaiting confirmation.
    """
    started = time.perf_counter()
    run_id = run_id or f"passage-{uuid.uuid4().hex[:12]}"

    def emit(event: str, **payload: Any) -> None:
        if on_event is None:
            return
        try:
            on_event({"event": event, "run_id": run_id, **payload})
        except Exception as exc:  # noqa: BLE001 — a viewer must not break ingest
            logger.warning("[PassagePipeline] event sink failed: %s", str(exc)[:120])

    emit("started", source_document=source_document, chars=len(text))

    if arch is None:
        arch = load_architecture(supabase_client)

    passages: list[Passage] = split_passages(text)
    cards = [
        PassageCard(
            text=p.text,
            index=p.index,
            source_document=source_document,
            start_char=p.start_char,
            end_char=p.end_char,
            label=p.label,
        )
        for p in passages
    ]

    unverified = verify_passage_spans(cards, text)
    if unverified:
        logger.error(
            "[PassagePipeline] %s: %d passage(s) are NOT exact substrings and "
            "will be refused at confirm: %s",
            source_document,
            len(unverified),
            [c.index for c in unverified],
        )

    emit(
        "split",
        passages=len(cards),
        labelled=sum(1 for c in cards if c.label),
        unverified=len(unverified),
    )
    for card in cards:
        emit(
            "passage",
            index=card.index,
            text=card.text,
            label=card.label,
            start_char=card.start_char,
            end_char=card.end_char,
            span_verified=card.span_verified,
            total=len(cards),
        )

    if cards:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _attach_one,
                    card,
                    arch,
                    max_attachments=max_attachments,
                    **classifier_kwargs,
                ): card
                for card in cards
            }
            for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
                card = futures[future]
                try:
                    future.result(timeout=CALL_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 — logged, card preserved
                    logger.error(
                        "[PassagePipeline] attachment failed for passage %d: %s",
                        card.index,
                        str(exc)[:200],
                    )
                    card.checks = [CHECK_NO_MATCH]
                    card.summary = f"classifier raised: {str(exc)[:160]}"
                emit("attached", **_card_event(card))

    batch = PassageBatch(
        run_id=run_id,
        source_document=source_document,
        total_passages=len(cards),
        cards=cards,
        attached=sum(1 for c in cards if c.attachments),
        unattached=sum(1 for c in cards if not c.attachments),
        total_attachments=sum(len(c.attachments) for c in cards),
        seconds=round(time.perf_counter() - started, 2),
        source_text=text,
    )
    emit(
        "done",
        total=batch.total_passages,
        attached=batch.attached,
        unattached=batch.unattached,
        total_attachments=batch.total_attachments,
        seconds=batch.seconds,
    )
    logger.info(
        "[PassagePipeline] %s: %d passage(s) in %.1fs — %d attached to %d node(s) "
        "total, %d unattached (run_id=%s). Nothing stored; awaiting confirmation.",
        source_document,
        batch.total_passages,
        batch.seconds,
        batch.attached,
        batch.total_attachments,
        batch.unattached,
        run_id,
    )
    return batch


def _card_event(card: PassageCard) -> dict[str, Any]:
    """The payload the live view needs to draw one classified passage."""
    return {
        "index": card.index,
        "text": card.text,
        "label": card.label,
        "bucket": bucket_of(card),
        "summary": card.summary,
        "attachments": card.attachments,
        "node_ids": card.node_ids,
        "checks": card.checks,
        "overflow": card.overflow,
        "span_verified": card.span_verified,
        "source_document": card.source_document,
        "start_char": card.start_char,
        "end_char": card.end_char,
    }


def _already_attached(node_id: str, passage_hash: str) -> Optional[str]:
    """The id of an existing row for this (passage, node) pair, or None.

    ``rag_service.store`` dedupes on content hash alone, which would treat the
    second attachment of one passage as a duplicate of the first and silently
    drop it. The natural key here is the pair, so the check lives here and
    ``store`` is called with ``deduplicate=False``.
    """
    try:
        client = rag_service._get_supabase()  # noqa: SLF001 — same package
        existing = (
            client.table(rag_service.TABLE_NAME)
            .select("id")
            .eq("section", node_id)
            .eq("metadata->>passage_hash", passage_hash)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — reported, then treated as absent
        logger.error(
            "[PassagePipeline] duplicate check failed for %s: %s", node_id, exc
        )
        return None
    return existing.data[0]["id"] if existing.data else None


def confirm_passage(
    card: PassageCard,
    confirmed_node_ids: list[str],
    confirmed_by: str,
    run_id: str,
    session_id: Optional[str] = None,
) -> PassageCard:
    """Store the passage at every node a human confirmed. The only write path.

    One row per node, each holding the WHOLE passage. The justifying span travels
    in metadata rather than as the content: retrieval at a node should return the
    author's paragraph, with the earning span marked, not a fragment of it.

    Args:
        card: The reviewed card.
        confirmed_node_ids: The nodes the human confirmed. May be a subset of the
            proposal, or nodes they browsed to themselves.
        confirmed_by: Who confirmed it.
        run_id: The batch this card came from.
        session_id: Session to attribute the passage to.

    Returns:
        The card, updated with the stored ids and confirmation metadata.

    Raises:
        ValueError: The card's span was not verified, or no nodes were given.
    """
    from datetime import datetime, timezone

    if not card.span_verified:
        raise ValueError(
            f"passage {card.index} is not a verified verbatim span of its source "
            f"(start={card.start_char}, end={card.end_char}) and must not be "
            f"stored: {card.text[:80]!r}"
        )
    if not confirmed_node_ids:
        raise ValueError(f"passage {card.index} was confirmed with no nodes")

    passage_hash = rag_service.content_hash(card.text)
    by_node = {a["node_id"]: a for a in card.attachments}
    stored: list[str] = []

    for node_id in confirmed_node_ids:
        existing = _already_attached(node_id, passage_hash)
        if existing:
            logger.info(
                "[PassagePipeline] passage %d already attached at %s (%s)",
                card.index,
                node_id,
                existing,
            )
            stored.append(existing)
            continue

        proposed = by_node.get(node_id, {})
        result = rag_service.store(
            content=card.text,
            source_type=SOURCE_TYPE,
            section=node_id,
            # Left NULL deliberately, as in the fact pipeline: extraction
            # fidelity is not a claim about the truth status of the content.
            epistemic_status=None,
            session_id=session_id,
            run_id=run_id,
            agent_name="passage_pipeline",
            # The pair (passage, node) is the key, so store's global content-hash
            # dedupe would be wrong here — it would drop every attachment after
            # the first. _already_attached does the right check above.
            deduplicate=False,
            metadata={
                # provenance — the passage is reproducible from the source
                "source_document": card.source_document,
                "start_char": card.start_char,
                "end_char": card.end_char,
                "passage_index": card.index,
                "passage_label": card.label,
                "passage_hash": passage_hash,
                "verbatim": True,
                "span_verified": card.span_verified,
                # what earned this node the attachment
                "attachment_span": proposed.get("span"),
                "attachment_span_start": proposed.get("span_start"),
                "attachment_span_end": proposed.get("span_end"),
                "attachment_reason": proposed.get("reason"),
                "attachment_rank": proposed.get("rank"),
                "attached_by_proposal": node_id in by_node,
                # the whole set, so any row can find its siblings
                "attached_nodes": list(confirmed_node_ids),
                "passage_summary": card.summary,
                "degraded_target": proposed.get("degraded"),
                "degraded_reason": proposed.get("degraded_reason"),
                "status": STATUS_CONFIRMED,
                "confirmed_by": confirmed_by,
                "pipeline": "passage_v1_multi_attach",
            },
        )
        if result:
            stored.append(result.id)
        else:
            logger.error(
                "[PassagePipeline] passage %d at %s was not stored: %s",
                card.index,
                node_id,
                result.outcome,
            )

    card.status = STATUS_CONFIRMED
    card.confirmed_node_ids = list(confirmed_node_ids)
    card.confirmed_by = confirmed_by
    card.confirmed_at = datetime.now(timezone.utc).isoformat()
    card.stored_ids = stored

    logger.info(
        "[PassagePipeline] confirmed passage %d -> %s by %s (%d row(s) written)",
        card.index,
        ", ".join(confirmed_node_ids),
        confirmed_by,
        len(stored),
    )
    return card


def retrieve_at_node(
    node_id: str, limit: int = 20, marker: str = "**"
) -> list[dict[str, Any]]:
    """Every passage attached to a node, whole, with its justifying span marked.

    This is the read side of multi-node attachment: the same passage surfaces at
    each node it was attached to, and at each one the marked span is the part
    that earned *that* node.

    Args:
        node_id: The node to read.
        limit: Maximum passages to return.
        marker: Wrapper placed either side of the justifying span.

    Returns:
        One dict per stored passage: the raw text, the highlighted text, the span
        and reason that earned this node, and the provenance to find it in the
        source document.
    """
    client = rag_service._get_supabase()  # noqa: SLF001 — same package
    rows = (
        client.table(rag_service.TABLE_NAME)
        .select("id,content,metadata,created_at")
        .eq("section", node_id)
        .limit(limit)
        .execute()
    )

    out: list[dict[str, Any]] = []
    for row in rows.data or []:
        meta = row.get("metadata") or {}
        text = row.get("content") or ""
        start = meta.get("attachment_span_start")
        end = meta.get("attachment_span_end")
        marked = text
        span_ok = isinstance(start, int) and isinstance(end, int)
        if span_ok and 0 <= start < end <= len(text):
            marked = f"{text[:start]}{marker}{text[start:end]}{marker}{text[end:]}"
        out.append(
            {
                "id": row.get("id"),
                "passage": text,
                "highlighted": marked,
                "span": meta.get("attachment_span"),
                "reason": meta.get("attachment_reason"),
                "source_document": meta.get("source_document"),
                "start_char": meta.get("start_char"),
                "end_char": meta.get("end_char"),
                "also_attached_to": [
                    n for n in (meta.get("attached_nodes") or []) if n != node_id
                ],
            }
        )
    return out


__all__ = [
    "Attachment",
    "PassageBatch",
    "PassageCard",
    "bucket_of",
    "confirm_passage",
    "highlight",
    "process_document",
    "retrieve_at_node",
    "verify_passage_spans",
]
