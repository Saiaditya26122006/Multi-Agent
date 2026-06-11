from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class DevelopmentStage(BaseModel):
    stage_name: str
    description: str = Field(..., min_length=30)
    duration_months: int = Field(..., ge=1, le=60)
    cost_estimate: str
    success_criteria: str


class RDTechnologyOutput(BaseModel):
    """Output schema for R&D & Technology Agent (Section 6)"""
    task_id: str
    section_number: str = Field(default="6")

    rd_plan: dict = Field(
        ...,
        description="Development stages, milestones, timeline_to_market, cost_estimate, trl_level"
    )

    ip_analysis: dict = Field(
        ...,
        description="Patent status, defensibility_score, freedom_to_operate, competitive_ip_landscape"
    )

    technology_risk: str = Field(
        ...,
        min_length=50,
        description="What could go wrong technically — specific failure modes, not generic"
    )

    development_milestones: List[DevelopmentStage] = Field(
        ...,
        min_length=1,
        description="Phased development plan with timeline and cost"
    )

    technical_dependencies: List[str] = Field(
        default=[],
        description="External dependencies: third-party tech, partnerships, regulatory approval"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
