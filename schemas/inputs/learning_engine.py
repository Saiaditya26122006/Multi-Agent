from pydantic import BaseModel, Field
from typing import Literal


class RecordAcceptanceInput(BaseModel):
    session_id: str
    section_number: str
    confidence_score: Literal["high", "medium", "low"]
    assumptions_count: int = Field(ge=0)
    devils_advocate_verdict: str = Field(default="not_reviewed")


class RecordRejectionInput(BaseModel):
    session_id: str
    section_number: str
    reason: str = Field(..., min_length=5, description="Why the section was rejected")
    ceo_feedback: str = Field(default="", description="Alex's exact words")


class RecordEditInput(BaseModel):
    session_id: str
    section_number: str
    field_edited: str = Field(..., description="Which field Alex changed")
    original_value: str
    new_value: str


class RecordDAAccuracyInput(BaseModel):
    session_id: str
    section_number: str
    challenge_type: str
    was_valid: bool = Field(..., description="Whether the DA challenge turned out to be correct")
