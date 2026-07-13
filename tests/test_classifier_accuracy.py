"""
Test suite for classifier accuracy improvements.

This test suite uses the 6 known misclassifications from Alex's PMF analysis
document (memory: project_classifier_accuracy_bug.md) to measure classifier
accuracy improvements after fixes.

The baseline was 3/9 correct (33%) before fixes. Target: 80%+ after fixes.
"""

import logging
import pytest

from web.handlers.feed_handler import classify_and_match_node

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Test cases: Each tuple is (fact_text, correct_node_id, prohibited_node_ids, context)
# prohibited_node_ids are nodes that should NEVER be returned (violate prohibitions)
PMF_TEST_CASES = [
    (
        "Job: Improve manuscript quality before submission",
        ["BP.2.1.1", "BP.10.3.2"],  # Either is acceptable
        ["BP.1.2.1"],  # This node PROHIBITS manuscript improvement claims
        "PMF options analysis for EpistemicOS",
    ),
    (
        "Job: Improve manuscript and thesis quality at scale",
        ["BP.2.1.1", "BP.10.3.2"],
        ["BP.1.2.1"],
        "PMF options analysis for EpistemicOS",
    ),
    (
        "Outcome: Manuscripts evaluated through auditable workflow",
        ["BP.1.3", "BP.1.8.5"],
        ["BP.5.4.3"],  # BP.5.4.3 is procurement approval, not product workflow
        "PMF options analysis for EpistemicOS",
    ),
    (
        "Institution concludes: We need systematic assessment of manuscript quality",
        ["BP.2.3"],  # Urgency Hypothesis
        ["BP.1.3"],  # BP.1.3 prohibits inferring adoption/urgency from workflow
        "PMF options analysis for EpistemicOS",
    ),
    (
        "PMF is not: researchers like it or send positive feedback",
        ["BP.10.3.8"],  # Prohibited PMF Inferences
        ["BP.10.3.2"],  # BP.10.3.2 is stage definitions, not false-signal definitions
        "PMF options analysis for EpistemicOS",
    ),
    (
        "PMF exists when a market repeatedly pulls a product from the company",
        ["BP.10.3.1"],  # PMF Evidence Framework (general definition)
        ["BP.10.3.2"],  # BP.10.3.2 is stage boundaries, not general definition
        "PMF options analysis for EpistemicOS",
    ),
]


@pytest.mark.parametrize("fact_text,acceptable_nodes,prohibited_nodes,context", PMF_TEST_CASES)
def test_pmf_classification_accuracy(fact_text, acceptable_nodes, prohibited_nodes, context):
    """Test classifier accuracy on Alex's PMF analysis facts.

    Each fact should:
    1. NOT be filed under any prohibited node (hard constraint)
    2. Ideally be filed under one of the acceptable nodes (accuracy target)
    """
    result = classify_and_match_node(
        fact_text,
        session_id=None,
        document_context=context,
        use_fast_model=False,  # Use Sonnet for test accuracy
    )

    classified_node = result.get("node_id")
    confidence = result.get("confidence")
    reasoning = result.get("reasoning", "")
    none_fit = result.get("none_fit", False)

    logger.info(
        f"\n[Test] Fact: {fact_text[:60]}...\n"
        f"  Classified as: {classified_node} ({confidence})\n"
        f"  Reasoning: {reasoning}\n"
        f"  Acceptable: {acceptable_nodes}\n"
        f"  Prohibited: {prohibited_nodes}"
    )

    # HARD CONSTRAINT: Must not classify into prohibited node
    if classified_node in prohibited_nodes:
        pytest.fail(
            f"PROHIBITION VIOLATION: Fact classified into prohibited node {classified_node}. "
            f"Prohibited nodes: {prohibited_nodes}. This violates the node's prohibited_claims."
        )

    # SOFT TARGET: Ideally should match one of the acceptable nodes
    # This is logged as a warning, not a hard failure, since "none_fit" or
    # a different (but not prohibited) node might be acceptable behavior
    if classified_node not in acceptable_nodes and not none_fit:
        logger.warning(
            f"  ACCURACY MISS: Expected one of {acceptable_nodes}, got {classified_node}. "
            f"Not a failure (didn't violate prohibition), but hurts accuracy score."
        )


def test_classifier_overall_accuracy():
    """Calculate overall accuracy across all PMF test cases.

    Runs all test cases and reports percentage that matched acceptable nodes.
    Target: 80%+ after fixes (baseline was 33% = 3/9 before fixes).
    """
    correct = 0
    total = len(PMF_TEST_CASES)
    violations = 0

    for fact_text, acceptable_nodes, prohibited_nodes, context in PMF_TEST_CASES:
        result = classify_and_match_node(
            fact_text,
            session_id=None,
            document_context=context,
            use_fast_model=False,
        )

        classified_node = result.get("node_id")

        # Count prohibition violations (these are FAILURES)
        if classified_node in prohibited_nodes:
            violations += 1
            logger.error(
                f"VIOLATION: '{fact_text[:50]}...' -> {classified_node} (prohibited)"
            )

        # Count accuracy hits (these are SUCCESSES)
        if classified_node in acceptable_nodes:
            correct += 1
            logger.info(
                f"CORRECT: '{fact_text[:50]}...' -> {classified_node}"
            )
        else:
            logger.warning(
                f"MISS: '{fact_text[:50]}...' -> {classified_node} (expected {acceptable_nodes})"
            )

    accuracy = (correct / total) * 100
    violation_rate = (violations / total) * 100

    logger.info(
        f"\n{'='*60}\n"
        f"CLASSIFIER ACCURACY REPORT\n"
        f"{'='*60}\n"
        f"Total test cases: {total}\n"
        f"Correct classifications: {correct}/{total} ({accuracy:.1f}%)\n"
        f"Prohibition violations: {violations}/{total} ({violation_rate:.1f}%)\n"
        f"Target accuracy: 80%+\n"
        f"Baseline (before fixes): 33%\n"
        f"{'='*60}\n"
    )

    # Fail if any prohibition violations
    assert violations == 0, (
        f"{violations} prohibition violations detected. These are HARD ERRORS — "
        f"facts were filed under nodes that explicitly prohibit those claims."
    )

    # Warn if accuracy is below target
    if accuracy < 80.0:
        logger.warning(
            f"Accuracy {accuracy:.1f}% is below 80% target. "
            f"Consider implementing additional fixes (two-stage classification, few-shot examples)."
        )


@pytest.mark.parametrize("fact_text,acceptable_nodes,prohibited_nodes,context", PMF_TEST_CASES)
def test_prohibition_gate_enforcement(fact_text, acceptable_nodes, prohibited_nodes, context):
    """Test that the prohibition gate ALWAYS rejects prohibited nodes.

    This is a regression test for Task #2. The prohibition gate must
    programmatically enforce prohibited_claims, even when the LLM picks
    a prohibited node with "high" confidence.
    """
    result = classify_and_match_node(
        fact_text,
        session_id=None,
        document_context=context,
        use_fast_model=False,
    )

    classified_node = result.get("node_id")

    # MUST NOT violate prohibitions
    assert classified_node not in prohibited_nodes, (
        f"Prohibition gate failed: fact '{fact_text[:50]}...' was classified into "
        f"prohibited node {classified_node}. Prohibited nodes: {prohibited_nodes}. "
        f"The _check_prohibition_violation() gate should have rejected this."
    )


def test_document_context_is_used():
    """Test that document_context is actually being passed to the LLM.

    This is a regression test for Task #3. When classifying facts from a
    structured document, the document_context should be passed through to
    help disambiguate ambiguous facts like "improve manuscript quality"
    (which could mean writing assistance OR be part of a PMF analysis).
    """
    fact = "Job: Improve manuscript quality before submission"
    context = "PMF options analysis for EpistemicOS"

    # Classify WITH context
    result_with_context = classify_and_match_node(
        fact,
        session_id=None,
        document_context=context,
        use_fast_model=False,
    )

    # Classify WITHOUT context
    result_without_context = classify_and_match_node(
        fact,
        session_id=None,
        document_context=None,
        use_fast_model=False,
    )

    logger.info(
        f"\nDocument context test:\n"
        f"  Fact: {fact}\n"
        f"  With context: {result_with_context.get('node_id')} ({result_with_context.get('confidence')})\n"
        f"  Without context: {result_without_context.get('node_id')} ({result_without_context.get('confidence')})\n"
        f"  Context: {context}"
    )

    # With context, should NOT classify into BP.1.2.1 (Writing Assistance Exclusion)
    # Without context, might still do that (since "manuscript quality" sounds like writing)
    assert result_with_context.get("node_id") != "BP.1.2.1", (
        "With PMF context provided, the fact should NOT be classified as writing assistance. "
        "This suggests document_context is not being used by the LLM."
    )
