from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Assumption(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    source: Literal["validated", "alex_provided", "agent_inferred", "assumed"]
    source_detail: Optional[str] = None


class ExitStrategyOutput(BaseModel):
    """Output schema for Exit Strategy & Cap Table Agent"""
    task_id: str
    section_number: str = Field(default="14")

    exit_strategy: dict = Field(
        ...,
        description="primary_exit_path, acquisition_targets, ipo_path, exit_timeline, exit_valuation"
    )

    cap_table: dict = Field(
        ...,
        description="pre_seed, post_seed, post_series_a, exit_scenario with equity percentages and valuations"
    )

    funding_strategy: dict = Field(
        ...,
        description="seed_round, series_a, series_b with amounts, timing, milestones"
    )

    investor_returns: dict = Field(
        ...,
        description="seed_return_multiple, series_a_return_multiple, exit_valuation scenarios"
    )

    dilution_analysis: dict = Field(
        ...,
        description="founder_dilution_path, employee_pool_sizing, investor_ownership"
    )

    exit_risks: List[str] = Field(
        ...,
        description="Market risks, acquisition landscape, valuation risks"
    )

    assumptions_used: List[Assumption]
    uncertainties: List[str] = Field(default=[])
    confidence_score: Literal["high", "medium", "low"]

    model_used: str
    input_tokens: int
    output_tokens: int
