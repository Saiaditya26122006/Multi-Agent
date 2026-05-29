"""
Benchmark Scorecard — visual summary of system intelligence.

Prints a formatted scorecard showing all 10 dimensions,
current score, target score, and gap.

Usage:
    python evaluation/benchmark_scorecard.py results/benchmark_*.json
    python evaluation/benchmark_scorecard.py --latest
"""

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"

DIMENSION_META = {
    "reasoning_depth": {
        "label": "Reasoning Depth",
        "critique": "Agents fill templates, don't think",
        "target": 8.0,
        "measures": "Generic phrase density, causal markers, idea-specific refs",
        "fix": "Domain-specific reasoning protocols in SYSTEM_PROMPTs",
    },
    "ie_enforcement": {
        "label": "IE Step Enforcement",
        "critique": "4-step chain has no enforcement between steps",
        "target": 7.0,
        "measures": "Decomp→Produce coverage, Challenge→Revise resolution rate",
        "fix": "Parse constraints from each step, validate next step addresses them",
    },
    "communication_efficiency": {
        "label": "Communication Efficiency",
        "critique": "SPADE/XMPP adds 30s overhead, all msgs route through Mother",
        "target": 8.0,
        "measures": "Messages per section, relay ratio, failure rate",
        "fix": "Replace SPADE with direct async MessageBus",
    },
    "cross_section_consistency": {
        "label": "Cross-Section Consistency",
        "critique": "Agents don't validate against prior sections during production",
        "target": 9.0,
        "measures": "Revenue match, ICP drift, timeline conflicts, confidence chain",
        "fix": "Pre-check + post-audit in every agent before returning output",
    },
    "learning_effectiveness": {
        "label": "Learning Effectiveness",
        "critique": "Learning Engine logs events but doesn't extract patterns",
        "target": 7.0,
        "measures": "Pattern specificity, error recurrence, run-over-run improvement",
        "fix": "Root cause extraction, anti-pattern rules, prompt adaptation",
    },
    "fallback_quality": {
        "label": "Fallback Quality",
        "critique": "Fallback = hardcoded generic text passed as real data",
        "target": 8.0,
        "measures": "Marking clarity, confidence accuracy, downstream contamination",
        "fix": "Structured failure modes: retry-simple, partial, refuse",
    },
    "negotiation_capability": {
        "label": "Negotiation Capability",
        "critique": "Contradictions escalated to human instead of resolved by agents",
        "target": 7.0,
        "measures": "Agent-resolution rate, rounds to consensus, deadlock frequency",
        "fix": "Bounded 3-round negotiation protocol before escalation",
    },
    "agent_autonomy": {
        "label": "Agent Autonomy",
        "critique": "Agents are stateless functions with zero initiative",
        "target": 6.0,
        "measures": "Challenges initiated, tasks refused, unsolicited proposals, beliefs",
        "fix": "BDI belief store, conflict detection on incoming data",
    },
    "mother_coupling": {
        "label": "Mother Decoupling",
        "critique": "All intelligence in Mother (2500 lines), agents are dumb routers",
        "target": 6.0,
        "measures": "Mother message ratio, direct agent-to-agent ratio",
        "fix": "Split Mother into focused classes, enable direct agent queries",
    },
    "adaptive_pipeline": {
        "label": "Adaptive Pipeline",
        "critique": "Pipeline runs all sections even when idea is clearly doomed",
        "target": 8.0,
        "measures": "Early kill detection, sections after fatal signal, checkpoint hits",
        "fix": "Kill checkpoints after sections 1, 3, 12 with CEO confirm",
    },
}


def load_latest_benchmark() -> dict:
    """Load the most recent benchmark result."""
    files = sorted(RESULTS_DIR.glob("benchmark_*.json"), key=lambda f: f.stat().st_mtime)
    if not files:
        return {}
    with open(files[-1]) as f:
        return json.load(f)


def format_scorecard(benchmark_data: dict) -> str:
    """Format benchmark as a visual scorecard."""
    lines = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("  MULTI-AGENT SYSTEM INTELLIGENCE SCORECARD")
    lines.append("=" * 72)

    overall = benchmark_data.get("overall_score", 0)
    grade = benchmark_data.get("overall_grade", "?")
    lines.append(f"  Overall: {overall:.1f}/10 (Grade: {grade})")
    lines.append(f"  Run: {benchmark_data.get('run_id', 'N/A')[:8]}")
    lines.append(f"  Idea: {benchmark_data.get('test_idea', 'N/A')}")
    lines.append("-" * 72)
    lines.append("")

    dimensions = benchmark_data.get("dimensions", {})

    # Header
    lines.append(
        f"  {'Dimension':<28} {'Score':>5} {'Target':>6} "
        f"{'Gap':>5}  {'Status':<10}"
    )
    lines.append(f"  {'-'*28} {'-'*5} {'-'*6} {'-'*5}  {'-'*10}")

    # Sort by gap (worst first)
    sorted_dims = sorted(
        DIMENSION_META.items(),
        key=lambda x: (
            dimensions.get(x[0], {}).get("score", 0)
            - x[1]["target"]
        ),
    )

    total_gap = 0
    for dim_name, meta in sorted_dims:
        dim_data = dimensions.get(dim_name, {})
        score = dim_data.get("score", 0)
        target = meta["target"]
        gap = score - target

        total_gap += abs(gap) if gap < 0 else 0

        if score >= target:
            status = "PASS"
            bar = _score_bar(score, 10)
        elif score >= target - 2:
            status = "CLOSE"
            bar = _score_bar(score, 10)
        else:
            status = "FAIL"
            bar = _score_bar(score, 10)

        lines.append(
            f"  {meta['label']:<28} {score:>5.1f} {target:>6.1f} "
            f"{gap:>+5.1f}  {status:<10}"
        )

    lines.append("")
    lines.append(f"  Total gap to close: {total_gap:.1f} points")
    lines.append("")

    # Detailed breakdown
    lines.append("=" * 72)
    lines.append("  DIMENSION DETAILS")
    lines.append("=" * 72)

    for dim_name, meta in sorted_dims:
        dim_data = dimensions.get(dim_name, {})
        score = dim_data.get("score", 0)

        lines.append("")
        lines.append(f"  [{meta['label']}] {score:.1f}/10 (target: {meta['target']})")
        lines.append(f"  Critique: {meta['critique']}")
        lines.append(f"  Measures: {meta['measures']}")
        lines.append(f"  Fix: {meta['fix']}")

        evidence = dim_data.get("evidence", [])
        if evidence:
            lines.append(f"  Evidence:")
            for e in evidence[:3]:
                lines.append(f"    - {e}")

        recommendations = dim_data.get("recommendations", [])
        if recommendations:
            lines.append(f"  Next steps:")
            for r in recommendations[:2]:
                lines.append(f"    > {r}")

    lines.append("")
    lines.append("=" * 72)

    # Priority fix order
    lines.append("")
    lines.append("  PRIORITY FIX ORDER (by gap size):")
    priority = [
        (meta["label"], dimensions.get(name, {}).get("score", 0) - meta["target"])
        for name, meta in sorted_dims
    ]
    priority_sorted = sorted(priority, key=lambda x: x[1])

    for i, (label, gap) in enumerate(priority_sorted[:5], 1):
        if gap < 0:
            lines.append(f"    {i}. {label} (gap: {gap:+.1f})")

    lines.append("")
    return "\n".join(lines)


def _score_bar(score: float, max_score: float, width: int = 10) -> str:
    """Generate a simple text-based score bar."""
    filled = int(score / max_score * width)
    empty = width - filled
    return "[" + "#" * filled + "." * empty + "]"


def format_comparison_scorecard(before: dict, after: dict) -> str:
    """Side-by-side scorecard comparison."""
    lines = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("  BENCHMARK COMPARISON")
    lines.append("=" * 72)

    b_overall = before.get("overall_score", 0)
    a_overall = after.get("overall_score", 0)
    delta = a_overall - b_overall

    lines.append(
        f"  Before: {b_overall:.1f}/10 ({before.get('overall_grade', '?')})"
    )
    lines.append(
        f"  After:  {a_overall:.1f}/10 ({after.get('overall_grade', '?')})"
    )
    lines.append(f"  Delta:  {delta:+.1f}")
    lines.append("")
    lines.append(
        f"  {'Dimension':<28} {'Before':>6} {'After':>6} {'Delta':>6} {'Dir':<4}"
    )
    lines.append(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*6} {'-'*4}")

    b_dims = before.get("dimensions", {})
    a_dims = after.get("dimensions", {})

    all_dims = set(list(b_dims.keys()) + list(a_dims.keys()))

    for dim_name in sorted(all_dims):
        meta = DIMENSION_META.get(dim_name, {"label": dim_name})
        b_score = b_dims.get(dim_name, {}).get("score", 0)
        a_score = a_dims.get(dim_name, {}).get("score", 0)
        d = a_score - b_score

        if d > 0.5:
            direction = "^"
        elif d < -0.5:
            direction = "v"
        else:
            direction = "="

        lines.append(
            f"  {meta.get('label', dim_name):<28} "
            f"{b_score:>6.1f} {a_score:>6.1f} {d:>+6.1f} {direction:<4}"
        )

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--latest":
        data = load_latest_benchmark()
        if data:
            print(format_scorecard(data))
        else:
            print("No benchmark results found in evaluation/results/")

    elif len(sys.argv) > 1 and sys.argv[1] == "--compare" and len(sys.argv) == 4:
        with open(sys.argv[2]) as f:
            before = json.load(f)
        with open(sys.argv[3]) as f:
            after = json.load(f)
        print(format_comparison_scorecard(before, after))

    elif len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
        print(format_scorecard(data))

    else:
        # No args — try latest
        data = load_latest_benchmark()
        if data:
            print(format_scorecard(data))
        else:
            print("Usage:")
            print("  python benchmark_scorecard.py results/benchmark_*.json")
            print("  python benchmark_scorecard.py --latest")
            print("  python benchmark_scorecard.py --compare before.json after.json")
