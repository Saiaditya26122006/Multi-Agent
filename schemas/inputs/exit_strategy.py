from pydantic import BaseModel
from typing import Optional


class ExitStrategyInput(BaseModel):
    """Input schema for Exit Strategy & Cap Table Agent"""
    task_id: str
    session_id: str

    # From Opportunity (Section 1)
    business_type: str
    market_size_tam: Optional[str] = None

    # From Financial (Section 12)
    year_3_revenue: Optional[float] = None
    year_3_arr: Optional[float] = None  # Annual Recurring Revenue
    break_even_year: Optional[int] = None
    profitability_year_3: Optional[bool] = None

    # From Marketing (Section 8)
    target_market: Optional[str] = None
    competitive_positioning: Optional[str] = None

    # Additional context
    industry_sector: Optional[str] = None  # SaaS, marketplace, etc.
    geography: Optional[str] = None  # EU, US, Global
    founder_goals: Optional[str] = None  # Exit preference, if known

    acceptance_criteria: str = ""
