from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class FinancialAssumption(BaseModel):
    name: str
    value: str
    label: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source: Optional[str] = None


class ScenarioResult(BaseModel):
    scenario: Literal["P10", "P50", "P90"]
    year1_revenue: float
    year2_revenue: float
    year3_revenue: float
    break_even_month: Optional[int] = None
    cash_out_month: Optional[int] = None


class FinancialModellingOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="12")
    three_statement_model: dict = Field(
        ...,
        description="pl_monthly_year1, pl_annual_years2_3, balance_sheet, cash_flow",
    )
    break_even_analysis: dict = Field(
        ...,
        description="baseline_month, optimistic_month, pessimistic_month, units_required",
    )
    probability_distribution: List[ScenarioResult] = Field(
        ...,
        description="SimPy outputs — P10 pessimistic, P50 most likely, P90 optimistic",
    )
    primary_risk_factor: str = Field(..., description="Top risk from simulation analysis")
    risk_mitigation_actions: List[str] = Field(..., min_length=2)
    dcf_valuation: Optional[dict] = Field(None, description="Conditional — only if traction assumptions support it")
    comps_table: Optional[dict] = Field(None, description="Comparable company benchmarks if available")
    assumption_log: List[FinancialAssumption]
    simpy_runs_completed: int
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    financial_skills_applied: List[str]
    input_tokens: int
    output_tokens: int
