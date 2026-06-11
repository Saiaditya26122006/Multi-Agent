from pydantic import BaseModel
from typing import Optional, List


class HRPlanInput(BaseModel):
    """Input schema for Human Resources Plan Agent (Section 11)"""
    task_id: str
    session_id: str

    # From Section 1 (Opportunity)
    business_model: str
    opportunity_description: str
    objectives: List[dict]

    # From Section 2 (Entrepreneur Team)
    team_gaps: Optional[List[str]] = None

    # From Section 4 (Organisation Designer)
    capability_gaps: Optional[List[dict]] = None
    org_structure: Optional[str] = None

    # From Section 5 (SWOT)
    strategic_implications: Optional[str] = None
    priority_strategic_issues: Optional[List[str]] = None

    # From Section 8 (Marketing)
    revenue_assumptions: Optional[dict] = None
    target_market_analysis: Optional[dict] = None

    acceptance_criteria: str = ""
