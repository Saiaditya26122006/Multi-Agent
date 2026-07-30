#!/usr/bin/env python3
"""Test harness for services/semantic_chunker.py.

Runs eight inputs through the chunker and prints input -> facts for each, so the
splitting and the fidelity verdicts can be read and judged by a human. Writes
nothing to any datastore.

Cases 1-4 exercise splitting. Cases 5-8 exercise epistemic strength: each is
built from hedges, preferences and opinions, and carries a list of markers that
must survive into the extracted facts.

NOTE ON REGRESSION TESTING: this harness deliberately asserts nothing about the
NUMBER of facts. Splitting is non-deterministic at merge/split boundaries, and a
count assertion would fail on a correct run. Assert on content and fidelity —
that a hedge survived, that a preference was not promoted to a plan, that no
fact contradicts its source.

    python scripts/test_semantic_chunker.py
"""

import logging
import os
import sys
import textwrap

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.semantic_chunker import chunk_text  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# ---------------------------------------------------------------- splitting

TINY = "Our annual price is twelve thousand euros per institution."

TWO_CLAIMS = (
    "We are focusing on business schools in Spain for the first year. "
    "The annual licence is twelve thousand euros per institution. "
    "Both of those are settled."
)

PRONOUNS = (
    "We shipped the new pricing tier in March. It replaced the old per-seat "
    "model, which nobody at the larger institutions liked. That change raised "
    "churn to 8% in the first quarter, mostly among the smaller departments who "
    "had been paying for three or four seats. They told us the new minimum was "
    "more than their whole training budget. We are keeping it anyway, because "
    "the larger accounts have expanded faster since it launched, and those "
    "accounts are now 70% of revenue."
)

LONG_PASSAGE = (
    "Here is where we stand going into the second half.\n\n"
    "The product is a diagnostic layer for academic manuscripts. It reads a "
    "full draft and flags claims that the cited evidence does not support. It "
    "does not check plagiarism and it does not predict whether a paper will be "
    "accepted, and we have been firm with everyone that it never will.\n\n"
    "Our buyer is the research dean, not the individual researcher. The "
    "researcher is the one who uses it day to day, but they have no budget. In "
    "practice the doctoral supervisors are the ones who push for it internally, "
    "and procurement is where deals slow down. A dean can approve up to "
    "twenty-five thousand euros without going to the finance committee, which "
    "is why we priced the department tier under that.\n\n"
    "Pricing is an annual institutional subscription. Eight thousand for a "
    "single department, twenty thousand for a faculty, forty-five thousand "
    "campus-wide. We will discount up to fifteen percent for a three-year "
    "commitment and no further. We are not charging per manuscript, because "
    "every institution we spoke to said unpredictable costs would kill it in "
    "procurement.\n\n"
    "Geographically we are Spain and the EU only for the first eighteen "
    "months. No US work. Compliance is the reason: GDPR applies to everything "
    "we store, manuscripts sit in EU-region storage, and they are deleted "
    "ninety days after the report goes out. Customer manuscripts are never used "
    "to train or fine-tune any model.\n\n"
    "The pilot runs twelve weeks across three departments, about forty "
    "manuscripts each. We are comparing our flags against what two independent "
    "reviewers marked on the same paper. If we cannot beat the reviewers on "
    "recall we do not have a product, and I would rather find that out in "
    "October than next year.\n\n"
    "On the team: neither of us has sold into universities before. That is the "
    "gap I am most worried about."
)

# ------------------------------------------------------- epistemic strength

HEDGES = (
    "We might move the pilot to Q1 if the second department signs. Probably "
    "three departments, maybe four. I think the pricing is roughly right but "
    "I'm not certain, and we should probably revisit it after the pilot."
)

PREFERENCES = (
    "I'd rather lose the deal than discount below fifteen percent. We are "
    "leaning toward the per-institution model, though nothing is signed yet. "
    "My preference is to stay in Spain another year before we look at France."
)

OPINIONS = (
    "Sales think the twelve thousand price point is too high for smaller "
    "departments. In my view the real blocker is procurement, not price. The "
    "engineering team believes we can ship by March, but they have been wrong "
    "about dates before."
)

CONDITIONALS = (
    "If the pilot beats the reviewers on recall, we hire two more engineers. "
    "Otherwise we stop and rethink. Churn is around eight percent, maybe a bit "
    "under. We could look at the UK next year but that is not decided."
)

# ------------------------------------------------------- speaker identity

INTERVIEW = (
    "Notes from the call with the research office at Universidad de Navarra.\n\n"
    "\"I would never pay twelve thousand for this. My whole training budget is "
    "eight and I have to cover three other tools out of it. I think the tool is "
    "good, but we would need it at half that price, and even then I would have "
    "to take it to the vice-rector. Honestly I am not the person who decides.\""
)

# Identity words that must never appear: the sources below never state a role,
# so any of these in a fact means the extractor inferred an identity.
ROLE_WORDS = ["ceo", "founder", "alex", "the sales lead", "the chief"]

# --------------------------------------------------- splitting guard rails
#
# Both of these guard the group tagging added for group classification. Grouping
# tells the classifier that several facts came from one list; it must never
# become a licence to fuse them into one fact, or to let one member's units
# leak onto another.

# Distinct claims that share a subject and sit in one sentence. Grouping them is
# correct; merging them into one fact is not — three tiers are three prices.
OVER_MERGE = (
    "Pricing is an annual institutional subscription. Eight thousand for a "
    "single department, twenty thousand for a faculty, forty-five thousand "
    "campus-wide."
)

# Two currencies in adjacent sentences. Each amount must keep its own currency;
# a fact that says the Spanish price is in dollars, or drops the currency
# entirely, is the failure this case exists to catch.
CURRENCY_BLEED = (
    "Our Spanish customers pay in euros. The US pilot is priced in dollars. "
    "Setup is two thousand euros in Spain and three thousand dollars in the US."
)

CASES = [
    ("1. Ten words, one claim", TINY, "expect 1 fact", [], []),
    ("2. Three sentences, two distinct claims", TWO_CLAIMS, "expect 2 facts", [], []),
    ("3. ~90 words, dense pronouns and references", PRONOUNS,
     "expect references resolved - no dangling this/it/they/those", [], []),
    ("4. ~380 words, many claims across topics", LONG_PASSAGE,
     "expect sensible split; 'I' must stay 'the speaker', never 'the CEO'",
     ["rather", "speaker"], ROLE_WORDS),
    ("5. Hedges", HEDGES,
     "expect might/probably/maybe/think/roughly preserved - no certainties",
     ["might", "probabl", "maybe", "think", "roughly", "not certain"], ROLE_WORDS),
    ("6. Preferences", PREFERENCES,
     "expect 'rather' / 'leaning' preserved - nothing promoted to a decision",
     ["rather", "leaning", "preference", "nothing is signed"], ROLE_WORDS),
    ("7. Opinions", OPINIONS,
     "expect opinions attributed to who holds them - not stated as fact",
     ["sales", "view", "believ"], ROLE_WORDS),
    ("8. Conditionals and approximations", CONDITIONALS,
     "expect conditions and 'around'/'maybe' kept with their claim",
     ["if", "around", "maybe", "not decided", "could"], ROLE_WORDS),
    ("9. THIRD-PARTY SPEAKER (the catastrophic case)", INTERVIEW,
     "a customer is speaking, NOT Alex - 'I' must never become 'the CEO'. "
     "A fact like 'The CEO would never pay twelve thousand' would be backwards.",
     ["speaker", "never pay"], ROLE_WORDS),
    ("10. NO OVER-MERGING (group tagging must not fuse a list)", OVER_MERGE,
     "expect one fact per tier - all three amounts present as separate claims, "
     "sharing a group label but never merged into one fact",
     ["eight thousand", "twenty thousand", "forty-five thousand"], []),
    ("11. NO CURRENCY BLEED", CURRENCY_BLEED,
     "expect each amount to keep its own currency - euros with Spain, dollars "
     "with the US, and no amount left without its currency",
     ["euro", "dollar"], []),
]

DANGLING = ("this ", "it ", "they ", "that change", "those ", "the new minimum")


def show(
    title: str,
    text: str,
    note: str,
    must_survive: list[str],
    must_not_appear: list[str],
) -> None:
    """Run one case and print the input, the facts, and the fidelity verdicts."""
    print("\n" + "=" * 78)
    print(f"{title}   ({len(text.split())} words, {len(text)} chars)")
    print(f"expectation: {note}")
    print("-" * 78)
    print("INPUT:")
    for line in text.split("\n"):
        print(textwrap.fill(line, 74, initial_indent="  ", subsequent_indent="  ")
              if line.strip() else "")
    print("-" * 78)

    facts = chunk_text(text)
    flagged = [f for f in facts if f.needs_review]
    print(f"OUTPUT: {len(facts)} fact(s), {len(flagged)} flagged for review\n")

    for f in facts:
        span = f"[{f.start_char}:{f.end_char}]" if f.start_char is not None else "[unlocated]"
        mark = "  !!" if f.needs_review else "    "
        print(textwrap.fill(f"{f.index + 1}. {f.fact}", 72,
                            initial_indent=mark, subsequent_indent="       "))
        group = f" group={f.group_id}:{f.group_label!r}" if f.group_id else ""
        print(f"       span {span}  verdict={f.verdict}{group}")
        if f.needs_review:
            print(f"       FLAGGED: {f.review_reason}")
        if f.fact.lower().startswith(DANGLING):
            print("       ** unresolved reference at start of fact **")
        print()

    blob = " ".join(f.fact.lower() for f in facts)

    if must_survive:
        missing = [m for m in must_survive if m.lower() not in blob]
        kept = [m for m in must_survive if m.lower() in blob]
        print(f"  strength markers kept:    {kept}")
        print(f"  strength markers MISSING: {missing if missing else 'none'}")

    if must_not_appear:
        leaked = [m for m in must_not_appear if m.lower() in blob]
        if leaked:
            print(f"  ** IDENTITY INFERRED — forbidden role words present: {leaked} **")
        else:
            print("  identity check: PASS (no inferred role words)")


def main() -> None:
    """Run every case."""
    print(f"model: {os.getenv('CLAUDE_SONNET_MODEL', '(default)')}")
    for title, text, note, markers, forbidden in CASES:
        show(title, text, note, markers, forbidden)
    print("\n" + "=" * 78)
    print("Nothing was written to knowledge_base or bp_architecture.")


if __name__ == "__main__":
    main()
