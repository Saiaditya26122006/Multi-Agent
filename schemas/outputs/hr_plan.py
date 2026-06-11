from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict, Any


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


class MonthlyHeadcount(BaseModel):
    """Single month's headcount entry"""
    headcount: int = Field(..., ge=0, description="Total headcount at end of month")
    total_cost_monthly: float = Field(..., ge=0, description="Total monthly salary + benefits cost")
    roles: List[str] = Field(..., description="List of role titles active this month")


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

    headcount_plan: Dict[str, MonthlyHeadcount] = Field(
        ...,
        description="Monthly headcount: {'month_0': MonthlyHeadcount(...), 'month_6': ..., 'month_12': ...}"
    )

    @field_validator('headcount_plan')
    @classmethod
    def validate_headcount_structure(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure headcount_plan has required months and structure"""
        required_months = ["month_0", "month_6", "month_12"]
        for month in required_months:
            if month not in v:
                raise ValueError(
                    f"headcount_plan missing required key '{month}'. "
                    f"Financial model requires month_0, month_6, month_12 at minimum."
                )
        return v

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
