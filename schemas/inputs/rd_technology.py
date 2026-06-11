from pydantic import BaseModel
from typing import Optional


class RDTechnologyInput(BaseModel):
    """Input schema for R&D & Technology Agent (Section 6)"""
    task_id: str
    session_id: str

    # From Section 1 (Opportunity)
    opportunity_description: str
    competitive_strategy: str

    # From CEO answers (Phase 1 clarification)
    technology_description: Optional[str] = None
    ip_status: Optional[str] = None
    patent_details: Optional[str] = None
    technical_milestones: Optional[str] = None

    # Additional context
    industry_sector: Optional[str] = None

    acceptance_criteria: str = ""
