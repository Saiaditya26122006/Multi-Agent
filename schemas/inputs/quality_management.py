from pydantic import BaseModel
from typing import Optional


class QualityManagementInput(BaseModel):
    """Input schema for Quality Management Agent (Section 9)"""
    task_id: str
    session_id: str

    # From Section 1 (Opportunity)
    opportunity_description: str

    # From Section 8 (Marketing)
    service_description: Optional[str] = None
    target_market_analysis: Optional[dict] = None

    acceptance_criteria: str = ""
