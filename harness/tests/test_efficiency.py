"""
Pipeline efficiency report — measures agent response times, cost,
retry rates, negotiation frequency, and simulation uncertainty.
"""
import logging
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from memory.supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

ROSTER_PATH = Path(__file__).parent.parent.parent / "config" / "phase2" / "agent_roster.yaml"

SONNET_INPUT_COST_PER_M = 3.0
SONNET_OUTPUT_COST_PER_M = 15.0
HAIKU_INPUT_COST_PER_M = 0.25
HAIKU_OUTPUT_COST_PER_M = 1.25

TARGETS = {
    "avg_response_seconds": {"pass": 60, "warn": 120},
    "retry_rate_pct": {"pass": 10, "warn": 25},
    "negotiation_pct": {"pass": 20, "warn": 40},
    "cost_usd": {"pass": 2.0, "warn": 5.0},
    "uncertainty_ratio": {"pass": 5.0, "warn": 10.0},
}


def load_roster() -> dict:
    with open(ROSTER_PATH, "r") as f:
        return yaml.safe_load(f)


def get_model_for_owner(owner: str, roster: dict) -> str:
    agent_config = roster.get("agents", {}).get(owner, {})
    return agent_config.get("model", "claude-haiku")


def classify(metric_name: str, value: float) -> str:
    target = TARGETS.get(metric_name, {})
    if not target:
        return "INFO"
    if value <= target["pass"]:
        return "PASS"
    elif value <= target["warn"]:
        return "WARN"
    return "FAIL"


def get_pipeline_run_id(provided_id: Optional[str]) -> Optional[str]:
    if provided_id:
        return provided_id
    result = supabase.table("pipeline_runs") \
        .select("id") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if result.data:
        return result.data[0]["id"]
    return None


def report_response_times(run_id: str, roster: dict) -> Optional[float]:
    result = supabase.table("task_readiness") \
        .select("owner, started_at, completed_at") \
        .eq("pipeline_run_id", run_id) \
        .eq("status", "complete") \
        .execute()

    if not result.data:
        logger.info("  No completed tasks found")
        return None

    agent_times: dict = {}
    for row in result.data:
        owner = row.get("owner", "unknown")
        started = row.get("started_at")
        completed = row.get("completed_at")
        if not started or not completed:
            continue
        start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        duration = (end_dt - start_dt).total_seconds()
        agent_times.setdefault(owner, []).append(duration)

    if not agent_times:
        logger.info("  No timing data available")
        return None

    all_durations = []
    for owner, durations in sorted(agent_times.items()):
        avg = sum(durations) / len(durations)
        mn = min(durations)
        mx = max(durations)
        all_durations.extend(durations)
        model = get_model_for_owner(owner, roster)
        logger.info("    %-25s avg=%.1fs  min=%.1fs  max=%.1fs  [%s]", owner, avg, mn, mx, model)

    overall_avg = sum(all_durations) / len(all_durations)
    return overall_avg


def report_cost(run_id: str, roster: dict) -> Optional[float]:
    result = supabase.table("task_readiness") \
        .select("owner, output_data") \
        .eq("pipeline_run_id", run_id) \
        .eq("status", "complete") \
        .execute()

    if not result.data:
        logger.info("  No completed tasks found")
        return None

    total_cost = 0.0
    total_input = 0
    total_output = 0

    for row in result.data:
        owner = row.get("owner", "unknown")
        output = row.get("output_data", {})
        if not isinstance(output, dict):
            continue

        input_tokens = output.get("input_tokens", 0)
        output_tokens = output.get("output_tokens", 0)
        total_input += input_tokens
        total_output += output_tokens

        model = get_model_for_owner(owner, roster)
        if "sonnet" in model:
            cost = (input_tokens / 1_000_000 * SONNET_INPUT_COST_PER_M +
                    output_tokens / 1_000_000 * SONNET_OUTPUT_COST_PER_M)
        else:
            cost = (input_tokens / 1_000_000 * HAIKU_INPUT_COST_PER_M +
                    output_tokens / 1_000_000 * HAIKU_OUTPUT_COST_PER_M)
        total_cost += cost

    logger.info("    Total input tokens:  %d", total_input)
    logger.info("    Total output tokens: %d", total_output)
    logger.info("    Estimated cost:      $%.4f", total_cost)
    return total_cost


def report_retry_rate(run_id: str) -> Optional[float]:
    result = supabase.table("task_readiness") \
        .select("id, version") \
        .eq("pipeline_run_id", run_id) \
        .execute()

    if not result.data:
        logger.info("  No tasks found")
        return None

    total = len(result.data)
    retried = sum(1 for r in result.data if (r.get("version") or 1) > 1)
    rate = (retried / total) * 100 if total > 0 else 0.0
    logger.info("    Total tasks: %d, Retried: %d, Rate: %.1f%%", total, retried, rate)
    return rate


def report_negotiation(run_id: str) -> Optional[float]:
    result = supabase.table("agent_messages") \
        .select("performative") \
        .eq("pipeline_run_id", run_id) \
        .execute()

    if not result.data:
        logger.info("  No agent messages found")
        return None

    total_inform = sum(1 for r in result.data if r.get("performative") == "inform")
    total_propose = sum(1 for r in result.data if r.get("performative") == "propose")
    total_refuse = sum(1 for r in result.data if r.get("performative") == "refuse")

    negotiation_msgs = total_propose + total_refuse
    pct = (negotiation_msgs / total_inform * 100) if total_inform > 0 else 0.0
    logger.info("    Inform: %d, Propose: %d, Refuse: %d", total_inform, total_propose, total_refuse)
    logger.info("    Negotiation frequency: %.1f%%", pct)
    return pct


def report_simpy_uncertainty(run_id: str) -> Optional[float]:
    result = supabase.table("bp_section_content") \
        .select("content") \
        .eq("pipeline_run_id", run_id) \
        .eq("section_number", "12") \
        .limit(1) \
        .execute()

    if not result.data:
        logger.info("  No financial section found")
        return None

    content = result.data[0].get("content", {})
    if not isinstance(content, dict):
        logger.info("  Financial content is not a dict")
        return None

    prob_dist = content.get("probability_distribution", [])
    if not prob_dist:
        logger.info("  No probability_distribution in financial output")
        return None

    p10_rev = None
    p90_rev = None
    for scenario in prob_dist:
        if scenario.get("scenario") == "P10":
            p10_rev = scenario.get("year1_revenue", 0)
        elif scenario.get("scenario") == "P90":
            p90_rev = scenario.get("year1_revenue", 0)

    if not p10_rev or not p90_rev:
        logger.info("  Missing P10 or P90 revenue data")
        return None

    ratio = p90_rev / p10_rev if p10_rev > 0 else 999.0
    logger.info("    P10 Year 1 revenue: $%.0f", p10_rev)
    logger.info("    P90 Year 1 revenue: $%.0f", p90_rev)
    logger.info("    P90/P10 ratio:      %.1fx", ratio)

    if ratio > 5.0:
        logger.info("    ⚠ High uncertainty — spread exceeds 5x")

    return ratio


def main():
    run_id_arg = sys.argv[1] if len(sys.argv) > 1 else None
    roster = load_roster()

    logger.info("=" * 60)
    logger.info("PIPELINE EFFICIENCY REPORT")
    logger.info("=" * 60)

    run_id = get_pipeline_run_id(run_id_arg)
    if not run_id:
        logger.info("")
        logger.info("No pipeline runs found in database.")
        logger.info("This is expected if no pipeline has been executed yet.")
        logger.info("")
        logger.info("Report structure verified — ready for real data.")
        logger.info("=" * 60)
        sys.exit(0)

    logger.info("Pipeline run: %s", run_id)
    logger.info("")

    results = {}

    logger.info("--- 1. Agent Response Times ---")
    avg_response = report_response_times(run_id, roster)
    results["avg_response_seconds"] = avg_response
    logger.info("")

    logger.info("--- 2. Pipeline Cost ---")
    cost = report_cost(run_id, roster)
    results["cost_usd"] = cost
    logger.info("")

    logger.info("--- 3. Retry Rate ---")
    retry_rate = report_retry_rate(run_id)
    results["retry_rate_pct"] = retry_rate
    logger.info("")

    logger.info("--- 4. Negotiation Frequency ---")
    negotiation_pct = report_negotiation(run_id)
    results["negotiation_pct"] = negotiation_pct
    logger.info("")

    logger.info("--- 5. SimPy Simulation Uncertainty ---")
    uncertainty = report_simpy_uncertainty(run_id)
    results["uncertainty_ratio"] = uncertainty
    logger.info("")

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    for metric, value in results.items():
        if value is None:
            status = "N/A"
            display = "no data"
        else:
            status = classify(metric, value)
            if "pct" in metric:
                display = f"{value:.1f}%"
            elif "cost" in metric:
                display = f"${value:.4f}"
            elif "ratio" in metric:
                display = f"{value:.1f}x"
            else:
                display = f"{value:.1f}s"
        logger.info("  [%4s]  %-25s %s", status, metric, display)

    logger.info("")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
