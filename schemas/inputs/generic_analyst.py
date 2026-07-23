"""Input schema for the Generic Analyst agent (Build v2 custom sections).

Unlike the topic-locked specialists, this agent is told its topic at runtime
via ``section_title`` — so one class can write any custom section Alex adds.
"""

from typing import Optional

from pydantic import BaseModel, Field


class GenericAnalystInput(BaseModel):
    """Everything the generic analyst needs to write one custom section."""

    section_title: str = Field(..., description="The topic Alex named for this section")
    idea: str = Field("", description="The business idea under analysis")
    context: str = Field("", description="Prior-section context / blackboard notes")
    focus: str = Field("", description="Alex's chosen focus angle, if any")
    task_id: Optional[str] = None
    session_id: Optional[str] = None
