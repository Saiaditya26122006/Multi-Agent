from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class TechStackOutput(BaseModel):
    """Output schema for Tech Stack & Data Privacy Agent"""
    task_id: str
    section_number: str = Field(default="6.5")

    infrastructure: dict = Field(
        ...,
        description="cloud_provider, regions, estimated_monthly_cost, key_services"
    )

    ai_ml_stack: dict = Field(
        ...,
        description="primary_llm, cost_per_1m_tokens, estimated_monthly_tokens, estimated_monthly_cost"
    )

    database: dict = Field(
        ...,
        description="primary_db, vector_db, cache, total_monthly_cost"
    )

    third_party_apis: List[dict] = Field(
        ...,
        description="List of external APIs: name, purpose, monthly_cost"
    )

    authentication: dict = Field(
        ...,
        description="provider, approach, gdpr_compliant"
    )

    data_privacy_compliance: dict = Field(
        ...,
        description="regulations_covered, data_residency, encryption, user_rights, dpa_signed, dpo_appointed"
    )

    total_tech_cost_monthly: float = Field(..., ge=0)
    total_tech_cost_annual: float = Field(..., ge=0)

    tech_risk_assessment: dict = Field(
        ...,
        description="scalability_concerns, vendor_lock_in, compliance_gaps"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
