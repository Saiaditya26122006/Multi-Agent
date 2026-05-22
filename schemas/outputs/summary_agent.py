from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class SummaryAgentOutput(BaseModel):
    task_id: str
    section_number: str = Field(default="executive_summary")
    executive_summary: str = Field(
        ...,
        min_length=200,
        max_length=3000,
        description="One page maximum. Opportunity, competitive advantage, team, financials, ask.",
    )
    headline_metrics: dict = Field(
        ...,
        description="year1_revenue_range, break_even_month, primary_risk, team_size_year1",
    )
    key_assumptions_flagged: List[str] = Field(
        default=[],
        description="Assumptions Alex should validate before using this plan externally",
    )
    sections_included: List[str]
    sections_skipped: List[str]
    coherence_issues_resolved: List[str] = Field(
        default=[],
        description="Contradictions that were resolved during agent negotiation",
    )
    model_used: str
    input_tokens: int
    output_tokens: int
