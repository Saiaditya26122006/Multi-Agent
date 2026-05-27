from pydantic import BaseModel, Field
from typing import Dict, Optional


class CompileInput(BaseModel):
    all_outputs: Dict[str, dict] = Field(..., description="All section outputs keyed by section number")
    business_name: str = Field(default="The Business", description="Name for the plan title")
    coherence_audit: Optional[dict] = Field(default=None, description="Coherence audit results for appendix")
