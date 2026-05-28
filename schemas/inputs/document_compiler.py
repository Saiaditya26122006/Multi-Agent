from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class CompileInput(BaseModel):
    all_outputs: Dict[str, dict] = Field(..., description="All section outputs keyed by section number")
    business_name: str = Field(default="The Business", description="Name for the plan title")
    coherence_audit: Optional[dict] = Field(default=None, description="Coherence audit results for appendix")
    council_reports: Optional[List[dict]] = Field(default=None, description="Council review reports for QA appendix")
    include_assumptions_appendix: bool = Field(default=True)
    include_quality_appendix: bool = Field(default=True)
