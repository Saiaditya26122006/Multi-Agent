from pydantic import BaseModel, Field
from typing import List, Optional


class FinancialModellingInput(BaseModel):
    task_id: str
    session_id: str
    revenue_assumptions: dict = Field(..., description="From Section 8 — price, volume, sales cycle")
    cac_assumptions: dict = Field(..., description="From Section 8")
    cost_structure: dict = Field(..., description="From Section 10 or Section 8 if Section 10 skipped")
    headcount_plan: dict = Field(..., description="From Section 11 — year 1, 2, 3 with cost estimates")
    business_type: str
    opportunity_description: str = Field(..., description="From Section 1")
    market_context: str = Field(..., description="From Section 3")
    simpy_runs: int = Field(default=1000)
    financial_skills: List[str] = Field(
        default=["three_statement_model", "dcf_model", "comps_analysis"],
        description="Which Claude financial skills to apply",
    )
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
