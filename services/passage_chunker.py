"""Passage chunker — one LLM call reads the document and says where the cuts go.

Front of the passage pipeline. Where ``semantic_chunker`` cuts a document into
atomic claims, this cuts it into units of MEANING: a claim block, a definition, a
procedure, a table. The passage is the unit that gets stored and the unit that
gets attached to nodes.

**The model chooses the boundaries; it never supplies the text.** It returns the
opening few words of each passage, this module locates them, and every passage is
then ``source[cut_i:cut_i+1]`` — a slice. So::

    source[p.start_char:p.end_char] == p.text

holds by construction rather than by trusting a copy, and ``audit_passages``
asserts it anyway. Boundaries are also **contiguous**: passage i ends where
passage i+1 begins, so no text can be dropped, duplicated or reordered whatever
the model returns.

Asking for openings rather than whole passages is what makes that possible. A
model asked to copy a nine-bullet protocol or a table back verbatim will
eventually normalise a space; a model asked for its first eight words will not,
and eight words is enough to locate a cut unambiguously.

**Why an LLM at all.** The previous version cut on blank lines and labelled
blocks — free, exact, identical every run. It could not see meaning: it split a
nine-step procedure into nine passages because the author bulleted them, and it
could not tell a table's rows from nine unrelated lines. A unit of meaning is not
always marked with a blank line, and that is the thing being divided on.

The cost is real and is paid in reproducibility: splitting is no longer
deterministic. ``split_passages_structural`` is kept as the fallback when the
model call fails, because losing Alex's document to a Bedrock timeout is worse
than splitting it bluntly.
"""

from __future__ import annotations

import logging
import os
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

DEFAULT_MODEL_ENV = "CLAUDE_SONNET_MODEL"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"


SPLIT_PROMPT = """You divide a document into passages. You do not rewrite it, and \
you do not copy it.

Read the WHOLE document first. Then decide where each passage begins, and return
those points. For each passage return only its OPENING — the first 6 to 10 words,
copied exactly — plus a short label.

Return a JSON array, in document order:
  [{"opening": "<the first 6-10 words of the passage, copied EXACTLY>",
    "label": "<3-6 words naming what this passage is>"}, ...]

The first element's "opening" must be the first words of the document.

=== WHERE A PASSAGE ENDS ===

Cut where a UNIT OF MEANING ends. A unit may be one sentence, a paragraph, a
claim block, a definition, a procedure, a table, or a list — whatever the content
actually is. Do not cut on formatting; formatting is a hint, not the rule.

**Keep together anything that only means something as a whole.** This is the rule
that matters most, because it is the one blank-line splitting gets wrong:

  - The steps of ONE procedure are ONE passage, however they are bulleted or
    numbered. Nine steps of a single protocol is one unit, not nine.
  - A table is ONE passage. Its meaning is positional — a row means what it means
    because of the column it sits in — so a row alone asserts nothing.
  - A list whose items are members of one set is ONE passage.
  - A claim and the sentence that qualifies, bounds or excepts it are ONE
    passage. Splitting a claim from its qualifier creates a claim the author
    never made.
  - A statement and the evidence offered for it in the next sentence are ONE
    passage when the evidence only makes sense as support for that statement.

**Cut when the document moves to a different thing.** A new claim, a new
definition, a different subject, a sentence that comments on the previous unit
from outside it rather than continuing it.

  document   "The stable diagnostic chain is: <definition>.
              - step one
              - step two
              ... (nine steps)"
  -> TWO passages: the chain definition, then the nine-step protocol whole.
     Not ten. The steps are one procedure.

  document   "<a 2x2 table>
              The upper-left quadrant is unoccupied."
  -> TWO passages: the table whole, then the sentence about it. The sentence is a
     comment on the table from outside it; the table's rows are not separable.

=== OPENINGS ===

"opening" is looked up in the document with an exact string search and the
boundary is DISCARDED if it is not found — the passage then merges into the one
before it. So:

  - Copy it character for character from the document. Do not retype it from
    memory, do not fix spelling, do not normalise punctuation or whitespace.
  - 6 to 10 words. Fewer may match the wrong place; more is wasted.
  - Start it at the true first character of the passage, including any label or
    bullet the passage opens with ("Claim 2 — The benchmark surfaced...").
  - Openings must appear in document order and must not repeat.

You are not returning the passage text. Only where it starts. Everything between
one opening and the next belongs to the earlier passage, so nothing is lost by
your not quoting it.

=== LABELS ===

3-6 words naming what the passage IS: "positioning claim", "nine-step diagnostic
protocol", "coverage comparison table", "pricing tiers". Not a summary and not a
judgement. The label is metadata and never becomes part of the passage.

Return ONLY the JSON array. No markdown fences, no commentary."""


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


def _passages_from_cuts(
    text: str, cuts: list[tuple[int, Optional[str]]]
) -> list[Passage]:
    """Turn ordered cut points into contiguous, whitespace-trimmed passages.

    Shared by both splitters, so the LLM path and the structural path produce
    identical guarantees: every passage is a slice, the slices are contiguous and
    in order, and no character of the document is dropped or duplicated.

    Args:
        text: The document.
        cuts: (offset, label) in ascending offset order. The first must be the
            start of the first passage.

    Returns:
        Passages in document order.
    """
    spans: list[tuple[int, int, Optional[str]]] = []
    for i, (start, label) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(text)
        # Trim whitespace off the span, so the stored text never carries the
        # separator characters between passages.
        block = text[start:end]
        lead = len(block) - len(block.lstrip())
        trail = len(block) - len(block.rstrip())
        start, end = start + lead, end - trail
        if start >= end:
            continue
        if spans and (end - start) < MIN_PASSAGE_CHARS:
            spans[-1] = (spans[-1][0], end, spans[-1][2])
            continue
        spans.append((start, end, label))

    passages = [
        Passage(
            text=text[s:e],
            start_char=s,
            end_char=e,
            index=i,
            label=label or _label_of(text[s:e]),
        )
        for i, (s, e, label) in enumerate(spans)
    ]
    audit_passages(passages, text)
    return passages


def split_passages_structural(text: str) -> list[Passage]:
    """Divide a document on blank lines and labelled blocks. No LLM.

    The fallback when the model call fails, and the deterministic reference the
    LLM splitter is compared against. It cannot see meaning — it will split a
    nine-step protocol into nine passages and a table into its rows — but it
    never fails and never varies.

    Args:
        text: The raw document.

    Returns:
        Passages in source order, each an exact slice of ``text``.
    """
    if not text or not text.strip():
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

    return _passages_from_cuts(text, [(s, None) for s in cuts])


# Characters that can sit between the start of a line and the start of a list
# item's text: whitespace, bullet glyphs, and the punctuation of "1." / "a)".
_MARKER_CHARS = set(" \t-*•·–—>+.)abcdefghijklmnopqrstuvwxyz0123456789")


def _snap_to_line_start(text: str, at: int) -> int:
    """Move a cut back over a list marker to the start of its line.

    A model asked for the opening of a bulleted passage returns "Parse the
    manuscript..." about as often as "- Parse the manuscript...", which puts the
    boundary either side of the "- ". The marker belongs to the item it marks, so
    the second is right and the first is a two-character error — visible as
    boundary drift between otherwise identical runs.

    Only moves backwards, only within one line, and only over marker characters,
    so it can never swallow content or cross a passage.

    Args:
        text: The document.
        at: The located cut offset.

    Returns:
        The line-start offset when only marker characters precede ``at`` on that
        line, otherwise ``at`` unchanged.
    """
    line_start = text.rfind("\n", 0, at) + 1
    if line_start == at:
        return at
    prefix = text[line_start:at]
    if len(prefix) <= 4 and all(c in _MARKER_CHARS for c in prefix.lower()):
        return line_start
    return at


def _anchor_cuts(text: str, items: list[Any]) -> list[tuple[int, Optional[str]]]:
    """Locate each returned opening in the document and derive the cut points.

    An opening that cannot be found, or that appears before the previous cut, is
    dropped with a warning rather than guessed at: the passage then merges into
    the one before it, which loses a boundary but never loses text. The first cut
    is forced to 0 so nothing before the model's first opening is orphaned.

    Args:
        text: The document.
        items: The parsed JSON array from the model.

    Returns:
        (offset, label) pairs in ascending order, starting at 0.
    """
    cuts: list[tuple[int, Optional[str]]] = []
    cursor = 0

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning("[Passages] element %d is not an object, skipped", i)
            continue
        opening = str(item.get("opening") or "").strip()
        label = str(item.get("label") or "").strip() or None
        if not opening:
            logger.warning("[Passages] element %d has no opening, skipped", i)
            continue

        at = text.find(opening, cursor)
        if at == -1:
            # Whitespace normalisation is the one copying deviation tolerated —
            # a model that copies across a line break returns a single space —
            # and only because the offset is then taken from the document, not
            # from what the model wrote.
            pattern = r"\s+".join(re.escape(word) for word in opening.split())
            match = re.search(pattern, text[cursor:]) if pattern else None
            at = cursor + match.start() if match else -1
        if at == -1:
            logger.warning(
                "[Passages] opening %d not found after offset %d, boundary "
                "dropped (its text merges into the previous passage): %r",
                i,
                cursor,
                opening[:70],
            )
            continue

        at = _snap_to_line_start(text, at)
        if cuts and at <= cuts[-1][0]:
            logger.warning(
                "[Passages] opening %d resolves at or before the previous cut, "
                "boundary dropped: %r",
                i,
                opening[:70],
            )
            continue

        cuts.append((at, label))
        cursor = at + 1

    if not cuts:
        return []
    if cuts[0][0] != 0:
        # Text before the first opening would otherwise be lost.
        cuts.insert(0, (0, cuts[0][1] if len(cuts) == 1 else None))
    return cuts


def split_passages(
    text: str, model_id: Optional[str] = None, use_llm: bool = True
) -> list[Passage]:
    """Divide a document into verbatim passages at units of meaning.

    One LLM call reads the whole document and returns where each passage begins.
    The model never supplies passage text — every passage is ``text[cut:next_cut]``
    — so ``source[start_char:end_char] == text`` holds by construction and the
    passages are contiguous, whatever the model returns.

    Args:
        text: The raw document.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        use_llm: False forces the structural splitter, for comparison runs.

    Returns:
        Passages in document order, each an exact slice of ``text``. Empty list
        for empty input. A block shorter than MIN_PASSAGE_CHARS folds into the
        one before it.
    """
    if not text or not text.strip():
        logger.info("[Passages] Empty input, returning no passages")
        return []

    if not use_llm:
        return split_passages_structural(text)

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    try:
        from services.semantic_chunker import _call_llm, _parse_json_array

        items = _parse_json_array(_call_llm(SPLIT_PROMPT, text, model))
        cuts = _anchor_cuts(text, items)
        if not cuts:
            raise ValueError("no usable passage boundaries were returned")
    except Exception as exc:  # noqa: BLE001 — logged, then degraded, never silent
        logger.error(
            "[Passages] LLM split failed (%s), falling back to the structural "
            "splitter — boundaries will follow blank lines, not meaning",
            str(exc)[:200],
        )
        return split_passages_structural(text)

    passages = _passages_from_cuts(text, cuts)
    logger.info(
        "[Passages] %d chars -> %d passage(s) via %s: %s",
        len(text),
        len(passages),
        model,
        ", ".join(f"{p.label!r}" for p in passages),
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
