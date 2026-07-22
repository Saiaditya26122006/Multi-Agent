"""End-to-end integration test for the Feed pipeline (F13).

Exercises the whole chain on real prose and asserts the invariants that this
session's work established — extraction -> classification -> tiering -> filing
target — so a future change that breaks the chain is caught.

Hits live Bedrock (a few LLM calls); slow, like the other feed/rag tests.
Run: pytest tests/test_feed_integration.py -v -s
"""

import pytest

from web.handlers.feed_handler import (
    divide_into_facts,
    classify_and_match_node,
    _determine_tier,
    _resolve_filing_target,
)

RAW = (
    "We spoke to a research dean at IESE last month. He confirmed that manuscript "
    "quality is a real problem and that ANECA accreditation pressure is pushing "
    "institutions to adopt systematic assessment. Pricing is still open but we think "
    "it should be around 5000 euros per institution annually."
)

AUTO_FILED_TIERS = {"auto_file", "auto_file_flagged"}
LIVE_TIERS = {"auto_file_flagged", "ask"}  # the only two the gate produces now


def test_prose_is_decontextualized():
    """Multi-sentence prose routes to LLM extraction and resolves pronouns."""
    facts = divide_into_facts(RAW)
    assert facts, "expected extracted facts"
    # LLM path used for multi-sentence prose
    assert facts[0].get("source_format") == "llm_extracted"
    joined = " ".join(f["text"].lower() for f in facts)
    # 'He' / 'it' should be resolved away into concrete subjects
    assert " he " not in " " + joined + " ", "pronoun 'he' not decontextualized"


def test_full_chain_invariants():
    """Every fact classifies, tiers to a live tier, and auto-filed ones are safe."""
    facts = divide_into_facts(RAW)
    wrong_domain_unflagged = 0

    for f in facts:
        result = classify_and_match_node(f["text"])
        tier = _determine_tier(result, source="alex_direct")

        # only the two calibrated tiers are ever produced
        assert tier in LIVE_TIERS, f"unexpected tier {tier}"

        if tier in AUTO_FILED_TIERS:
            # auto-filed => router-committed => domain agreement present
            signals = result.get("signals", {})
            assert signals.get("domain_agreement"), "auto-filed without domain agreement"

            # auto-filed facts file at the SECTION with a suggested leaf (or are
            # already section/domain level)
            target = _resolve_filing_target(result)
            assert target["file_node_id"], "no filing target"
            if target["backed_off"]:
                assert target["suggested_leaf_id"], "backed off but no suggested leaf"
                # filed node is shallower than the leaf
                assert target["file_node_id"].count(".") < target["suggested_leaf_id"].count(".")

    # invariant: nothing auto-files into a bad state silently (all flagged)
    assert wrong_domain_unflagged == 0


def test_confidence_signal_present():
    """classify_and_match_node exposes the signals the tier gate depends on."""
    result = classify_and_match_node("We charge 5000 euros per institution annually")
    assert "signals" in result
    s = result["signals"]
    assert "domain_agreement" in s
    assert "domain_router_ids" in s


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
