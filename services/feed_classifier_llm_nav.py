"""LLM-navigated classification — no embeddings anywhere in the path.

Every previous approach used vector similarity to narrow the tree, then an LLM to
choose. Eight mechanisms were measured that way and the ceiling was retrieval:
best leaf recall@10 = 55.6%, because facts are written as operational instances
and nodes as abstract governance categories, and no similarity function bridges
that gap.

This removes the similarity step entirely:

    fact + 12 domain definitions        -> LLM picks a domain
    fact + that domain's 6-9 sections   -> LLM picks a section
    fact + that section's leaves + RULES -> LLM picks a leaf, or "none fit"

The premise is that the rule columns Alex wrote (`required_output`,
`evidence_requirement`, `prohibited_claims_inference_patterns`) are *instructions
for a reasoner*, not text to be matched. A reasoner can apply "Must not infer
budget authority from buyer role" as a rule; an embedding can only measure how
much a fact sounds like it.

**This is v1's shape with v1's failure mode removed.** v1 walked the same three
hops but chose by cosine similarity and lost 40 of 54 facts at hop one, because
domain embeddings are abstract governance prose whose total spread across all 12
domains was 0.22. The tree was never the problem; the similarity function was.

Cost is the trade: three sequential LLM calls per fact instead of one, and the
hops cannot be parallelised because each depends on the previous choice.

Standalone: reads bp_architecture, calls Bedrock, writes nothing.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from services.feed_classifier_v3 import (
    _call_llm,
    _parse_json_object,
    parent_of,
)

logger = logging.getLogger(__name__)

NONE_FIT = "none"
DEFAULT_MODEL_ENV = "CLAUDE_SONNET_MODEL"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"

_COMMON_RULES = """
=== HOW TO CHOOSE ===
Ask what the fact would POPULATE, not what it is about. Topic overlap is the most
common way to get this wrong: a fact mentioning price is not automatically the
pricing node, and a title that repeats a word from the fact is not thereby right.

Answer from the definitions given. Do not invent an option that is not listed.

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<one sentence: what the fact actually asserts>",
 "choice": "<id from the list%s>",
 "confidence": "<certain | likely | unsure>"}

Write "reason" first and let the choice follow from it.

=== CONFIDENCE — used to decide whether to file without a human ===
"certain"  the option's own definition covers this fact directly, and no other
           listed option covers it as well. You would defend this to a reviewer.
"likely"   this is the best of the listed options, but another is defensible, or
           the fact only partly matches what this option governs.
"unsure"   you are choosing the least-bad option; the right one may not be listed.

Be honest rather than generous. "certain" is a claim that a human need not check
this, so use it only when that is true."""

DOMAIN_PROMPT = (
    """You are filing one atomic business fact into a business-plan architecture.

Twelve top-level DOMAINS are listed below with what each governs. Choose the one
domain whose scope this fact falls inside."""
    + _COMMON_RULES % ""
)

SECTION_PROMPT = (
    """You are filing one atomic business fact inside a chosen domain of a
business-plan architecture.

The SECTIONS of that domain are listed below. Choose the one whose scope this
fact falls inside."""
    + _COMMON_RULES % ""
)

LEAF_PROMPT = (
    """You are filing one atomic business fact at a specific node of a
business-plan architecture.

The candidate NODES are listed below with the rules that govern them:

  required_output       what must eventually be written at this node
  evidence_requirement  what kind of evidence that output needs
  prohibited_claims     inferences that must NOT be made here

Apply those rules as rules. If the fact would violate a node's prohibited_claims,
that node is wrong however well the topic matches.

Choose the node whose REQUIRED OUTPUT this fact would populate — or answer "none"
if nothing here fits. "none" is a correct and valuable answer: a wrong filing is
worse than a deferred one, and the earlier hops may have landed in the wrong part
of the tree."""
    + _COMMON_RULES % ', or the string "none"'
)


@dataclass
class NavStep:
    """One navigation hop."""

    level: str
    choice: Optional[str]
    reason: str
    confidence: str
    options: int
    seconds: float
    in_tokens: int = 0
    out_tokens: int = 0


@dataclass
class NavResult:
    """The outcome of navigating one fact down the tree."""

    fact: str
    domain: Optional[str]
    section: Optional[str]
    leaf: Optional[str]
    steps: list[NavStep] = field(default_factory=list)
    seconds: float = 0.0
    failed_at: Optional[str] = None

    @property
    def confidence(self) -> str:
        """The weakest confidence across the hops taken.

        A chain of choices is only as trustworthy as its least certain link: a
        "certain" leaf pick inside a section chosen while "unsure" is not a
        certain filing, and treating it as one is how a hard commit at hop 2
        turns into a confident wrong auto-file.
        """
        order = {"certain": 2, "likely": 1, "unsure": 0}
        taken = [s.confidence for s in self.steps]
        if not taken:
            return "unsure"
        return min(taken, key=lambda c: order.get(c, 0))

    @property
    def decided(self) -> bool:
        """True when a concrete leaf was chosen."""
        return bool(self.leaf and self.leaf != NONE_FIT)

    def to_dict(self) -> dict[str, Any]:
        """Return the result as a plain dict."""
        return asdict(self)


def _field(node: dict, key: str) -> str:
    """Trim a node field, treating 'None' as absent."""
    value = (node.get(key) or "").strip()
    return value if value and value != "None" else "(not specified)"


def _describe_scope(node: dict) -> str:
    """Domain/section rendering — scope only, no leaf-level rules."""
    return (
        f"{node['node_id']}: {_field(node, 'node_title')}\n"
        f"   governs: {_field(node, 'purpose')}"
    )


def _describe_leaf(node: dict) -> str:
    """Leaf rendering — the full rule set, which is the point of this design."""
    lines = [
        f"{node['node_id']}: {_field(node, 'node_title')}",
        f"   purpose: {_field(node, 'purpose')}",
        f"   required_output: {_field(node, 'required_output')}",
        f"   evidence_requirement: {_field(node, 'evidence_requirement')}",
        f"   prohibited_claims: {_field(node, 'prohibited_claims_inference_patterns')}",
    ]
    if node.get("degraded_target"):
        lines.append(f"   NOTE: definition incomplete ({node.get('degraded_reason')})")
    return "\n".join(lines)


def _hop(
    level: str,
    fact: str,
    prompt: str,
    options: list[dict],
    renderer,
    valid: set[str],
    model: str,
) -> NavStep:
    """Run one navigation hop and return the chosen id."""
    payload = (
        f"FACT:\n{fact}\n\n"
        f"OPTIONS ({len(options)}):\n\n"
        + "\n\n".join(renderer(o) for o in options)
    )
    started = time.perf_counter()
    raw = _call_llm(prompt, payload, model)
    elapsed = time.perf_counter() - started

    parsed = _parse_json_object(raw)
    choice = str(parsed.get("choice", "")).strip()
    if choice != NONE_FIT and choice not in valid:
        logger.warning(
            "[LLMNav] %s hop returned unlisted id %r — treated as no choice",
            level,
            choice[:60],
        )
        choice = None
    confidence = str(parsed.get("confidence", "unsure")).strip().lower()
    if confidence not in ("certain", "likely", "unsure"):
        confidence = "unsure"
    return NavStep(
        level=level,
        choice=choice,
        reason=str(parsed.get("reason", "")).strip(),
        confidence=confidence,
        options=len(options),
        seconds=round(elapsed, 2),
    )


def navigate(fact: str, arch: Any, model_id: Optional[str] = None) -> NavResult:
    """Walk domain -> section -> leaf by LLM reasoning over the node rules.

    Args:
        fact: The atomic fact text.
        arch: A loaded Architecture (used for its node metadata only — no vectors).
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.

    Returns:
        A NavResult with the chosen leaf (or None), every hop, and timings.
    """
    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    started = time.perf_counter()
    result = NavResult(fact=fact, domain=None, section=None, leaf=None)

    domains = sorted(
        (arch.nodes[n] for n in arch.nodes if n.count(".") == 1),
        key=lambda r: int(r["node_id"].split(".")[1]),
    )
    step = _hop("domain", fact, DOMAIN_PROMPT, domains, _describe_scope,
                {d["node_id"] for d in domains}, model)
    result.steps.append(step)
    if not step.choice or step.choice == NONE_FIT:
        result.failed_at = "domain"
        result.seconds = round(time.perf_counter() - started, 2)
        return result
    result.domain = step.choice

    sections = [
        arch.nodes[n] for n in arch.nodes
        if n.count(".") == 2 and n.startswith(result.domain + ".")
    ]
    if not sections:
        result.failed_at = "section (none under domain)"
        result.seconds = round(time.perf_counter() - started, 2)
        return result
    step = _hop("section", fact, SECTION_PROMPT, sections, _describe_scope,
                {s["node_id"] for s in sections}, model)
    result.steps.append(step)
    if not step.choice or step.choice == NONE_FIT:
        result.failed_at = "section"
        result.seconds = round(time.perf_counter() - started, 2)
        return result
    result.section = step.choice

    leaves = [
        arch.nodes[n] for n in arch.leaf_ids if parent_of(n) == result.section
    ]
    if not leaves:
        # A section whose children are not leaves, or a childless section.
        leaves = [arch.nodes[n] for n in arch.leaf_ids
                  if n.startswith(result.section + ".")] or [arch.nodes[result.section]]
    step = _hop("leaf", fact, LEAF_PROMPT, leaves, _describe_leaf,
                {leaf["node_id"] for leaf in leaves}, model)
    result.steps.append(step)
    result.leaf = step.choice
    if not step.choice:
        result.failed_at = "leaf"
    elif step.choice == NONE_FIT:
        result.failed_at = "leaf (none fit)"

    result.seconds = round(time.perf_counter() - started, 2)
    return result
