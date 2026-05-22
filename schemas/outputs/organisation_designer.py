from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Role(BaseModel):
    title: str
    responsibilities: List[str]
    required_skills: List[str]
    hire_timeline: str
    assigned_to: Literal["founder", "hire", "outsource", "tbd"]


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class OrganisationDesignerOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="4")
    org_structure: str = Field(..., description="Description of hierarchical structure")
    capability_gaps: List[dict] = Field(..., description="gap, severity, resolution: build/buy/partner")
    roles_and_responsibilities: List[Role]
    headcount_plan: dict = Field(..., description="year_1, year_2, year_3 headcount with cost estimates")
    personnel_policy: str = Field(..., min_length=50)
    knowledge_gaps: List[str]
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
