"""Passage chunker — divide a document at natural boundaries. No LLM, no rewriting.

Front of the passage pipeline. Where ``semantic_chunker`` cuts a document into
atomic claims, this cuts it only where the author already put a boundary: a blank
line, a heading, or a labelled block such as ``Claim 1 — ...``. The passage is
the unit that gets stored and the unit that gets attached to nodes.

**There is no model in this module.** Splitting on structure is a string
operation, and doing it deterministically is what makes the guarantee free::

    source[p.start_char:p.end_char] == p.text

Every passage is a slice of the source. Nothing is generated, so nothing can be
paraphrased, and the audit is an equality check rather than a belief. The same
input always produces the same passages — unlike the LLM splitter, which varies
run to run.

Splitting shallow is deliberate. An atomic-claim splitter has to decide what a
claim is, and every such decision is a chance to drop a qualifier or separate a
claim from its scope. A paragraph needs no such decision: the author already made
it. What the old design bought with that risk — one fact, one node — is replaced
by attaching one passage to several nodes; see ``services/passage_classifier.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# A line that opens a labelled block. Alex writes "Claim 1 — ...", and a label
# like that starts a new passage even without a blank line before it, because the
# label is the author saying "new unit here". Markdown headings and numbered or
# bulleted list openers count for the same reason.
_LABEL_PATTERNS = (
    # Claim 1 —  /  Assumption 2:  /  Risk 3 -  /  Finding:  /  Decision 4 –
    r"(?:claim|assumption|risk|finding|decision|note|section|hypothesis|"
    r"observation|evidence|exhibit|question|action|constraint|principle)"
    r"\s*\d*\s*[—–\-:.)]",
    r"#{1,6}\s+\S",           # markdown heading
    r"\d+[.)]\s+\S",          # 1. or 1)
    r"[-*•]\s+\S",            # bullet
)
_LABEL_RE = re.compile(
    r"^\s*(?:" + "|".join(_LABEL_PATTERNS) + ")", re.IGNORECASE
)

# Passages shorter than this are folded into the previous one rather than stored
# alone. A stray "Notes:" line is a label, not a passage, and storing it as its
# own unit gives the classifier nothing to work with.
MIN_PASSAGE_CHARS = 40


@dataclass
class Passage:
    """One verbatim passage of a source document.

    Attributes:
        text: The passage, character-for-character as the author wrote it. It is
            sliced from the source, never generated, so it may contain pronouns
            with no local referent — that is the point, and the classifier is
            given the whole passage to resolve them.
        start_char: Offset of the passage in the source.
        end_char: End offset (exclusive).
        index: Position in the document, 0-based.
        label: The block label that opened this passage ("Claim 1"), or None.
        span_verified: Set by ``audit_passages``. True when
            ``source[start_char:end_char] == text`` was checked and held.
    """

    text: str
    start_char: int
    end_char: int
    index: int
    label: Optional[str] = None
    span_verified: bool = False
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the passage as a plain dict."""
        return asdict(self)


def _label_of(block: str) -> Optional[str]:
    """The label opening a block, trimmed to something short and readable."""
    first = block.lstrip().split("\n", 1)[0].strip()
    if not _LABEL_RE.match(first):
        return None
    for separator in ("—", "–", ":", " - "):
        if separator in first:
            head = first.split(separator, 1)[0].strip()
            if head:
                return head[:60]
    return first[:60]


def _boundaries(text: str) -> list[int]:
    """Offsets where a new passage starts, always including 0.

    Two kinds of boundary, both structural: a blank line, and a line that opens a
    labelled block. Nothing here inspects meaning.
    """
    cuts = {0}

    for match in re.finditer(r"\n[ \t]*\n", text):
        cuts.add(match.end())

    for match in re.finditer(r"^.*$", text, re.MULTILINE):
        line = match.group()
        if line.strip() and _LABEL_RE.match(line):
            cuts.add(match.start())

    return sorted(c for c in cuts if c < len(text))


def split_passages(text: str) -> list[Passage]:
    """Divide a document into verbatim passages at its natural boundaries.

    Args:
        text: The raw document.

    Returns:
        Passages in source order, each an exact slice of ``text``. Empty list for
        empty input. Whitespace-only blocks are dropped; a block shorter than
        MIN_PASSAGE_CHARS is folded into the one before it.
    """
    if not text or not text.strip():
        logger.info("[Passages] Empty input, returning no passages")
        return []

    cuts = _boundaries(text)
    spans: list[tuple[int, int]] = []
    for i, start in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        # Trim surrounding whitespace off the span itself, so the stored text
        # never carries the separator characters between passages.
        block = text[start:end]
        lead = len(block) - len(block.lstrip())
        trail = len(block) - len(block.rstrip())
        start, end = start + lead, end - trail
        if start >= end:
            continue
        if spans and (end - start) < MIN_PASSAGE_CHARS:
            spans[-1] = (spans[-1][0], end)
            continue
        spans.append((start, end))

    passages = [
        Passage(
            text=text[s:e],
            start_char=s,
            end_char=e,
            index=i,
            label=_label_of(text[s:e]),
        )
        for i, (s, e) in enumerate(spans)
    ]

    audit_passages(passages, text)
    logger.info(
        "[Passages] %d chars -> %d passage(s), %d labelled",
        len(text),
        len(passages),
        sum(1 for p in passages if p.label),
    )
    return passages


def audit_passages(passages: list[Passage], source: str) -> list[Passage]:
    """Assert every passage is an exact substring of its source, in place.

    The guarantee the design rests on::

        source[p.start_char:p.end_char] == p.text

    ``split_passages`` slices rather than generates, so a failure here means a bug
    in this module. It is checked anyway — an unchecked invariant is a belief, and
    this one is the reason the module exists.

    Args:
        passages: The passages to audit.
        source: The document they were cut from.

    Returns:
        The passages that failed. Empty is the expected result.
    """
    failures: list[Passage] = []
    for p in passages:
        p.span_verified = source[p.start_char : p.end_char] == p.text
        if not p.span_verified:
            failures.append(p)
            logger.error(
                "[Passages] passage %d is not verbatim: source[%d:%d]=%r text=%r",
                p.index,
                p.start_char,
                p.end_char,
                source[p.start_char : p.end_char][:100],
                p.text[:100],
            )
    if failures:
        logger.error(
            "[Passages] %d of %d passages failed the verbatim audit",
            len(failures),
            len(passages),
        )
    return failures
