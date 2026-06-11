from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class RoleRequirement(BaseModel):
    role_title: str
    responsibilities: str = Field(..., min_length=30)
    required_by_month: int = Field(..., ge=0, le=36)
    cost_range_annual: str
    criticality: Literal["critical", "important", "nice_to_have"]
    closes_gap: Optional[str] = None


class HiringMilestone(BaseModel):
    month: int = Field(..., ge=0, le=36)
    role: str
    justification: str = Field(..., min_length=20)
    prerequisite: Optional[str] = None


class HRPlanOutput(BaseModel):
    """Output schema for Human Resources Plan Agent (Section 11)"""
    task_id: str
    section_number: str = Field(default="11")

    roles_and_responsibilities: List[RoleRequirement] = Field(
        ...,
        min_length=1,
        description="Key positions required for execution with timing and cost"
    )

    hiring_timeline: List[HiringMilestone] = Field(
        ...,
        min_length=1,
        description="When to hire each role, in sequence"
    )

    headcount_plan: dict = Field(
        ...,
        description="Monthly headcount and cost: {month_0: {headcount: N, total_cost: $X}, ...}"
    )

    personnel_policy: str = Field(
        ...,
        min_length=100,
        description="Compensation approach, equity policy, contractor vs FTE strategy"
    )

    knowledge_gaps: List[str] = Field(
        ...,
        min_length=1,
        description="Skills the business must acquire (training, advisory, hiring)"
    )

    hiring_risks: List[str] = Field(
        default=[],
        description="Risks related to hiring plan: talent availability, cost, time to hire"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
