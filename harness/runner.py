"""
Harness runner — validates all mock outputs against Pydantic schemas
and runs the SimPy financial simulation.
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.outputs.opportunity_analyst import OpportunityAnalystOutput
from schemas.outputs.environment_research import EnvironmentResearchOutput
from schemas.outputs.organisation_designer import OrganisationDesignerOutput
from schemas.outputs.swot_synthesizer import SWOTSynthesizerOutput
from schemas.outputs.marketing_strategy import MarketingStrategyOutput
from schemas.outputs.operations import OperationsOutput
from schemas.outputs.financial_modelling import FinancialModellingOutput
from schemas.outputs.launch_contingency import LaunchContingencyOutput
from schemas.outputs.summary_agent import SummaryAgentOutput
from simulation.financial_sim import run_simulation

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MOCKS_DIR = Path(__file__).parent / "mocks"

AGENT_SCHEMAS = {
    "opportunity_analyst": OpportunityAnalystOutput,
    "environment_research": EnvironmentResearchOutput,
    "organisation_designer": OrganisationDesignerOutput,
    "swot_synthesizer": SWOTSynthesizerOutput,
    "marketing_strategy": MarketingStrategyOutput,
    "operations": OperationsOutput,
    "financial_modelling": FinancialModellingOutput,
    "launch_contingency": LaunchContingencyOutput,
    "summary_agent": SummaryAgentOutput,
}


def validate_mock(agent_name: str, schema_class) -> bool:
    """Load mock JSON and validate against Pydantic schema."""
    mock_path = MOCKS_DIR / f"{agent_name}_output.json"

    if not mock_path.exists():
        logger.error("  FAIL  %s — mock file not found: %s", agent_name, mock_path)
        return False

    with open(mock_path, "r") as f:
        data = json.load(f)

    try:
        schema_class(**data)
        logger.info("  PASS  %s", agent_name)
        return True
    except Exception as e:
        logger.error("  FAIL  %s — %s", agent_name, e)
        return False


def test_simpy_simulation() -> bool:
    """Run SimPy with sample assumptions and verify output structure."""
    sample_assumptions = {
        "price_per_unit": 60000,
        "volume_year1": 8,
        "volume_year2": 22,
        "volume_year3": 45,
        "sales_cycle_months": 5,
        "churn_rate": 0.08,
        "conversion_rate": 0.01,
        "cac": 18000,
        "fixed_costs_monthly": 7500,
        "variable_cost_per_unit": 800,
        "headcount_cost_monthly": 26667,
        "initial_cash": 500000,
        "leads_per_month": 50,
    }

    try:
        result = run_simulation(sample_assumptions, num_runs=100)

        scenarios = result.get("probability_distribution", [])
        scenario_labels = {s["scenario"] for s in scenarios}

        if not {"P10", "P50", "P90"}.issubset(scenario_labels):
            logger.error("  FAIL  simpy — missing P10/P50/P90 scenarios. Got: %s", scenario_labels)
            return False

        if "primary_risk_factor" not in result:
            logger.error("  FAIL  simpy — missing primary_risk_factor")
            return False

        if result.get("runs_completed", 0) != 100:
            logger.error("  FAIL  simpy — expected 100 runs, got %s", result.get("runs_completed"))
            return False

        for scenario in scenarios:
            for key in ("year1_revenue", "year2_revenue", "year3_revenue"):
                if key not in scenario:
                    logger.error("  FAIL  simpy — scenario %s missing %s", scenario["scenario"], key)
                    return False

        logger.info("  PASS  simpy (100 runs, P10/P50/P90 present, risk factor: %s)",
                    result["primary_risk_factor"][:50])
        return True
    except Exception as e:
        logger.error("  FAIL  simpy — %s", e)
        return False


def main():
    logger.info("=" * 60)
    logger.info("HARNESS RUNNER — Schema Validation + SimPy Test")
    logger.info("=" * 60)
    logger.info("")
    logger.info("--- Schema Validations ---")

    results = {}
    for agent_name, schema_class in AGENT_SCHEMAS.items():
        results[agent_name] = validate_mock(agent_name, schema_class)

    logger.info("")
    logger.info("--- SimPy Simulation Test ---")
    results["simpy"] = test_simpy_simulation()

    logger.info("")
    logger.info("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed == total:
        logger.info("RESULT: ALL %d TESTS PASSED", total)
    else:
        failed = [k for k, v in results.items() if not v]
        logger.error("RESULT: %d/%d PASSED — FAILED: %s", passed, total, ", ".join(failed))

    logger.info("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
