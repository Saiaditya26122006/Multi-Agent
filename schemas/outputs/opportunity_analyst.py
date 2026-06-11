from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class MarketSizing(BaseModel):
    """TAM-SAM-SOM market sizing framework (investor-standard)"""
    tam: float = Field(..., gt=0, description="Total Addressable Market ($)")
    tam_definition: str = Field(..., min_length=30, description="What market does TAM represent?")
    tam_source: str = Field(..., min_length=20, description="Where did TAM number come from? (URL, report, calculation)")

    sam: float = Field(..., gt=0, description="Serviceable Addressable Market ($)")
    sam_definition: str = Field(..., min_length=30, description="How is SAM filtered from TAM?")
    sam_calculation: str = Field(..., min_length=20, description="Show the math: TAM × geography × segment × budget")

    som_year_1: float = Field(..., gt=0, description="Serviceable Obtainable Market Year 1 ($)")
    som_year_3: float = Field(..., gt=0, description="Serviceable Obtainable Market Year 3 ($)")
    som_logic: str = Field(..., min_length=50, description="Why is this capture rate realistic? What are the constraints?")

    capture_rate_year_1_pct: float = Field(..., ge=0.0, le=100.0, description="SOM/SAM as percentage Year 1")
    capture_rate_year_3_pct: float = Field(..., ge=0.0, le=100.0, description="SOM/SAM as percentage Year 3")

    @field_validator('capture_rate_year_1_pct')
    @classmethod
    def validate_year_1_capture(cls, v: float) -> float:
        if v > 5.0:
            raise ValueError(
                f"Capture rate Year 1 is {v:.1f}% — unrealistic for startups (typically 0.1-5%). "
                "Either reduce SOM or increase SAM with justification."
            )
        return v


class OpportunityAnalystOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="1")
    opportunity_description: str = Field(..., min_length=50)
    competitive_strategy: str = Field(..., min_length=30)

    market_sizing: MarketSizing = Field(
        ...,
        description="TAM-SAM-SOM framework with sources and capture rate logic"
    )

    objectives: List[dict] = Field(..., description="List of quantified Year 1 objectives with metrics")
    icp_hypothesis: dict = Field(..., description="buyer_role, budget_process, decision_timeline, pain_points")
    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[], description="Things the agent could not validate")
    confidence_score: Literal["high", "medium", "low"]
    model_used: str
    input_tokens: int
    output_tokens: int
