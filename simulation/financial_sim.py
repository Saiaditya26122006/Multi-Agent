import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import simpy

logger = logging.getLogger(__name__)


@dataclass
class SimulationAssumptions:
    """Base assumptions fed into the simulation from upstream agents."""

    price_per_unit: float
    volume_year1: int
    volume_year2: int
    volume_year3: int
    sales_cycle_months: int = 3
    churn_rate: float = 0.12
    conversion_rate: float = 0.01
    cac: float = 500.0
    fixed_costs_monthly: float = 10000.0
    variable_cost_per_unit: float = 0.0
    headcount_cost_monthly: float = 20000.0
    initial_cash: float = 100000.0
    leads_per_month: int = 1000


@dataclass
class RunResult:
    """Result of a single 36-month simulation run."""

    monthly_revenue: List[float] = field(default_factory=list)
    monthly_cash: List[float] = field(default_factory=list)
    year1_revenue: float = 0.0
    year2_revenue: float = 0.0
    year3_revenue: float = 0.0
    break_even_month: Optional[int] = None
    cash_out_month: Optional[int] = None
    params_used: Dict[str, float] = field(default_factory=dict)


def _randomise_params(base: SimulationAssumptions) -> Dict[str, float]:
    """Apply random variation to key parameters for one simulation run."""
    sales_cycle = max(1, base.sales_cycle_months + random.randint(-2, 2))
    churn = np.clip(
        base.churn_rate + random.uniform(-0.07, 0.13),
        0.05, 0.25
    )
    conversion = np.clip(
        base.conversion_rate * random.uniform(0.5, 3.0),
        0.005, 0.03
    )
    cac = base.cac * random.uniform(0.7, 1.3)

    return {
        "sales_cycle_months": sales_cycle,
        "churn_rate": float(churn),
        "conversion_rate": float(conversion),
        "cac": float(cac),
    }


def _run_single_simulation(
    env: simpy.Environment,
    base: SimulationAssumptions,
    params: Dict[str, float],
    result: RunResult,
):
    """SimPy process: simulate 36 months of business operation."""
    cash = base.initial_cash
    active_customers = 0
    cumulative_revenue = 0.0
    monthly_revenues = []
    monthly_cash_positions = []
    break_even_found = False
    cash_out_found = False

    sales_cycle = int(params["sales_cycle_months"])
    churn_rate_monthly = params["churn_rate"] / 12.0
    conversion_rate = params["conversion_rate"]
    cac = params["cac"]

    yearly_volumes = [base.volume_year1, base.volume_year2, base.volume_year3]

    for month in range(36):
        year_idx = month // 12
        target_volume = yearly_volumes[min(year_idx, 2)]
        monthly_target = target_volume / 12.0

        if month >= sales_cycle:
            new_customers = int(base.leads_per_month * conversion_rate)
            new_customers = min(new_customers, int(monthly_target * 1.5))
        else:
            new_customers = 0

        churned = int(active_customers * churn_rate_monthly)
        active_customers = max(0, active_customers + new_customers - churned)

        revenue = active_customers * base.price_per_unit
        cogs = active_customers * base.variable_cost_per_unit
        acquisition_cost = new_customers * cac
        total_costs = (
            base.fixed_costs_monthly
            + base.headcount_cost_monthly
            + cogs
            + acquisition_cost
        )

        net_income = revenue - total_costs
        cash += net_income

        monthly_revenues.append(revenue)
        monthly_cash_positions.append(cash)
        cumulative_revenue += revenue

        if not break_even_found and net_income >= 0 and month > 0:
            result.break_even_month = month + 1
            break_even_found = True

        if not cash_out_found and cash <= 0:
            result.cash_out_month = month + 1
            cash_out_found = True

        yield env.timeout(1)

    result.monthly_revenue = monthly_revenues
    result.monthly_cash = monthly_cash_positions
    result.year1_revenue = sum(monthly_revenues[0:12])
    result.year2_revenue = sum(monthly_revenues[12:24])
    result.year3_revenue = sum(monthly_revenues[24:36])
    result.params_used = params


def run_simulation(
    assumptions: Dict,
    num_runs: int = 1000,
) -> Dict:
    """Run the full Monte Carlo simulation and return P10/P50/P90 results.

    Args:
        assumptions: Dict with keys matching SimulationAssumptions fields.
        num_runs: Number of simulation runs (default 1000).

    Returns:
        Dict with probability_distribution, primary_risk_factor, and metadata.
    """
    base = SimulationAssumptions(
        price_per_unit=float(assumptions.get("price_per_unit", 100)),
        volume_year1=int(assumptions.get("volume_year1", 100)),
        volume_year2=int(assumptions.get("volume_year2", 500)),
        volume_year3=int(assumptions.get("volume_year3", 1500)),
        sales_cycle_months=int(assumptions.get("sales_cycle_months", 3)),
        churn_rate=float(assumptions.get("churn_rate", 0.12)),
        conversion_rate=float(assumptions.get("conversion_rate", 0.01)),
        cac=float(assumptions.get("cac", 500)),
        fixed_costs_monthly=float(assumptions.get("fixed_costs_monthly", 10000)),
        variable_cost_per_unit=float(assumptions.get("variable_cost_per_unit", 0)),
        headcount_cost_monthly=float(assumptions.get("headcount_cost_monthly", 20000)),
        initial_cash=float(assumptions.get("initial_cash", 100000)),
        leads_per_month=int(assumptions.get("leads_per_month", 1000)),
    )

    results: List[RunResult] = []

    for i in range(num_runs):
        env = simpy.Environment()
        params = _randomise_params(base)
        run_result = RunResult()
        env.process(_run_single_simulation(env, base, params, run_result))
        env.run()
        results.append(run_result)

    logger.info("Simulation complete: %d runs", num_runs)

    year1_revenues = [r.year1_revenue for r in results]
    year2_revenues = [r.year2_revenue for r in results]
    year3_revenues = [r.year3_revenue for r in results]
    break_even_months = [r.break_even_month for r in results if r.break_even_month is not None]
    cash_out_months = [r.cash_out_month for r in results if r.cash_out_month is not None]

    def percentile_result(pct: int, label: str) -> Dict:
        idx = int(len(results) * pct / 100)
        sorted_y1 = sorted(year1_revenues)
        sorted_y2 = sorted(year2_revenues)
        sorted_y3 = sorted(year3_revenues)
        sorted_be = sorted(break_even_months) if break_even_months else [None]
        sorted_co = sorted(cash_out_months) if cash_out_months else [None]

        be_idx = min(idx, len(sorted_be) - 1) if sorted_be[0] is not None else 0
        co_idx = min(idx, len(sorted_co) - 1) if sorted_co[0] is not None else 0

        return {
            "scenario": label,
            "year1_revenue": round(sorted_y1[min(idx, len(sorted_y1) - 1)], 2),
            "year2_revenue": round(sorted_y2[min(idx, len(sorted_y2) - 1)], 2),
            "year3_revenue": round(sorted_y3[min(idx, len(sorted_y3) - 1)], 2),
            "break_even_month": sorted_be[be_idx] if sorted_be[0] is not None else None,
            "cash_out_month": sorted_co[co_idx] if sorted_co[0] is not None else None,
        }

    probability_distribution = [
        percentile_result(10, "P10"),
        percentile_result(50, "P50"),
        percentile_result(90, "P90"),
    ]

    primary_risk_factor = _identify_primary_risk(results)

    cash_out_rate = len(cash_out_months) / num_runs

    return {
        "probability_distribution": probability_distribution,
        "primary_risk_factor": primary_risk_factor,
        "runs_completed": num_runs,
        "cash_out_rate": round(cash_out_rate, 3),
        "median_break_even_month": int(np.median(break_even_months)) if break_even_months else None,
    }


def _identify_primary_risk(results: List[RunResult]) -> str:
    """Determine which randomised variable most correlates with failure."""
    failure_runs = [r for r in results if r.cash_out_month is not None]
    success_runs = [r for r in results if r.cash_out_month is None]

    if not failure_runs or not success_runs:
        return "churn_rate"

    variables = ["sales_cycle_months", "churn_rate", "conversion_rate", "cac"]
    max_diff = 0.0
    risk_var = "churn_rate"

    for var in variables:
        fail_mean = np.mean([r.params_used.get(var, 0) for r in failure_runs])
        success_mean = np.mean([r.params_used.get(var, 0) for r in success_runs])
        overall_mean = np.mean([r.params_used.get(var, 0) for r in results])

        if overall_mean == 0:
            continue

        normalised_diff = abs(fail_mean - success_mean) / overall_mean
        if normalised_diff > max_diff:
            max_diff = normalised_diff
            risk_var = var

    risk_descriptions = {
        "sales_cycle_months": "Sales cycle length — longer cycles delay revenue and increase cash burn risk",
        "churn_rate": "Customer churn — high churn prevents revenue compounding and increases CAC payback period",
        "conversion_rate": "Conversion rate — low conversion means insufficient customers to cover fixed costs",
        "cac": "Customer acquisition cost — high CAC extends payback period and accelerates cash burn",
    }

    return risk_descriptions.get(risk_var, risk_var)
