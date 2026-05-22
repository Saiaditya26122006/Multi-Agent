from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class PESTFactor(BaseModel):
    category: Literal["political", "economic", "social", "technological"]
    factor: str
    impact: Literal["positive", "negative", "neutral"]
    relevance: Literal["high", "medium", "low"]


class PorterForce(BaseModel):
    force: str
    assessment: str
    strength: Literal["high", "medium", "low"]


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class EnvironmentResearchOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="3")
    pest_analysis: List[PESTFactor] = Field(..., min_length=4)
    five_forces: List[PorterForce] = Field(..., min_length=5)
    risks_opportunities: dict = Field(..., description="risks: List[str], opportunities: List[str]")
    market_context: str = Field(..., min_length=100, description="Overall external environment summary")
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
