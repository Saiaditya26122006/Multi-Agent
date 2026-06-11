from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class Partnership(BaseModel):
    partner_type: str
    rationale: str = Field(..., min_length=30)
    value_exchange: str = Field(..., min_length=20)
    timeline: str
    criticality: Literal["critical", "important", "nice_to_have"]


class AlliancesOutput(BaseModel):
    """Output schema for Alliances & Outsourcing Agent (Section 7)"""
    task_id: str
    section_number: str = Field(default="7")

    alliance_plan: List[Partnership] = Field(
        ...,
        min_length=1,
        description="Key partnerships and what they enable"
    )

    outsourcing_strategy: str = Field(
        ...,
        min_length=100,
        description="What will NOT be built in-house and why — make vs buy decisions"
    )

    partnership_risks: List[str] = Field(
        default=[],
        description="Risks related to partnerships: dependency, control, execution"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
