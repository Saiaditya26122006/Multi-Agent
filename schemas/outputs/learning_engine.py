from pydantic import BaseModel, Field
from typing import Dict, List, Literal


class PatternRecord(BaseModel):
    event: Literal["accepted", "rejected", "edit"]
    section: str
    timestamp: str
    session_id: str
    confidence: str = Field(default="")
    reason: str = Field(default="")
    ceo_feedback: str = Field(default="")
    field: str = Field(default="")


class DAAccuracyStat(BaseModel):
    total: int = Field(ge=0)
    valid: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)


class DAAccuracyStats(BaseModel):
    stats: Dict[str, DAAccuracyStat] = Field(default_factory=dict)


class LearningContext(BaseModel):
    context_text: str = Field(default="", description="Formatted learning context string for prompt injection")
    failure_count: int = Field(default=0, description="Number of past failures for this section type")
