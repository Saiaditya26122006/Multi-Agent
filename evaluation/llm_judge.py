"""
LLM-as-Judge scoring for evaluation runs.

Uses Haiku (cheap, fast) to evaluate output quality on dimensions
that can't be measured programmatically:
  - Relevance: Does the output actually address THIS specific business idea?
  - Coherence: Do the sections tell a consistent story?
  - Actionability: Could Alex take specific actions based on this plan?
"""

import asyncio
import json
import logging
import os
from typing import Optional

import boto3

from agents.phase2.llm_utils import strip_markdown_json

logger = logging.getLogger(__name__)


class LLMJudge:
    """Uses a cheap LLM (Haiku) to score output quality."""

    def __init__(self):
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

    async def score_section_relevance(self, idea_summary: str, section_output: dict, agent_role: str) -> dict:
        """Score whether a section output is relevant to the specific business idea.

        Returns: {"relevance_score": 1-10, "explanation": "..."}
        """
        output_str = json.dumps(
            {k: v for k, v in section_output.items()
             if not k.startswith("_") and k not in ("input_tokens", "output_tokens", "model_used", "task_id")},
            indent=2, default=str,
        )[:3000]

        response = await self._call(
            system="You are evaluating AI-generated business plan sections. Score 1-10 for relevance. Be strict.",
            user=f"""BUSINESS IDEA:
{idea_summary}

AGENT ROLE: {agent_role}

SECTION OUTPUT:
{output_str}

Score this output 1-10 on RELEVANCE:
- 10: Every claim directly addresses this specific business idea with specific details
- 7: Mostly relevant but some generic filler that could apply to any business
- 4: Mix of relevant and generic — feels like a template with some customization
- 1: Completely generic — could be about any business, no specific details from the idea

Return ONLY valid JSON: {{"relevance_score": N, "explanation": "one sentence"}}""",
            max_tokens=256,
        )

        return self._parse_score(response, "relevance_score")

    async def score_coherence(self, all_sections: dict, idea_summary: str) -> dict:
        """Score cross-section coherence of a complete pipeline run.

        Returns: {"coherence_score": 1-10, "contradictions": [...], "explanation": "..."}
        """
        summary_parts = []
        for sec_num, output in sorted(all_sections.items()):
            if not isinstance(output, dict):
                continue
            key_fields = {}
            for k in ("confidence_score", "revenue_assumptions", "icp_hypothesis",
                      "competitive_strategy", "break_even_analysis", "market_entry_strategy"):
                if k in output:
                    key_fields[k] = output[k]
            if key_fields:
                summary_parts.append(f"Section {sec_num}: {json.dumps(key_fields, default=str)[:400]}")

        if len(summary_parts) < 2:
            return {"coherence_score": 5, "contradictions": [], "explanation": "Too few sections to evaluate coherence"}

        sections_str = "\n\n".join(summary_parts)

        response = await self._call(
            system="You are auditing a multi-section business plan for internal consistency. Be specific about contradictions.",
            user=f"""BUSINESS IDEA: {idea_summary}

SECTION OUTPUTS:
{sections_str}

Score 1-10 on COHERENCE (do the sections tell a consistent story?):
- 10: All numbers match, strategies align, no contradictions
- 7: Minor inconsistencies but overall story holds together
- 4: Some contradictions between sections (e.g., different pricing assumptions)
- 1: Sections actively contradict each other on key facts

Return ONLY valid JSON:
{{"coherence_score": N, "contradictions": ["specific contradiction 1", "..."], "explanation": "one sentence"}}""",
            max_tokens=512,
        )

        return self._parse_score(response, "coherence_score")

    async def score_actionability(self, all_sections: dict, idea_summary: str) -> dict:
        """Score whether Alex could actually ACT on this plan.

        Returns: {"actionability_score": 1-10, "missing_for_action": [...], "explanation": "..."}
        """
        key_outputs = {}
        for sec_num, output in all_sections.items():
            if not isinstance(output, dict):
                continue
            key_outputs[sec_num] = {
                k: v for k, v in output.items()
                if k in ("objectives", "revenue_assumptions", "break_even_analysis",
                         "launch_programme", "capital_plan", "market_entry_strategy",
                         "contingency_scenarios", "icp_hypothesis")
            }

        outputs_str = json.dumps(key_outputs, indent=2, default=str)[:4000]

        response = await self._call(
            system="You are a CEO evaluating whether a business plan gives you enough specific information to take action. Be strict.",
            user=f"""BUSINESS IDEA: {idea_summary}

KEY PLAN OUTPUTS:
{outputs_str}

Score 1-10 on ACTIONABILITY (can Alex actually DO something with this?):
- 10: Alex could start executing tomorrow — specific steps, dates, budgets, targets
- 7: Mostly actionable but some steps are vague ("develop marketing strategy" instead of specific channels/budgets)
- 4: Provides direction but not enough detail to execute without significant additional planning
- 1: Pure analysis with no clear next steps — "interesting but what do I DO?"

Return ONLY valid JSON:
{{"actionability_score": N, "missing_for_action": ["what's missing for Alex to act"], "explanation": "one sentence"}}""",
            max_tokens=512,
        )

        return self._parse_score(response, "actionability_score")

    async def full_evaluation(self, run_result: dict) -> dict:
        """Run all LLM-judge evaluations on a complete pipeline run.

        Returns combined scores.
        """
        idea_summary = ""
        for idea in _get_test_ideas():
            if idea["id"] == run_result.get("idea_id"):
                idea_summary = idea["idea_summary"]
                break

        if not idea_summary:
            return {"error": "Could not find idea summary for this run"}

        all_outputs = {}
        for sec_num, sec_data in run_result.get("sections", {}).items():
            if sec_data.get("output"):
                all_outputs[sec_num] = sec_data["output"]

        if not all_outputs:
            return {"error": "No section outputs to evaluate"}

        # Score coherence and actionability for the whole run
        coherence = await self.score_coherence(all_outputs, idea_summary)
        actionability = await self.score_actionability(all_outputs, idea_summary)

        # Score relevance per section
        section_relevance = {}
        for sec_num, output in all_outputs.items():
            from evaluation.eval_runner import AGENT_CONFIGS
            config = AGENT_CONFIGS.get(sec_num, {})
            relevance = await self.score_section_relevance(
                idea_summary, output, config.get("role", f"Section {sec_num}")
            )
            section_relevance[sec_num] = relevance

        avg_relevance = 0
        if section_relevance:
            scores = [r.get("relevance_score", 5) for r in section_relevance.values()]
            avg_relevance = round(sum(scores) / len(scores), 1)

        return {
            "coherence": coherence,
            "actionability": actionability,
            "section_relevance": section_relevance,
            "avg_relevance": avg_relevance,
            "overall_llm_score": round(
                (coherence.get("coherence_score", 5) * 0.3 +
                 actionability.get("actionability_score", 5) * 0.4 +
                 avg_relevance * 0.3),
                1,
            ),
        }

    async def _call(self, system: str, user: str, max_tokens: int = 512) -> Optional[str]:
        """Make a single LLM call to Haiku for judging."""
        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
            return response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("[LLMJudge] Call failed: %s", e)
            return None

    def _parse_score(self, response: Optional[str], score_key: str) -> dict:
        """Parse JSON response from judge LLM."""
        if not response:
            return {score_key: 5, "explanation": "Judge LLM failed — default score"}
        try:
            text = strip_markdown_json(response)
            result = json.loads(text)
            if isinstance(result, dict) and score_key in result:
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        return {score_key: 5, "explanation": f"Failed to parse judge response: {response[:100]}"}


def _get_test_ideas():
    """Import test ideas lazily to avoid circular imports."""
    from evaluation.test_ideas import TEST_IDEAS
    return TEST_IDEAS
