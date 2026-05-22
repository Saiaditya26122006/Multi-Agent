from pydantic import BaseModel, Field
from typing import List, Optional


class LaunchContingencyInput(BaseModel):
    task_id: str
    session_id: str
    revenue_assumptions: dict = Field(..., description="From Section 8")
    headcount_plan: dict = Field(..., description="From Section 11")
    break_even_analysis: dict = Field(..., description="From Section 12")
    probability_distribution: list = Field(..., description="From Section 12 SimPy")
    primary_risk_factor: str = Field(..., description="From Section 12")
    market_entry_strategy: str = Field(..., description="From Section 8")
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str
