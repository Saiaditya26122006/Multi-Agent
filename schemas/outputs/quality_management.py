from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class QualityManagementOutput(BaseModel):
    """Output schema for Quality Management Agent (Section 9)"""
    task_id: str
    section_number: str = Field(default="9")

    quality_policy: str = Field(
        ...,
        min_length=100,
        description="Overall quality assurance approach and standards"
    )

    quality_procedures: List[str] = Field(
        ...,
        min_length=2,
        description="Specific methods for ensuring consistent delivery"
    )

    quality_metrics: List[dict] = Field(
        default=[],
        description="How quality is measured: KPIs, SLAs, customer satisfaction"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
