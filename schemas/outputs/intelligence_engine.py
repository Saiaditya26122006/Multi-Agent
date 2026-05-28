from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional


class ReasoningTrace(BaseModel):
    decomposition: str = Field(default="", description="Step 1 decomposition output")
    challenge: str = Field(default="", description="Step 3 adversarial challenge output")
    revisions_applied: bool = Field(default=False, description="Whether Step 4 revision was applied")
    reasoning_budget: int = Field(default=3, description="Budget used for this run")


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)


class ReasonAndProduceOutput(BaseModel):
    parsed_output: Optional[dict] = Field(default=None, description="Parsed JSON output from the reasoning chain")
    reasoning_trace: ReasoningTrace = Field(default_factory=ReasoningTrace)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


class GradedClaim(BaseModel):
    statement: str
    original_confidence: Literal["high", "medium", "low"]
    corrected_confidence: Literal["high", "medium", "low"]
    reason: str


class FailedHypothesis(BaseModel):
    hypothesis: Literal["funnel_math", "unit_economics", "timeline", "growth"]
    result: Literal["pass", "fail"]
    explanation: str
    numbers_involved: str = Field(default="")


class CalibrateConfidenceOutput(BaseModel):
    calibrated_confidence: Literal["high", "medium", "low"]


class GradeEvidenceOutput(BaseModel):
    graded_claims: List[GradedClaim] = Field(default_factory=list)
    total_claims: int = Field(default=0, ge=0)
    downgraded_count: int = Field(default=0, ge=0)


class ValidateHypothesesOutput(BaseModel):
    failed_hypotheses: List[FailedHypothesis] = Field(default_factory=list)
    total_checked: int = Field(default=0, ge=0)
    all_passed: bool = Field(default=True)


class SoWhatFilterOutput(BaseModel):
    passed: bool = Field(default=True)
    critique: Optional[str] = Field(default=None, description="Critique text if failed, None if passed")
