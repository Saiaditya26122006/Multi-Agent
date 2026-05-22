from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class OperationsOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="10")
    production_process: str = Field(..., min_length=50)
    cost_structure: dict = Field(..., description="fixed_costs, variable_costs, cogs_per_unit — all with source labels")
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
