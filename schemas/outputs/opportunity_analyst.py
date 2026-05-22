from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class OpportunityAnalystOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="1")
    opportunity_description: str = Field(..., min_length=50)
    competitive_strategy: str = Field(..., min_length=30)
    objectives: List[dict] = Field(..., description="List of quantified Year 1 objectives with metrics")
    icp_hypothesis: dict = Field(..., description="buyer_role, budget_process, decision_timeline, pain_points")
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[], description="Things the agent could not validate")
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
