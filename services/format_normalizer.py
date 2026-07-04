"""
Format Normalizer — detects and splits raw text into atomic claims.

Handles paragraphs, bullets, tables, CSV, and mixed formats.
This is the first stage of the FEED pipeline: raw input → atomic facts.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def detect_format(text: str) -> str:
    """Detect the format of raw text input.

    Args:
        text: The raw text to analyze.

    Returns:
        One of: "bullets", "table", "csv", "paragraph", "mixed".
    """
    lines = text.strip().split("\n")
    lines = [l for l in lines if l.strip()]

    if not lines:
        return "paragraph"

    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    bullet_lines = sum(1 for l in lines if bullet_pattern.match(l))

    pipe_lines = sum(1 for l in lines if "|" in l)
    tab_lines = sum(1 for l in lines if "\t" in l)
    table_lines = max(pipe_lines, tab_lines)

    comma_rich_lines = sum(1 for l in lines if l.count(",") >= 3)

    total = len(lines)

    if comma_rich_lines > total * 0.6 and total >= 2:
        return "csv"

    if table_lines > total * 0.5:
        return "table"

    if bullet_lines > total * 0.6:
        return "bullets"

    if bullet_lines > 0 and (total - bullet_lines) >= 2:
        return "mixed"

    return "paragraph"


def normalize(text: str) -> list[dict]:
    """Master function: detect format → split → return atomic facts.

    Args:
        text: Raw text in any format.

    Returns:
        List of dicts with: text, source_format, line_number (approx).
    """
    if not text or not text.strip():
        return []

    fmt = detect_format(text)

    if fmt == "bullets":
        return split_bullets(text)
    elif fmt == "table":
        return split_table(text)
    elif fmt == "csv":
        return split_csv(text)
    elif fmt == "mixed":
        return split_mixed(text)
    else:
        return split_paragraphs(text)


def split_paragraphs(text: str) -> list[dict]:
    """Split paragraph text into sentences as atomic claims.

    Uses sentence-boundary detection. Merges very short sentences with previous.

    Args:
        text: Paragraph text.

    Returns:
        List of fact dicts.
    """
    text = text.strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

    facts = []
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if len(sent) < 10:
            if facts:
                facts[-1]["text"] += " " + sent
            continue
        facts.append({
            "text": sent,
            "source_format": "paragraph",
            "position": i,
        })

    return facts


def split_bullets(text: str) -> list[dict]:
    """Parse bullet-point text into individual facts.

    Args:
        text: Bullet-formatted text.

    Returns:
        List of fact dicts.
    """
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    lines = text.strip().split("\n")
    facts = []

    for i, line in enumerate(lines):
        cleaned = bullet_pattern.sub("", line).strip()
        if cleaned and len(cleaned) > 5:
            facts.append({
                "text": cleaned,
                "source_format": "bullets",
                "position": i,
            })

    return facts


def split_table(text: str) -> list[dict]:
    """Parse table-formatted text (pipe or tab separated) into facts.

    Combines header with row values for context.

    Args:
        text: Table-formatted text.

    Returns:
        List of fact dicts.
    """
    lines = text.strip().split("\n")
    separator = "|" if text.count("|") > text.count("\t") else "\t"

    header = None
    facts = []

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^[\s\-|:+]+$", line):
            continue

        cells = [c.strip() for c in line.split(separator) if c.strip()]

        if header is None and cells:
            header = cells
            continue

        if not cells:
            continue

        if header:
            parts = []
            for j, cell in enumerate(cells):
                col_name = header[j] if j < len(header) else f"col_{j}"
                parts.append(f"{col_name}: {cell}")
            row_text = "; ".join(parts)
        else:
            row_text = "; ".join(cells)

        if len(row_text) > 5:
            facts.append({
                "text": row_text,
                "source_format": "table",
                "position": i,
            })

    return facts


def split_csv(text: str) -> list[dict]:
    """Parse CSV-formatted text into facts.

    Args:
        text: CSV text with comma separators.

    Returns:
        List of fact dicts.
    """
    lines = text.strip().split("\n")
    header = None
    facts = []

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        cells = [c.strip().strip('"') for c in line.split(",")]

        if header is None and cells:
            header = cells
            continue

        if not cells:
            continue

        if header:
            parts = []
            for j, cell in enumerate(cells):
                if cell:
                    col_name = header[j] if j < len(header) else f"col_{j}"
                    parts.append(f"{col_name}: {cell}")
            row_text = "; ".join(parts)
        else:
            row_text = "; ".join(c for c in cells if c)

        if len(row_text) > 5:
            facts.append({
                "text": row_text,
                "source_format": "csv",
                "position": i,
            })

    return facts


def split_mixed(text: str) -> list[dict]:
    """Split mixed-format text (bullets + paragraphs).

    Args:
        text: Mixed format text.

    Returns:
        List of fact dicts.
    """
    lines = text.strip().split("\n")
    bullet_pattern = re.compile(r"^\s*[-*•]\s+|^\s*\d+[.)]\s+")
    facts = []
    paragraph_buffer = []

    for i, line in enumerate(lines):
        if bullet_pattern.match(line):
            if paragraph_buffer:
                para_text = " ".join(paragraph_buffer)
                facts.extend(split_paragraphs(para_text))
                paragraph_buffer = []
            cleaned = bullet_pattern.sub("", line).strip()
            if cleaned and len(cleaned) > 5:
                facts.append({
                    "text": cleaned,
                    "source_format": "mixed_bullet",
                    "position": i,
                })
        elif line.strip():
            paragraph_buffer.append(line.strip())

    if paragraph_buffer:
        para_text = " ".join(paragraph_buffer)
        facts.extend(split_paragraphs(para_text))

    return facts


def deduplicate_facts(facts: list[dict]) -> list[dict]:
    """Remove duplicate facts based on normalized text comparison."""
    seen: set = set()
    unique: list[dict] = []
    for fact in facts:
        normalized = re.sub(r"[^\w\s]", "", fact.get("text", "").lower()).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(fact)
    return unique
