"""
Compare two evaluation runs to measure improvement.

Usage:
    python evaluation/compare.py results/eval_run_A.json results/eval_run_B.json
"""

import json
import sys
from pathlib import Path


def load_run(filepath: str) -> list:
    with open(filepath) as f:
        return json.load(f)


def compare_runs(run_a: list, run_b: list):
    """Compare two evaluation runs and print delta."""
    print("\n" + "=" * 80)
    print("EVALUATION COMPARISON")
    print("=" * 80)

    # Match runs by idea_id
    a_by_id = {r["idea_id"]: r for r in run_a}
    b_by_id = {r["idea_id"]: r for r in run_b}

    common_ids = set(a_by_id.keys()) & set(b_by_id.keys())

    if not common_ids:
        print("No common test ideas between runs — cannot compare.")
        return

    total_delta_score = 0
    total_delta_tokens = 0
    total_delta_latency = 0

    for idea_id in sorted(common_ids):
        a = a_by_id[idea_id]
        b = b_by_id[idea_id]

        a_score = a.get("scores", {}).get("overall_score", 0)
        b_score = b.get("scores", {}).get("overall_score", 0)
        delta_score = b_score - a_score

        a_tokens = a.get("total_input_tokens", 0) + a.get("total_output_tokens", 0)
        b_tokens = b.get("total_input_tokens", 0) + b.get("total_output_tokens", 0)
        delta_tokens = b_tokens - a_tokens

        a_latency = a.get("total_latency_seconds", 0)
        b_latency = b.get("total_latency_seconds", 0)
        delta_latency = b_latency - a_latency

        total_delta_score += delta_score
        total_delta_tokens += delta_tokens
        total_delta_latency += delta_latency

        score_arrow = "↑" if delta_score > 0 else "↓" if delta_score < 0 else "="
        token_arrow = "↑" if delta_tokens > 0 else "↓" if delta_tokens < 0 else "="

        print(f"\n  {a.get('idea_name', idea_id)}")
        print(f"    Score: {a_score}/10 → {b_score}/10  ({score_arrow} {delta_score:+.1f})")
        print(f"    Tokens: {a_tokens:,} → {b_tokens:,}  ({token_arrow} {delta_tokens:+,})")
        print(f"    Latency: {a_latency:.1f}s → {b_latency:.1f}s  ({delta_latency:+.1f}s)")

        # Per-section comparison
        a_sections = a.get("scores", {}).get("section_scores", {})
        b_sections = b.get("scores", {}).get("section_scores", {})
        common_sections = set(a_sections.keys()) & set(b_sections.keys())

        if common_sections:
            improved = []
            regressed = []
            for sec in sorted(common_sections):
                a_sec_score = a_sections[sec].get("total", 0)
                b_sec_score = b_sections[sec].get("total", 0)
                if b_sec_score > a_sec_score:
                    improved.append(f"Section {sec} (+{b_sec_score - a_sec_score:.1f})")
                elif b_sec_score < a_sec_score:
                    regressed.append(f"Section {sec} ({b_sec_score - a_sec_score:.1f})")

            if improved:
                print(f"    Improved: {', '.join(improved)}")
            if regressed:
                print(f"    Regressed: {', '.join(regressed)}")

    # Summary
    n = len(common_ids)
    print(f"\n{'─' * 80}")
    print(f"  SUMMARY ({n} ideas compared)")
    print(f"    Avg score delta: {total_delta_score / n:+.1f}/10")
    print(f"    Avg token delta: {total_delta_tokens / n:+,.0f}")
    print(f"    Avg latency delta: {total_delta_latency / n:+.1f}s")

    if total_delta_score > 0:
        print(f"\n    VERDICT: Run B is BETTER (quality improved)")
    elif total_delta_score < 0:
        print(f"\n    VERDICT: Run B is WORSE (quality regressed)")
    else:
        print(f"\n    VERDICT: No significant change")

    if total_delta_tokens > 0 and total_delta_score > 0:
        efficiency = total_delta_score / (total_delta_tokens / 1000)
        print(f"    Efficiency: {efficiency:.2f} score points per 1K extra tokens")

    print(f"\n{'=' * 80}\n")


def main():
    if len(sys.argv) != 3:
        print("Usage: python evaluation/compare.py <run_a.json> <run_b.json>")
        print("  Compares run_b against run_a (run_a = baseline, run_b = new)")
        sys.exit(1)

    run_a = load_run(sys.argv[1])
    run_b = load_run(sys.argv[2])
    compare_runs(run_a, run_b)


if __name__ == "__main__":
    main()
