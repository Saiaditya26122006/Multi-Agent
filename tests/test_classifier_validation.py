"""
Validation test suite - UNSEEN data only.
These facts are NEVER used as few-shot examples in the classifier.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from web.handlers.feed_handler import classify_and_match_node

logging.basicConfig(level=logging.WARNING)

# VALIDATION SET - 20 unseen cases covering all patterns
VALIDATION_CASES = [
    # PMF evidence patterns (4 cases)
    ("PMF means customers actively seek us out without marketing", ["BP.10.3.1", "BP.10.3.2"], "PMF framework"),
    ("Positive feedback alone doesn't indicate product-market fit", ["BP.10.3.8"], "PMF standards"),
    ("Evidence of PMF: 40% organic growth quarter over quarter", ["BP.10.3.1", "BP.10.1.8", "BP.10.3.3"], "PMF metrics"),
    ("Single customer testimonial is insufficient for PMF claim", ["BP.10.3.8"], "PMF criteria"),

    # JTBD and problem statements (4 cases)
    ("User job: Validate research methodology before publication", ["BP.2.1.1", "BP.6.1.3"], "Product requirements"),
    ("Customer needs to ensure manuscript meets journal standards", ["BP.2.1.1"], "User research"),
    ("Researcher wants to improve thesis quality systematically", ["BP.2.1.1"], "Problem definition"),
    ("Task: Detect plagiarism in academic submissions", ["BP.1.1.4", "BP.1.2.4"], "Product feature spec"),

    # Urgency and demand (3 cases)
    ("Dean emphasized urgent need for quality control tools", ["BP.2.3", "BP.2.3.3", "BP.2.3.4"], "Customer interview"),
    ("University leadership considers this a priority initiative", ["BP.2.3", "BP.2.3.3", "BP.2.3.4"], "Market research"),
    ("Department faces immediate pressure to improve standards", ["BP.2.3", "BP.2.3.3", "BP.2.3.4"], "Demand signal"),

    # Workflow and product (3 cases)
    ("Product supports peer review workflow with three approval stages", ["BP.1.3"], "Product spec"),
    ("Workflow: Submit → Review → Approve → Archive", ["BP.1.3"], "System design"),
    ("Diagnostic output: Quality score and improvement recommendations", ["BP.1.1.5", "BP.1.1.4"], "Output spec"),

    # Business model and market (3 cases)
    ("Revenue model: Annual subscription at $5000 per institution", ["BP.9.2.1", "BP.9.2.2", "BP.9.5.1", "BP.9.1"], "Business model"),
    ("Target customers: Research universities in North America", ["BP.4.1.1", "BP.4.1.2", "BP.4.1.5", "BP.3.1"], "Market definition"),
    ("Pricing: Tiered based on manuscript volume", ["BP.9.2.1", "BP.9.2.2", "BP.6.4.4", "BP.9.2.3"], "Pricing strategy"),

    # Risk and constraints (3 cases)
    ("Main risk: Competing tools have stronger brand recognition", ["BP.8.7.1", "BP.8.5.4", "BP.8.6.1", "BP.8.7", "BP.8.8.1", "BP.8.1"], "Risk analysis"),
    ("Constraint: Must integrate with existing university IT systems", ["BP.7.3", "BP.7.6.2", "BP.7.6.1"], "Technical requirements"),
    ("Assumption: Institutions will pay for quality improvement tools", ["BP.2.1.6", "BP.2.5.5", "BP.9.2.3", "BP.6.4.4"], "Business assumptions"),
]


def run_validation():
    """Run classifier on validation set and report accuracy."""
    correct = 0
    total = len(VALIDATION_CASES)
    results = []

    for fact, acceptable, context in VALIDATION_CASES:
        result = classify_and_match_node(
            fact,
            session_id=None,
            document_context=context,
            use_fast_model=False
        )
        node_id = result.get("node_id")
        confidence = result.get("confidence")

        is_correct = node_id in acceptable if node_id else False
        if is_correct:
            correct += 1

        results.append({
            "fact": fact[:60],
            "expected": acceptable,
            "actual": node_id,
            "confidence": confidence,
            "correct": is_correct,
        })

    accuracy = (correct / total) * 100
    return accuracy, results, correct, total


if __name__ == "__main__":
    print("="*80)
    print("VALIDATION SET ACCURACY (UNSEEN DATA ONLY)")
    print("="*80)

    accuracy, results, correct, total = run_validation()

    # Show results
    for r in results:
        status = "✅" if r["correct"] else "❌"
        print(f"{status} {r['fact']}...")
        print(f"   Expected: {r['expected']}, Got: {r['actual']} [{r['confidence']}]")

    print("\n" + "="*80)
    print(f"ACCURACY: {correct}/{total} = {accuracy:.1f}%")
    print(f"TARGET: 90%")
    print(f"GAP: {90 - accuracy:.1f} percentage points")
    print("="*80)
