from pydantic import BaseModel, Field
from typing import List, Optional


class OpportunityAnalystInput(BaseModel):
    task_id: str = Field(..., description="Task ID from task_readiness table")
    session_id: str
    idea_summary: str = Field(..., min_length=10, description="Raw approved idea from Phase 1")
    ceo_assumptions: List[dict] = Field(..., description="Q&A pairs from L1 — question_asked + ceo_answer")
    approved_decision: dict = Field(..., description="Approved decision record from Gate 1")
    constitution_version: str = Field(default="1.0")
    acceptance_criteria: str = Field(..., description="From task_readiness — how output quality is judged")
