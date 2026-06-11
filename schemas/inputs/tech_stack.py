from pydantic import BaseModel
from typing import Optional


class TechStackInput(BaseModel):
    """Input schema for Tech Stack & Data Privacy Agent"""
    task_id: str
    session_id: str

    # From Opportunity (Section 1)
    business_type: str  # b2b_saas, b2c_marketplace, etc.
    product_description: str

    # From Organisation (Section 4)
    team_capabilities: Optional[dict] = None
    technology_requirements: Optional[str] = None

    # From Operations (Section 10)
    delivery_model: Optional[str] = None
    infrastructure_needs: Optional[str] = None

    # Additional context
    target_geography: Optional[str] = None  # EU, US, Global
    data_sensitivity: Optional[str] = None  # high, medium, low
    compliance_requirements: Optional[list] = None  # ["GDPR", "HIPAA", etc.]

    acceptance_criteria: str = ""
