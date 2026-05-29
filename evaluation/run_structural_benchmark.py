"""
Structural Benchmark Runner — evaluates system intelligence by analyzing code.

Instead of running the full pipeline (which requires live LLM calls), this
analyzes the code structure to score each dimension based on what capabilities
are present vs absent.

Usage:
    python evaluation/run_structural_benchmark.py
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "phase2"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def score_reasoning_depth() -> dict:
    """Score reasoning depth based on SYSTEM_PROMPT quality across agents."""
    agent_files = [
        "opportunity_analyst.py", "environment_research.py",
        "organisation_designer.py", "swot_synthesizer.py",
        "marketing_strategy.py", "operations.py",
        "financial_modelling.py", "launch_contingency.py",
        "summary_agent.py",
    ]

    scores = []
    evidence = []

    for filename in agent_files:
        filepath = AGENTS_DIR / filename
        if not filepath.exists():
            continue

        content = filepath.read_text()

        # Extract SYSTEM_PROMPT
        prompt_match = re.search(
            r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""',
            content, re.DOTALL,
        )
        if not prompt_match:
            prompt_match = re.search(
                r"SYSTEM_PROMPT\s*=\s*'''(.*?)'''",
                content, re.DOTALL,
            )
        if not prompt_match:
            scores.append(2.0)
            evidence.append(f"{filename}: No SYSTEM_PROMPT found")
            continue

        prompt = prompt_match.group(1)
        prompt_lower = prompt.lower()

        agent_score = 0.0

        # Check for reasoning framework (not just "return JSON")
        reasoning_markers = [
            "reasoning protocol", "reasoning framework",
            "think step", "evaluate", "before producing",
            "why now", "what stops", "what would",
            "assess", "determine", "validate",
        ]
        reasoning_count = sum(1 for m in reasoning_markers if m in prompt_lower)
        agent_score += min(3.0, reasoning_count * 0.6)

        # Check for anti-generic instructions
        anti_generic = [
            "never write", "never use", "do not write",
            "if you catch yourself", "avoid phrases like",
            "not filler", "not generic",
        ]
        anti_count = sum(1 for a in anti_generic if a in prompt_lower)
        agent_score += min(2.0, anti_count * 1.0)

        # Check for kill conditions
        kill_markers = [
            "kill", "fatal", "flag as", "stop and",
            "cannot proceed", "refuse",
        ]
        kill_count = sum(1 for k in kill_markers if k in prompt_lower)
        agent_score += min(2.0, kill_count * 1.0)

        # Check for domain-specific reasoning steps (numbered items)
        numbered_steps = len(re.findall(r'\d+\.', prompt))
        agent_score += min(2.5, numbered_steps * 0.25)

        # Check for specificity requirements
        specificity_markers = [
            "specific number", "exact", "cite",
            "source", "evidence", "because",
        ]
        spec_count = sum(1 for s in specificity_markers if s in prompt_lower)
        agent_score += min(1.0, spec_count * 0.25)

        agent_score = min(10.0, agent_score)
        scores.append(agent_score)

        if agent_score < 5.0:
            evidence.append(f"{filename}: Weak reasoning framework (score {agent_score:.1f})")

    avg = sum(scores) / len(scores) if scores else 0
    return {
        "score": round(avg, 2),
        "details": {"agents_scored": len(scores), "avg_per_agent": round(avg, 2)},
        "evidence": evidence[:5],
        "recommendations": (
            ["Rewrite remaining weak SYSTEM_PROMPTs with reasoning protocols"]
            if avg < 7 else []
        ),
    }


def score_ie_enforcement() -> dict:
    """Score IE enforcement based on code structure."""
    ie_path = AGENTS_DIR / "intelligence_engine.py"
    if not ie_path.exists():
        return {"score": 0, "evidence": ["intelligence_engine.py not found"]}

    content = ie_path.read_text()
    content_lower = content.lower()

    score = 0.0
    evidence = []

    # Check for judgment parsing
    if "_parse_judgments" in content:
        score += 2.0
    else:
        evidence.append("No judgment parsing between DECOMPOSE and PRODUCE")

    # Check for coverage verification
    if "_check_judgment_coverage" in content:
        score += 2.0
    else:
        evidence.append("No coverage check — PRODUCE can ignore DECOMPOSE")

    # Check for challenge parsing
    if "_parse_challenges" in content:
        score += 1.5
    else:
        evidence.append("No structured challenge parsing")

    # Check for resolution verification
    if "_check_challenge_resolution" in content:
        score += 2.0
    else:
        evidence.append("No verification that REVISE addressed CHALLENGE")

    # Check for confidence downgrade on failure
    if "confidence_score" in content and "unresolved" in content_lower:
        score += 1.0

    # Check for generic phrase detection
    if "_count_generic_phrases" in content or "generic_phrases" in content_lower:
        score += 0.5

    # Check for multiple revision passes
    if "_revise_targeted" in content:
        score += 1.0
    else:
        evidence.append("No second-pass revision for unresolved challenges")

    return {
        "score": round(min(10.0, score), 2),
        "details": {"enforcement_mechanisms": int(score / 1.5)},
        "evidence": evidence[:5],
        "recommendations": (
            ["Add missing enforcement mechanisms"] if score < 7 else []
        ),
    }


def score_communication_efficiency() -> dict:
    """Score communication layer based on architecture."""
    bus_path = AGENTS_DIR / "message_bus.py"
    base_path = AGENTS_DIR / "base_child_agent.py"

    score = 5.0  # Base score — system works
    evidence = []

    if bus_path.exists():
        bus_content = bus_path.read_text()
        if "class MessageBus" in bus_content:
            score += 2.0
        if "async" in bus_content and "await" in bus_content:
            score += 1.0
        if "broadcast" in bus_content:
            score += 0.5
    else:
        evidence.append("No MessageBus — still using SPADE/XMPP overhead")
        score -= 2.0

    if base_path.exists():
        base_content = base_path.read_text()
        # Check if still using SPADE
        if "from spade" in base_content.lower():
            evidence.append("base_child_agent still imports SPADE")
            score -= 1.0
        if "time.sleep" in base_content:
            evidence.append("Blocking sleep calls in agent code")
            score -= 0.5

    return {
        "score": round(max(0, min(10.0, score)), 2),
        "details": {"has_message_bus": bus_path.exists()},
        "evidence": evidence[:5],
        "recommendations": (
            ["Complete SPADE→MessageBus migration"] if score < 7 else []
        ),
    }


def score_cross_section_consistency() -> dict:
    """Score cross-section awareness mechanisms."""
    base_path = AGENTS_DIR / "base_child_agent.py"

    score = 3.0  # Base — cross_context is passed to IE
    evidence = []

    if base_path.exists():
        content = base_path.read_text()

        if "_pre_check_consistency" in content:
            score += 2.5
        else:
            evidence.append("No pre-production consistency check")

        if "_post_audit_consistency" in content:
            score += 2.5
        else:
            evidence.append("No post-production self-audit")

        if "constraints" in content.lower() and "prior sections" in content.lower():
            score += 1.0

        if "contradiction" in content.lower() or "divergence" in content.lower():
            score += 1.0

    return {
        "score": round(min(10.0, score), 2),
        "details": {"has_pre_check": "_pre_check" in (base_path.read_text() if base_path.exists() else "")},
        "evidence": evidence[:5],
        "recommendations": (
            ["Add per-agent consistency checks"] if score < 7 else []
        ),
    }


def score_learning_effectiveness() -> dict:
    """Score learning engine capabilities."""
    le_path = AGENTS_DIR / "learning_engine.py"

    if not le_path.exists():
        return {"score": 0, "evidence": ["learning_engine.py not found"]}

    content = le_path.read_text()
    content_lower = content.lower()

    score = 2.0  # Base — records events
    evidence = []

    if "root_cause" in content_lower:
        score += 2.0
    else:
        evidence.append("No root cause extraction")

    if "anti_pattern" in content_lower:
        score += 1.5
    else:
        evidence.append("No anti-pattern tracking")

    if "positive_pattern" in content_lower:
        score += 1.0

    if "recurring" in content_lower and "error" in content_lower:
        score += 1.0
    else:
        evidence.append("No recurring error detection")

    if "prompt_adjustment" in content_lower or "suggest" in content_lower:
        score += 1.0

    if "improvement_trend" in content_lower or "run_score" in content_lower:
        score += 1.0
    else:
        evidence.append("No run-over-run improvement tracking")

    if "_extract_pattern" in content:
        score += 0.5

    return {
        "score": round(min(10.0, score), 2),
        "details": {"has_pattern_extraction": "extract_pattern" in content_lower},
        "evidence": evidence[:5],
        "recommendations": (
            ["Add LLM-powered root cause analysis for deeper patterns"]
            if score < 7 else []
        ),
    }


def score_fallback_quality() -> dict:
    """Score fallback strategy."""
    base_path = AGENTS_DIR / "base_child_agent.py"

    if not base_path.exists():
        return {"score": 0, "evidence": ["base_child_agent.py not found"]}

    content = base_path.read_text()
    content_lower = content.lower()

    score = 2.0  # Base — has fallback
    evidence = []

    if "_handle_llm_failure" in content:
        score += 2.0
    else:
        evidence.append("No structured failure handling — uses template dump")

    if "simplified_retry" in content_lower or "minimal_prompt" in content_lower:
        score += 1.5
    else:
        evidence.append("No simplified retry strategy")

    if "_derive_from_inputs" in content:
        score += 1.5
    else:
        evidence.append("No partial derivation from inputs")

    if "refused" in content_lower or "refuse" in content_lower:
        score += 1.5
    else:
        evidence.append("Agent cannot refuse impossible tasks")

    if "generation_mode" in content_lower:
        score += 1.0

    if "_missing_fields" in content:
        score += 0.5

    return {
        "score": round(min(10.0, score), 2),
        "details": {"has_structured_failure": "_handle_llm_failure" in content},
        "evidence": evidence[:5],
        "recommendations": (
            ["Implement per-agent _derive_from_inputs overrides"]
            if score < 8 else []
        ),
    }


def score_negotiation_capability() -> dict:
    """Score agent-to-agent negotiation."""
    neg_path = AGENTS_DIR / "negotiation.py"

    if not neg_path.exists():
        return {
            "score": 1.0,
            "evidence": ["No negotiation protocol — all contradictions escalate to human"],
            "recommendations": ["Implement bounded negotiation protocol"],
        }

    content = neg_path.read_text()
    content_lower = content.lower()

    score = 3.0  # Base — file exists
    evidence = []

    if "class NegotiationRound" in content or "class NegotiationManager" in content:
        score += 2.0

    if "max_rounds" in content_lower:
        score += 1.0

    if "consensus" in content_lower and "deadlock" in content_lower:
        score += 1.5

    if "compromise" in content_lower:
        score += 1.0

    if "should_negotiate" in content:
        score += 1.0

    if "evidence" in content_lower and "counter" in content_lower:
        score += 0.5

    return {
        "score": round(min(10.0, score), 2),
        "details": {"has_negotiation": True},
        "evidence": evidence[:5],
        "recommendations": (
            ["Wire negotiation into Mother Agent conflict handling"]
            if score < 8 else []
        ),
    }


def score_agent_autonomy() -> dict:
    """Score agent autonomy / BDI capabilities."""
    beliefs_path = AGENTS_DIR / "agent_beliefs.py"
    base_path = AGENTS_DIR / "base_child_agent.py"

    score = 1.0  # Base — agents can accept/refuse proposals
    evidence = []

    if beliefs_path.exists():
        beliefs_content = beliefs_path.read_text()
        if "class AgentBeliefStore" in beliefs_content:
            score += 2.5
        if "challenge_belief" in beliefs_content:
            score += 1.5
        if "get_conflicts_with" in beliefs_content:
            score += 1.5
        if "get_beliefs_for_prompt" in beliefs_content:
            score += 1.0
    else:
        evidence.append("No belief system — agents are stateless functions")
        return {
            "score": 1.0,
            "details": {"has_beliefs": False},
            "evidence": evidence,
            "recommendations": ["Implement BDI belief layer per agent"],
        }

    if base_path.exists():
        base_content = base_path.read_text()
        if "beliefs" in base_content.lower():
            score += 1.0
        if "_propose_revision" in base_content:
            score += 1.0

    return {
        "score": round(min(10.0, score), 2),
        "details": {"has_beliefs": True},
        "evidence": evidence[:5],
        "recommendations": (
            ["Integrate beliefs into base_child_agent handle_request"]
            if score < 7 else []
        ),
    }


def score_mother_coupling() -> dict:
    """Score Mother Agent decoupling."""
    mother_path = AGENTS_DIR / "mother_agent.py"

    if not mother_path.exists():
        return {"score": 5.0, "evidence": ["mother_agent.py not found"]}

    content = mother_path.read_text()
    line_count = content.count("\n")

    score = 5.0
    evidence = []

    # Deductions for god-object size
    if line_count > 2000:
        score -= 2.0
        evidence.append(f"Mother Agent is {line_count} lines (god object)")
    elif line_count > 1000:
        score -= 1.0
        evidence.append(f"Mother Agent is {line_count} lines (large)")

    # Check for split architecture
    split_files = [
        "pipeline_orchestrator.py", "quality_gate.py",
        "coherence_auditor.py", "conflict_resolver.py",
        "delivery_manager.py",
    ]
    existing_splits = [f for f in split_files if (AGENTS_DIR / f).exists()]
    score += len(existing_splits) * 0.5

    # Check for direct agent-to-agent communication
    if "direct" in content.lower() and "agent" in content.lower():
        score += 0.5

    # Check for pipeline_checkpoints integration
    if (AGENTS_DIR / "pipeline_checkpoints.py").exists():
        score += 1.0

    return {
        "score": round(max(0, min(10.0, score)), 2),
        "details": {
            "mother_lines": line_count,
            "split_files": existing_splits,
        },
        "evidence": evidence[:5],
        "recommendations": (
            ["Split Mother Agent into focused subsystems"]
            if line_count > 1500 else []
        ),
    }


def score_adaptive_pipeline() -> dict:
    """Score adaptive pipeline capabilities."""
    checkpoint_path = AGENTS_DIR / "pipeline_checkpoints.py"

    if not checkpoint_path.exists():
        return {
            "score": 1.0,
            "evidence": ["No kill checkpoints — pipeline runs all sections blindly"],
            "recommendations": ["Add checkpoints after sections 1, 3, 12"],
        }

    content = checkpoint_path.read_text()
    content_lower = content.lower()

    score = 4.0  # Base — file exists with logic
    evidence = []

    if "kill_checkpoints" in content_lower:
        score += 1.5

    checkpoint_sections = re.findall(r'"(\d+)"', content[:500])
    score += min(2.0, len(set(checkpoint_sections)) * 0.7)

    if "should_continue_pipeline" in content:
        score += 1.5

    if "compound" in content_lower or "prior_warnings" in content_lower:
        score += 1.0

    if "severity" in content_lower:
        score += 0.5

    return {
        "score": round(min(10.0, score), 2),
        "details": {"checkpoint_sections": checkpoint_sections},
        "evidence": evidence[:5],
        "recommendations": (
            ["Wire checkpoints into Mother Agent pipeline execution"]
            if score < 8 else []
        ),
    }


def run_structural_benchmark() -> dict:
    """Run all structural benchmark dimensions."""
    dimensions = {
        "reasoning_depth": score_reasoning_depth(),
        "ie_enforcement": score_ie_enforcement(),
        "communication_efficiency": score_communication_efficiency(),
        "cross_section_consistency": score_cross_section_consistency(),
        "learning_effectiveness": score_learning_effectiveness(),
        "fallback_quality": score_fallback_quality(),
        "negotiation_capability": score_negotiation_capability(),
        "agent_autonomy": score_agent_autonomy(),
        "mother_coupling": score_mother_coupling(),
        "adaptive_pipeline": score_adaptive_pipeline(),
    }

    # Compute weighted overall
    weights = {
        "reasoning_depth": 0.20,
        "ie_enforcement": 0.10,
        "communication_efficiency": 0.05,
        "cross_section_consistency": 0.15,
        "learning_effectiveness": 0.10,
        "fallback_quality": 0.05,
        "negotiation_capability": 0.10,
        "agent_autonomy": 0.10,
        "mother_coupling": 0.05,
        "adaptive_pipeline": 0.10,
    }

    weighted_sum = sum(
        dimensions[dim]["score"] * weights.get(dim, 0.05)
        for dim in dimensions
    )
    total_weight = sum(weights.values())
    overall = weighted_sum / total_weight

    # Grade
    if overall >= 9.0:
        grade = "A+"
    elif overall >= 8.0:
        grade = "A"
    elif overall >= 7.0:
        grade = "B"
    elif overall >= 6.0:
        grade = "C"
    elif overall >= 5.0:
        grade = "D"
    else:
        grade = "F"

    result = {
        "run_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_idea": "structural_analysis",
        "benchmark_type": "structural",
        "overall_score": round(overall, 2),
        "overall_grade": grade,
        "dimensions": dimensions,
    }

    # Save
    filename = f"structural_benchmark_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    output_path = RESULTS_DIR / filename
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def print_scorecard(result: dict) -> None:
    """Print a formatted scorecard."""
    print()
    print("=" * 72)
    print("  STRUCTURAL INTELLIGENCE BENCHMARK")
    print("=" * 72)
    print(f"  Overall: {result['overall_score']:.1f}/10 (Grade: {result['overall_grade']})")
    print(f"  Type: Code structure analysis (no LLM calls)")
    print("-" * 72)
    print()
    print(f"  {'Dimension':<30} {'Score':>5} {'Target':>6} {'Gap':>5}  {'Status':<8}")
    print(f"  {'-'*30} {'-'*5} {'-'*6} {'-'*5}  {'-'*8}")

    targets = {
        "reasoning_depth": 8.0,
        "ie_enforcement": 7.0,
        "communication_efficiency": 8.0,
        "cross_section_consistency": 9.0,
        "learning_effectiveness": 7.0,
        "fallback_quality": 8.0,
        "negotiation_capability": 7.0,
        "agent_autonomy": 6.0,
        "mother_coupling": 6.0,
        "adaptive_pipeline": 8.0,
    }

    labels = {
        "reasoning_depth": "Reasoning Depth",
        "ie_enforcement": "IE Step Enforcement",
        "communication_efficiency": "Communication Efficiency",
        "cross_section_consistency": "Cross-Section Consistency",
        "learning_effectiveness": "Learning Effectiveness",
        "fallback_quality": "Fallback Quality",
        "negotiation_capability": "Negotiation Capability",
        "agent_autonomy": "Agent Autonomy",
        "mother_coupling": "Mother Decoupling",
        "adaptive_pipeline": "Adaptive Pipeline",
    }

    total_gap = 0
    for dim_name, dim_data in sorted(
        result["dimensions"].items(),
        key=lambda x: x[1]["score"] - targets.get(x[0], 7.0),
    ):
        score = dim_data["score"]
        target = targets.get(dim_name, 7.0)
        gap = score - target
        total_gap += abs(gap) if gap < 0 else 0

        if score >= target:
            status = "PASS"
        elif score >= target - 2:
            status = "CLOSE"
        else:
            status = "FAIL"

        label = labels.get(dim_name, dim_name)
        print(f"  {label:<30} {score:>5.1f} {target:>6.1f} {gap:>+5.1f}  {status:<8}")

    print()
    print(f"  Total gap to close: {total_gap:.1f} points")
    print()

    # Print evidence for failing dimensions
    print("=" * 72)
    print("  KEY FINDINGS")
    print("=" * 72)
    for dim_name, dim_data in result["dimensions"].items():
        target = targets.get(dim_name, 7.0)
        if dim_data["score"] < target:
            label = labels.get(dim_name, dim_name)
            print(f"\n  [{label}] {dim_data['score']:.1f}/{target:.1f}")
            for e in dim_data.get("evidence", [])[:2]:
                print(f"    - {e}")
            for r in dim_data.get("recommendations", [])[:1]:
                print(f"    > {r}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    result = run_structural_benchmark()
    print_scorecard(result)
