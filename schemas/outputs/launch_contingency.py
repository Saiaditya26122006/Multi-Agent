from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Milestone(BaseModel):
    milestone: str
    target_date_months: int
    responsible: str
    success_metric: str
    dependencies: List[str] = Field(default=[])


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class LaunchContingencyOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="13")
    launch_programme: List[Milestone] = Field(..., min_length=3)
    prerequisite_conditions: List[str] = Field(..., min_length=2)
    capital_plan: str = Field(..., min_length=50)
    critical_path_item: str = Field(..., description="The single most important thing to get right first")
    contingency_scenarios: Optional[List[dict]] = None
    exit_conditions: Optional[str] = None
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
