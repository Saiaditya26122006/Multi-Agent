"""Passage classifier — attach one verbatim passage to every node it populates.

The fact pipeline asked "which single node does this claim belong to". A real
paragraph does not have one answer. Alex's Claim-1 block states a differentiation
claim, decides a positioning stance, and reports a benchmark result; filing it at
one node loses two of the three, and splitting it into three claims to avoid that
is what forced the rewriting this design removed.

So the passage is stored whole and attached to several nodes:

    passage -> rank sections -> judge over every candidate leaf
            -> ALL nodes whose required_output this passage populates,
               each with the SPAN of the passage that earns it

**An attachment must be earned by a span.** The judge returns the substring of
the passage that satisfies the node's required_output, and this module checks
that the span really is a substring before keeping the attachment. That check is
the whole difference between multi-node attachment and topic spraying: a node
that is merely *related* has no span to point at, so it cannot produce one, so it
does not get attached. `MAX_ATTACHMENTS` caps what survives; the rest are dropped
with `overflow` set so the reviewer knows the passage was crowded.

Two rules carried forward unchanged from feed_classifier_v3:

* **Retrieval sees degraded nodes; the gate decides.** Degraded leaves stay in the
  candidate set and are shown to the judge, flagged rather than hidden.
* **A note that rejects its own node cannot rank it.** `_note_rejects` and the
  `fit` verdict are reused directly rather than reimplemented, so the guard that
  fixed the confident-wrong-filing bug applies here too.

Standalone: reads bp_architecture, calls Bedrock, writes nothing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from services.feed_classifier_v3 import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_ENV,
    FIT_OK,
    FIT_POOR,
    Architecture,
    SectionCandidate,
    _call_llm,
    _describe,
    _note_rejects,
    _parse_json_object,
    parent_of,
    rank_sections,
)

logger = logging.getLogger(__name__)

# Ceiling on attachments per passage. Four is the point past which a "passage
# about everything" is more likely than a passage that genuinely populates five
# required_outputs — beyond it, the reviewer is being asked to check a spray.
MAX_ATTACHMENTS = 4

# Sections whose leaves the judge sees. Higher than the fact path's 3: a passage
# legitimately spans several areas of the plan, and the whole point here is to
# find all of them, so the candidate pool has to cover more of the tree.
DEFAULT_SECTIONS = 6

ATTACHED = "attached"
NO_ATTACHMENT = "no_attachment"

# Routing levels, per attachment.
LEVEL_LEAF = "leaf"
LEVEL_SECTION = "section"


ATTACH_PROMPT = """You attach ONE passage of a business document to the nodes of a \
business-plan architecture that it populates.

The passage is stored VERBATIM — exactly as the author wrote it, and it is not \
rewritten, ever. Your job is not to file it in one place. A real paragraph often \
states a claim, decides something, AND reports a result; each of those may \
populate a different node, and every one of them should be attached.

Each node carries:
  purpose               what the node is for
  required_output       what must eventually be written there
  evidence_requirement  what kind of evidence that output needs
  prohibited_claims     inferences that must NOT be made at this node

=== WHAT EARNS AN ATTACHMENT ===

A node earns an attachment when the passage contains content that node's
REQUIRED_OUTPUT specifically asks for. Not related content. Not same-topic
content. Content that node is waiting to be given.

The test is mechanical, and you must apply it to every node you propose:

    Can you quote the EXACT span of the passage that populates this node's
    required_output?

If you can, quote it — that span is what earns the attachment. If you cannot
point at a span, the node does not qualify, however related it feels. A node that
is "about the same subject" and a node whose required_output this passage
populates are different things, and only the second one gets attached.

  passage    "We assess the argument, not the apparatus. The highest-output tool
              scored zero on reviewer-risk coverage."
  node       required_output: the differentiation claim
  span       "We assess the argument, not the apparatus"        -> ATTACH
  node       required_output: the core diagnostic capability map
  span       (none — the passage says what is assessed, not what the product's
              own capability map contains)                      -> NO ATTACHMENT

The same span may earn more than one node, and one passage may have several
different earning spans. Both are normal.

=== SPANS ===

"span" must be an EXACT substring of the passage, copied character for character.
It is checked with a string search and the attachment is DISCARDED if it does not
match. Do not paraphrase, do not tidy, do not join two separate stretches with
"...". Quote the shortest span that genuinely populates the node.

=== HOW MANY ===

Return every node that passes the span test, best first, at most 8. Returning one
is correct when only one qualifies. Returning none is correct and useful when the
passage populates nothing in this candidate set — say so rather than attaching
the least-bad node.

Do not pad the list to look thorough. Each extra attachment is a claim that the
passage populates that node, and a wrong one puts the author's words somewhere
they do not belong.

=== LEAVES AND PARENTS ===

Most candidates are leaves. Some are the PARENT section of a group of leaves,
marked as such. Attach to a parent ONLY when the passage populates the section
but no single leaf under it covers the content. Prefer a leaf whenever one fits.

=== THE NOTE DECIDES THE NODE, NEVER THE REVERSE ===

Within every attachment write "span" first, then "reason", then "fit", then
"node_id". That order is the rule, not formatting: you quote the evidence, say
what it means, judge it, and only then name the node. A node_id written first can
only be rationalised afterwards.

"fit" is a verdict on the reason you just wrote:
  "fits"  the span populates this node's required_output
  "poor"  it does not — related, wrong subject, or the span does not really
          answer what this node asks for

A "poor" attachment MUST NOT appear in the list at all. Delete it. Never return a
node annotated with the reason it is wrong.

=== OUTPUT ===

Return one JSON object, nothing else:

{"passage_summary": "<one sentence: what this passage does — the claims it makes,
                     the decisions it records, the results it reports>",
 "attachments": [{"span": "<exact substring of the passage>",
                  "reason": "<one line: what this span populates at this node>",
                  "fit": "<fits or poor>",
                  "node_id": "<id>"}, ...]}

Write "passage_summary" first and let the attachments follow from it."""


@dataclass
class Attachment:
    """One node this passage populates, and the span that earned it."""

    node_id: str
    title: Optional[str]
    level: str
    span: str
    span_start: int
    span_end: int
    reason: str
    rank: int
    degraded: bool = False
    degraded_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Return the attachment as a plain dict."""
        return asdict(self)


@dataclass
class PassageProposal:
    """Everything proposed for one passage. Files nothing; a human confirms."""

    passage: str
    decision: str
    attachments: list[Attachment]
    sections: list[SectionCandidate]
    considered_node_ids: list[str] = field(default_factory=list)
    summary: str = ""
    overflow: int = 0
    dropped: list[dict[str, str]] = field(default_factory=list)

    @property
    def node_ids(self) -> list[str]:
        """The nodes this passage attaches to, best first."""
        return [a.node_id for a in self.attachments]

    def to_dict(self) -> dict[str, Any]:
        """Return the proposal as a plain dict."""
        payload = asdict(self)
        payload["node_ids"] = self.node_ids
        return payload


def _candidate_ids(
    arch: Architecture, sections: Sequence[SectionCandidate]
) -> list[str]:
    """Every leaf in the chosen sections, plus each section node itself.

    The section is included so the judge has a legal target when a passage
    populates a section but no single leaf under it — the "no leaf, section
    matches" routing rule. It is rendered as a parent so the judge knows to prefer
    a leaf.
    """
    ids: list[str] = []
    for section in sections:
        for leaf in arch.siblings.get(section.section_id, []):
            if leaf not in ids:
                ids.append(leaf)
        if section.section_id in arch.nodes and section.section_id not in ids:
            ids.append(section.section_id)
    return ids


def _build_input(passage: str, candidate_ids: list[str], arch: Architecture) -> str:
    """Compose the judge's user message: the passage plus every candidate node."""
    blocks = []
    for node_id in candidate_ids:
        described = _describe(arch.nodes[node_id])
        if arch.siblings.get(node_id):
            described += "\nNOTE: this is a PARENT section — prefer a leaf under it."
        blocks.append(described)
    return (
        f'PASSAGE (stored verbatim, never rewritten):\n"""\n{passage}\n"""\n\n'
        f"CANDIDATE NODES ({len(candidate_ids)}):\n\n" + "\n---\n".join(blocks)
    )


def _locate_span(span: str, passage: str) -> tuple[int, int]:
    """Offsets of a span within its passage, or (-1, -1) when it is not there.

    Exact match first. A model that normalises whitespace while copying is the
    one deviation tolerated, and only because the span is then re-derived from
    the passage rather than kept as the model wrote it.
    """
    if not span:
        return -1, -1
    at = passage.find(span)
    if at != -1:
        return at, at + len(span)

    collapsed_span = " ".join(span.split())
    chars: list[str] = []
    offsets: list[int] = []
    previous_space = False
    for position, char in enumerate(passage):
        if char.isspace():
            if not previous_space and chars:
                chars.append(" ")
                offsets.append(position)
            previous_space = True
            continue
        chars.append(char)
        offsets.append(position)
        previous_space = False

    hit = "".join(chars).find(collapsed_span)
    if hit == -1:
        return -1, -1
    return offsets[hit], offsets[hit + len(collapsed_span) - 1] + 1


def _attachments_from(
    parsed: dict[str, Any],
    passage: str,
    arch: Architecture,
    max_attachments: int,
) -> tuple[list[Attachment], list[dict[str, str]], int]:
    """Turn a judge response into attachments, dropping everything unearned.

    Four ways an attachment is refused, each of them a way the old pipeline could
    have filed something wrong:

    * unknown node id — a hallucinated target
    * ``fit: poor``, or a reason that rejects its own node — the guard from
      feed_classifier_v3, reused rather than reimplemented
    * a span that is not in the passage — the node could not point at what earned
      it, which is exactly the "merely related" case this design excludes
    * a duplicate node — one attachment per node per passage

    Args:
        parsed: The parsed judge response.
        passage: The verbatim passage.
        arch: A loaded Architecture.
        max_attachments: Ceiling on kept attachments.

    Returns:
        (attachments, dropped, overflow) — dropped carries a reason per refusal
        so a thin result can be explained, and overflow counts qualifying
        attachments discarded by the cap.
    """
    kept: list[Attachment] = []
    dropped: list[dict[str, str]] = []
    seen: set[str] = set()
    qualified = 0

    for item in parsed.get("attachments", []):
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id", "")).strip()
        span = str(item.get("span", "")).strip()
        reason = str(item.get("reason", "")).strip()
        fit = str(item.get("fit", "")).strip().lower()

        node = arch.nodes.get(node_id)
        if node is None:
            dropped.append({"node_id": node_id[:60], "why": "unknown node id"})
            logger.warning("[Passages] dropped unknown node %r", node_id[:60])
            continue

        if node_id in seen:
            dropped.append({"node_id": node_id, "why": "duplicate attachment"})
            continue

        if fit and fit not in (FIT_OK, FIT_POOR):
            logger.warning(
                "[Passages] unrecognised fit %r on %s, judging by the reason",
                fit[:40],
                node_id,
            )
            fit = ""
        if fit == FIT_POOR or _note_rejects(reason):
            dropped.append(
                {"node_id": node_id, "why": f"reason rejects it: {reason[:80]}"}
            )
            logger.warning(
                "[Passages] dropped %s: reason rejects its own node | %r",
                node_id,
                reason[:120],
            )
            continue

        start, end = _locate_span(span, passage)
        if start < 0:
            dropped.append(
                {"node_id": node_id, "why": "span is not in the passage"}
            )
            logger.warning(
                "[Passages] dropped %s: span not found in passage | %r",
                node_id,
                span[:100],
            )
            continue

        qualified += 1
        seen.add(node_id)
        if len(kept) >= max_attachments:
            continue

        kept.append(
            Attachment(
                node_id=node_id,
                title=node.get("node_title"),
                level=LEVEL_SECTION if arch.siblings.get(node_id) else LEVEL_LEAF,
                span=passage[start:end],
                span_start=start,
                span_end=end,
                reason=reason,
                rank=len(kept) + 1,
                degraded=bool(node.get("degraded_target")),
                degraded_reason=node.get("degraded_reason"),
            )
        )

    return kept, dropped, max(0, qualified - len(kept))


def attach(
    passage: str,
    arch: Architecture,
    passage_vector: Optional[list[float]] = None,
    sections_to_consider: int = DEFAULT_SECTIONS,
    max_attachments: int = MAX_ATTACHMENTS,
    model_id: Optional[str] = None,
) -> PassageProposal:
    """Propose every node one passage populates. Files nothing, decides nothing.

    Args:
        passage: The verbatim passage text.
        arch: A loaded Architecture.
        passage_vector: Pre-computed embedding, to avoid re-embedding in batches.
        sections_to_consider: How many sections' leaves the judge sees.
        max_attachments: Ceiling on attachments kept.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.

    Returns:
        A PassageProposal. ``decision`` is ATTACHED when at least one node earned
        a span, NO_ATTACHMENT otherwise — which routes the passage to human
        review rather than to the least-bad node.
    """
    if passage_vector is None:
        from services.embedding_service import embed

        passage_vector = embed(passage, input_type="search_query")

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    sections = rank_sections(passage_vector, arch, top_n=max(sections_to_consider, 2))
    candidate_ids = _candidate_ids(arch, sections)

    if not candidate_ids:
        logger.warning("[Passages] no candidate nodes for passage %r", passage[:60])
        return PassageProposal(
            passage=passage,
            decision=NO_ATTACHMENT,
            attachments=[],
            sections=sections,
            summary="no candidate nodes were retrieved",
        )

    parsed = _parse_json_object(
        _call_llm(ATTACH_PROMPT, _build_input(passage, candidate_ids, arch), model)
    )
    attachments, dropped, overflow = _attachments_from(
        parsed, passage, arch, max_attachments
    )

    if overflow:
        logger.info(
            "[Passages] %d qualifying attachment(s) over the cap of %d, dropped",
            overflow,
            max_attachments,
        )

    logger.info(
        "[Passages] %r -> %d attachment(s): %s",
        passage[:60],
        len(attachments),
        ", ".join(a.node_id for a in attachments) or "none",
    )

    return PassageProposal(
        passage=passage,
        decision=ATTACHED if attachments else NO_ATTACHMENT,
        attachments=attachments,
        sections=sections,
        considered_node_ids=candidate_ids,
        summary=str(parsed.get("passage_summary", "")).strip(),
        overflow=overflow,
        dropped=dropped,
    )


def highlight(passage: str, attachment: Attachment, marker: str = "**") -> str:
    """The passage with the justifying span marked, for display at a node.

    Retrieval from an attached node returns the whole passage — the author's
    paragraph, not a fragment of it — and this is how the reader sees which part
    of it earned the node they were looking at.

    Args:
        passage: The full passage text.
        attachment: The attachment whose span to mark.
        marker: Wrapper placed either side of the span.

    Returns:
        The passage with the span wrapped. Returned unchanged when the span
        offsets do not fit the passage.
    """
    start, end = attachment.span_start, attachment.span_end
    if not (0 <= start < end <= len(passage)):
        logger.warning(
            "[Passages] cannot highlight %s: span [%d:%d] outside a %d-char passage",
            attachment.node_id,
            start,
            end,
            len(passage),
        )
        return passage
    return f"{passage[:start]}{marker}{passage[start:end]}{marker}{passage[end:]}"
