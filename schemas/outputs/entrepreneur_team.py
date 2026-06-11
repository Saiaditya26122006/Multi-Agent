from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class FounderProfile(BaseModel):
    name: str = Field(default="Founder")
    role: str
    background: str = Field(..., min_length=20)
    relevant_experience: str = Field(..., min_length=30)
    credibility_score: Literal["high", "medium", "low"]
    founder_market_fit: str = Field(..., min_length=30)


class EntrepreneurTeamOutput(BaseModel):
    """Output schema for Entrepreneur & Development Team Agent (Section 2)"""
    task_id: str
    section_number: str = Field(default="2")

    founder_profiles: List[FounderProfile] = Field(
        ...,
        min_length=1,
        description="Profiles of founders with background and credibility assessment"
    )

    team_strengths: List[str] = Field(
        ...,
        min_length=2,
        description="What the team is good at — specific capabilities, not generic"
    )

    team_gaps: List[str] = Field(
        ...,
        min_length=1,
        description="Capabilities the team lacks — feeds Section 11 HR plan"
    )

    team_credibility_assessment: str = Field(
        ...,
        min_length=100,
        description="Overall assessment of team fit for this opportunity"
    )

    execution_risks: List[str] = Field(
        default=[],
        description="Team-related risks: solo founder, no GTM experience, technical gaps, etc."
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
