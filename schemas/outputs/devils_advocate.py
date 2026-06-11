from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class Challenge(BaseModel):
    claim: str = Field(..., description="The specific claim being challenged")
    challenge_type: Literal["logical_gap", "overconfidence", "unsupported", "math_error", "contradiction", "survivorship_bias", "system_failure"] = Field(...)
    severity: Literal["high", "medium", "low"] = Field(...)
    explanation: str = Field(..., min_length=20, description="Why this is a problem")
    suggested_fix: str = Field(..., description="How to address this issue")
    section_reference: Optional[str] = Field(default=None, description="Cross-section reference if contradiction")


class DevilsAdvocateOutput(BaseModel):
    task_id: str
    section_number: str
    verdict: Literal["pass", "revise", "reject", "escalate"] = Field(..., description="Overall verdict on section quality")
    challenges: List[Challenge] = Field(..., description="List of challenges found")
    confidence_assessment: Literal["honest", "inflated", "deflated", "unknown"] = Field(..., description="Is the section's self-reported confidence accurate?")
    recommended_confidence: Literal["high", "medium", "low"] = Field(..., description="What confidence should actually be")
    assumptions_grade: Literal["well_sourced", "mixed", "mostly_unsupported", "unknown"] = Field(...)
    overall_reasoning_quality: Literal["strong", "adequate", "weak", "unknown"] = Field(...)
    summary: str = Field(..., min_length=50, description="One-paragraph summary for the Mother Agent")
    model_used: str = Field(default="")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
