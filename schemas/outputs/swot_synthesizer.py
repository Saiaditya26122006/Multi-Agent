from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class SWOTItem(BaseModel):
    item: str
    evidence: str
    impact: Literal["high", "medium", "low"]


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class SWOTSynthesizerOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="5")
    strengths: List[SWOTItem] = Field(..., min_length=2)
    weaknesses: List[SWOTItem] = Field(..., min_length=2)
    opportunities: List[SWOTItem] = Field(..., min_length=2)
    threats: List[SWOTItem] = Field(..., min_length=2)
    strategic_implications: str = Field(..., min_length=100)
    priority_strategic_issues: List[str] = Field(..., min_length=2, description="Top issues to address")
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
