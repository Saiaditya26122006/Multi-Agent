from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict, Any


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class CostStructure(BaseModel):
    """Structured cost breakdown (replaces loose dict)"""
    fixed_costs_monthly: float = Field(..., ge=0, description="Fixed costs per month (rent, insurance, software, salaries for non-production roles)")
    cogs_per_unit: float = Field(..., ge=0, description="Cost of goods sold per unit (direct materials, labor)")
    variable_costs_per_unit: Optional[float] = Field(default=0, ge=0, description="Other variable costs per unit (packaging, shipping)")
    initial_cash: float = Field(..., gt=0, description="Starting capital required")
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"] = Field(...)
    confidence: Literal["high", "medium", "low"] = Field(...)


class OperationsOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="10")
    production_process: str = Field(..., min_length=50)

    cost_structure: CostStructure = Field(
        ...,
        description="Structured cost breakdown for Financial model consumption"
    )

    capacity_plan: str = Field(..., description="Production or delivery capacity and scalability")
    supplier_strategy: Optional[str] = None
    rd_plan: Optional[str] = None
    ip_analysis: Optional[str] = None
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
