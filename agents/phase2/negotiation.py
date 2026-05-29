"""
Bounded negotiation protocol for inter-agent contradiction resolution.

Instead of immediately escalating contradictions to the CEO via Telegram,
agents first attempt to resolve disagreements themselves through structured
negotiation (max 3 rounds). Only deadlocks escalate.

Classes:
    NegotiationResult — outcome dataclass
    NegotiationRound — single negotiation session
    NegotiationManager — orchestrates negotiation with LLM calls

Helper:
    should_negotiate() — determines if a contradiction warrants negotiation
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from agents.phase2.llm_utils import strip_markdown_json

logger = logging.getLogger(__name__)


@dataclass
class NegotiationResult:
    """Outcome of a bounded negotiation between two agents."""

    outcome: Literal["consensus", "compromise", "deadlock"]
    agreed_value: Optional[dict]
    rounds: int
    history: list[dict]
    initiator: str
    responder: str


@dataclass
class NegotiationRound:
    """Runs a bounded negotiation between two agents over a contested claim.

    Each round: initiator states position with evidence, responder evaluates.
    Verdicts: "accept" -> consensus, "counter" -> next round, "reject_with_evidence" -> next round.
    After max_rounds exhausted -> deadlock.
    """

    initiator: str
    responder: str
    claim: str
    evidence: dict
    max_rounds: int = 3
    _history: list[dict] = field(default_factory=list, init=False)

    async def run(self, llm_caller: Callable) -> NegotiationResult:
        """Execute the negotiation loop.

        Args:
            llm_caller: async callable(agent_name, prompt, system_prompt) -> str
                Returns raw LLM text response.

        Returns:
            NegotiationResult with outcome, agreed value, and full history.
        """
        current_position = {
            "claim": self.claim,
            "evidence": self.evidence,
            "stance": "initial",
        }

        for round_num in range(1, self.max_rounds + 1):
            logger.info(
                "[Negotiation] Round %d/%d: %s vs %s over '%s'",
                round_num,
                self.max_rounds,
                self.initiator,
                self.responder,
                self.claim[:80],
            )

            # Initiator states/restates position
            position_prompt = self._build_position_prompt(
                self.initiator, current_position, round_num
            )
            position_response = await llm_caller(
                self.initiator,
                position_prompt,
                POSITION_SYSTEM_PROMPT,
            )
            position = self._parse_position(position_response)

            self._history.append({
                "round": round_num,
                "phase": "position",
                "agent": self.initiator,
                "content": position,
            })

            # Responder evaluates
            eval_prompt = self._build_evaluation_prompt(
                self.responder, position, round_num
            )
            eval_response = await llm_caller(
                self.responder,
                eval_prompt,
                EVALUATION_SYSTEM_PROMPT,
            )
            evaluation = self._parse_evaluation(eval_response)

            self._history.append({
                "round": round_num,
                "phase": "evaluation",
                "agent": self.responder,
                "content": evaluation,
            })

            verdict = evaluation.get("verdict", "reject_with_evidence")

            if verdict == "accept":
                logger.info(
                    "[Negotiation] Consensus reached in round %d", round_num
                )
                return NegotiationResult(
                    outcome="consensus",
                    agreed_value=position.get("proposed_value", position),
                    rounds=round_num,
                    history=self._history,
                    initiator=self.initiator,
                    responder=self.responder,
                )

            if verdict == "counter":
                # Responder offers a compromise — use it as next position
                counter_value = evaluation.get("counter_proposal", {})
                if counter_value:
                    current_position = {
                        "claim": self.claim,
                        "evidence": counter_value.get("evidence", self.evidence),
                        "stance": "counter",
                        "proposed_value": counter_value.get("value", {}),
                        "rationale": counter_value.get("rationale", ""),
                    }
                    # Check if initiator can accept the counter
                    if round_num < self.max_rounds:
                        # Swap roles for next round evaluation
                        self.initiator, self.responder = (
                            self.responder,
                            self.initiator,
                        )
                else:
                    current_position["stance"] = "restated"

            elif verdict == "reject_with_evidence":
                rejection_evidence = evaluation.get("rejection_evidence", {})
                current_position = {
                    "claim": self.claim,
                    "evidence": rejection_evidence,
                    "stance": "contested",
                    "prior_position": position,
                }

        # Max rounds exhausted — check if we can settle on a compromise
        compromise_value = self._extract_compromise()
        if compromise_value:
            logger.info(
                "[Negotiation] Compromise found after %d rounds", self.max_rounds
            )
            return NegotiationResult(
                outcome="compromise",
                agreed_value=compromise_value,
                rounds=self.max_rounds,
                history=self._history,
                initiator=self.initiator,
                responder=self.responder,
            )

        logger.warning(
            "[Negotiation] Deadlock after %d rounds — escalation required",
            self.max_rounds,
        )
        return NegotiationResult(
            outcome="deadlock",
            agreed_value=None,
            rounds=self.max_rounds,
            history=self._history,
            initiator=self.initiator,
            responder=self.responder,
        )

    def _build_position_prompt(
        self, agent_name: str, position: dict, round_num: int
    ) -> str:
        """Build the prompt for an agent to state their position."""
        history_context = ""
        if self._history:
            history_context = (
                "\n\nNEGOTIATION HISTORY SO FAR:\n"
                + json.dumps(self._history[-4:], indent=2, default=str)
            )

        return (
            f"You are {agent_name} in round {round_num} of a negotiation.\n\n"
            f"CONTESTED CLAIM: {self.claim}\n\n"
            f"YOUR CURRENT POSITION:\n{json.dumps(position, indent=2, default=str)}\n"
            f"{history_context}\n\n"
            "State your position clearly. Be specific with numbers and data points. "
            "Cite your evidence. If this is round 2+, address the other agent's "
            "concerns and look for middle ground.\n\n"
            "Respond with a JSON object containing:\n"
            '- "proposed_value": your specific proposed value/conclusion (as a dict)\n'
            '- "evidence_cited": list of specific evidence points supporting your position\n'
            '- "concessions": any points you are willing to concede (list, can be empty)\n'
            '- "rationale": one paragraph explaining your reasoning\n'
        )

    def _build_evaluation_prompt(
        self, agent_name: str, position: dict, round_num: int
    ) -> str:
        """Build the prompt for an agent to evaluate the other's position."""
        return (
            f"You are {agent_name} evaluating a position in round {round_num}.\n\n"
            f"CONTESTED CLAIM: {self.claim}\n\n"
            f"OTHER AGENT'S POSITION:\n{json.dumps(position, indent=2, default=str)}\n\n"
            "Evaluate this position against your knowledge and evidence. "
            "Be fair — if their evidence is strong, acknowledge it. "
            "Seek compromise where possible.\n\n"
            "Respond with a JSON object containing:\n"
            '- "verdict": one of "accept", "counter", "reject_with_evidence"\n'
            '- "reasoning": why you chose this verdict\n'
            '- "counter_proposal": (if verdict is "counter") object with '
            '"value" (dict), "evidence" (dict), "rationale" (str)\n'
            '- "rejection_evidence": (if verdict is "reject_with_evidence") '
            "dict of evidence that contradicts the position\n"
            '- "areas_of_agreement": list of points where you DO agree\n'
        )

    def _parse_position(self, raw: str) -> dict:
        """Parse a position response from LLM."""
        text = strip_markdown_json(raw)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[Negotiation] Failed to parse position: %s", e)
            return {
                "proposed_value": {},
                "evidence_cited": [],
                "concessions": [],
                "rationale": raw[:500],
            }

    def _parse_evaluation(self, raw: str) -> dict:
        """Parse an evaluation response from LLM."""
        text = strip_markdown_json(raw)
        try:
            parsed = json.loads(text)
            # Validate verdict is one of the allowed values
            if parsed.get("verdict") not in ("accept", "counter", "reject_with_evidence"):
                parsed["verdict"] = "reject_with_evidence"
            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[Negotiation] Failed to parse evaluation: %s", e)
            return {
                "verdict": "reject_with_evidence",
                "reasoning": raw[:500],
                "rejection_evidence": {},
                "areas_of_agreement": [],
            }

    def _extract_compromise(self) -> Optional[dict]:
        """Try to find a compromise from negotiation history.

        Looks for counter proposals or areas of agreement across rounds.
        """
        areas_of_agreement = []
        last_counter = None

        for entry in self._history:
            content = entry.get("content", {})
            if content.get("areas_of_agreement"):
                areas_of_agreement.extend(content["areas_of_agreement"])
            if content.get("counter_proposal"):
                last_counter = content["counter_proposal"]
            if content.get("concessions"):
                areas_of_agreement.extend(content["concessions"])

        if last_counter and areas_of_agreement:
            return {
                "basis": "compromise",
                "value": last_counter.get("value", {}),
                "agreed_points": list(set(str(a) for a in areas_of_agreement)),
                "source": "negotiation_synthesis",
            }

        if len(areas_of_agreement) >= 2:
            return {
                "basis": "partial_agreement",
                "agreed_points": list(set(str(a) for a in areas_of_agreement)),
                "source": "areas_of_agreement",
            }

        return None


class NegotiationManager:
    """Orchestrates negotiation between agents using LLM calls via Bedrock.

    This is the main entry point for triggering negotiations from the
    Mother Agent or child agents when contradictions are detected.
    """

    def __init__(self, bedrock_client, model_id: str) -> None:
        self.bedrock = bedrock_client
        self.model_id = model_id

    async def negotiate(
        self,
        initiator: str,
        responder: str,
        claim: str,
        evidence: dict,
        max_rounds: int = 3,
    ) -> NegotiationResult:
        """Run a full negotiation between two agents.

        Args:
            initiator: Name/JID of the agent initiating the claim.
            responder: Name/JID of the agent contesting the claim.
            claim: The contested assertion.
            evidence: Supporting evidence from the initiator.
            max_rounds: Maximum negotiation rounds before deadlock.

        Returns:
            NegotiationResult with outcome and history.
        """
        logger.info(
            "[NegotiationManager] Starting negotiation: %s vs %s — '%s'",
            initiator,
            responder,
            claim[:100],
        )

        negotiation = NegotiationRound(
            initiator=initiator,
            responder=responder,
            claim=claim,
            evidence=evidence,
            max_rounds=max_rounds,
        )

        result = await negotiation.run(self._llm_caller)

        logger.info(
            "[NegotiationManager] Result: %s after %d rounds",
            result.outcome,
            result.rounds,
        )
        return result

    async def _llm_caller(
        self, agent_name: str, prompt: str, system_prompt: str
    ) -> str:
        """Make an LLM call on behalf of an agent.

        Routes through _generate_position or _evaluate_response based on
        the system prompt provided.
        """
        if system_prompt == POSITION_SYSTEM_PROMPT:
            position = await self._generate_position(
                agent_name, prompt, {}, []
            )
            return json.dumps(position, default=str)
        else:
            evaluation = await self._evaluate_response(
                agent_name, {"prompt": prompt}, []
            )
            return json.dumps(evaluation, default=str)

    async def _generate_position(
        self,
        agent_name: str,
        claim: str,
        evidence: dict,
        history: list[dict],
    ) -> dict:
        """Use LLM to generate an agent's negotiation position.

        Args:
            agent_name: The agent whose perspective to adopt.
            claim: The contested claim.
            evidence: Available evidence.
            history: Prior negotiation rounds.

        Returns:
            Dict with proposed_value, evidence_cited, concessions, rationale.
        """
        system = POSITION_SYSTEM_PROMPT
        user_message = (
            f"Agent: {agent_name}\n"
            f"Claim: {claim}\n"
            f"Evidence: {json.dumps(evidence, indent=2, default=str)}\n"
            f"History: {json.dumps(history[-4:], indent=2, default=str)}\n\n"
            "Generate your negotiation position as JSON."
        )

        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": 2048},
            )
            raw = response["output"]["message"]["content"][0]["text"]
            text = strip_markdown_json(raw)
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "[NegotiationManager] Failed to parse position for %s: %s",
                agent_name,
                e,
            )
            return {
                "proposed_value": {},
                "evidence_cited": [],
                "concessions": [],
                "rationale": f"Position generation failed: {e}",
            }
        except Exception as e:
            logger.error(
                "[NegotiationManager] LLM call failed for position (%s): %s",
                agent_name,
                e,
            )
            return {
                "proposed_value": {},
                "evidence_cited": [],
                "concessions": [],
                "rationale": f"LLM error: {e}",
            }

    async def _evaluate_response(
        self,
        agent_name: str,
        position: dict,
        history: list[dict],
    ) -> dict:
        """Use LLM to evaluate a negotiation position from the other agent.

        Args:
            agent_name: The evaluating agent.
            position: The position to evaluate.
            history: Prior negotiation rounds.

        Returns:
            Dict with verdict, reasoning, and optional counter/rejection evidence.
        """
        system = EVALUATION_SYSTEM_PROMPT
        user_message = (
            f"Agent: {agent_name}\n"
            f"Position to evaluate: {json.dumps(position, indent=2, default=str)}\n"
            f"History: {json.dumps(history[-4:], indent=2, default=str)}\n\n"
            "Evaluate this position and respond as JSON."
        )

        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": 2048},
            )
            raw = response["output"]["message"]["content"][0]["text"]
            text = strip_markdown_json(raw)
            parsed = json.loads(text)
            if parsed.get("verdict") not in ("accept", "counter", "reject_with_evidence"):
                parsed["verdict"] = "reject_with_evidence"
            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "[NegotiationManager] Failed to parse evaluation for %s: %s",
                agent_name,
                e,
            )
            return {
                "verdict": "reject_with_evidence",
                "reasoning": f"Evaluation parse failed: {e}",
                "rejection_evidence": {},
                "areas_of_agreement": [],
            }
        except Exception as e:
            logger.error(
                "[NegotiationManager] LLM call failed for evaluation (%s): %s",
                agent_name,
                e,
            )
            return {
                "verdict": "reject_with_evidence",
                "reasoning": f"LLM error: {e}",
                "rejection_evidence": {},
                "areas_of_agreement": [],
            }


def should_negotiate(contradiction: dict) -> bool:
    """Determine if a contradiction warrants negotiation vs. being trivial.

    Negotiation is worthwhile when:
    - The contradiction involves quantitative claims (numbers differ significantly)
    - Both sides have cited evidence
    - The severity is medium or high
    - The difference is not merely cosmetic (wording, formatting)

    Args:
        contradiction: Dict with keys like 'severity', 'type', 'initiator_evidence',
                      'responder_evidence', 'difference_magnitude', 'category'.

    Returns:
        True if negotiation should be attempted before escalation.
    """
    if not contradiction:
        logger.debug("[should_negotiate] Empty contradiction — skipping")
        return False

    severity = contradiction.get("severity", "low")
    if severity == "low":
        logger.debug("[should_negotiate] Low severity — not worth negotiating")
        return False

    category = contradiction.get("category", "")
    trivial_categories = {"formatting", "wording", "style", "cosmetic", "typo"}
    if category.lower() in trivial_categories:
        logger.debug(
            "[should_negotiate] Trivial category '%s' — skipping", category
        )
        return False

    initiator_evidence = contradiction.get("initiator_evidence", {})
    responder_evidence = contradiction.get("responder_evidence", {})
    if not initiator_evidence and not responder_evidence:
        logger.debug("[should_negotiate] No evidence on either side — skipping")
        return False

    # High severity always negotiates if there's any evidence
    if severity == "high":
        logger.info(
            "[should_negotiate] High severity with evidence — negotiation warranted"
        )
        return True

    # Medium severity: check for quantitative difference or conflicting data
    contradiction_type = contradiction.get("type", "")
    quantitative_types = {
        "numeric",
        "financial",
        "timeline",
        "percentage",
        "projection",
        "estimate",
    }
    if contradiction_type.lower() in quantitative_types:
        logger.info(
            "[should_negotiate] Quantitative contradiction — negotiation warranted"
        )
        return True

    # Check difference magnitude if provided
    magnitude = contradiction.get("difference_magnitude", 0)
    if isinstance(magnitude, (int, float)) and magnitude > 0.2:
        logger.info(
            "[should_negotiate] Significant magnitude (%.2f) — negotiation warranted",
            magnitude,
        )
        return True

    # Medium severity with evidence on both sides — negotiate
    if initiator_evidence and responder_evidence:
        logger.info(
            "[should_negotiate] Both sides have evidence — negotiation warranted"
        )
        return True

    logger.debug("[should_negotiate] Does not meet negotiation threshold")
    return False


# ── System Prompts for Negotiation ──────────────────────────────────────────

POSITION_SYSTEM_PROMPT = (
    "You are an AI agent participating in a structured negotiation with another agent. "
    "Your goal is to find the BEST answer supported by evidence, not to 'win'.\n\n"
    "Rules:\n"
    "1. Be specific — cite exact numbers, percentages, dates, sources\n"
    "2. Acknowledge uncertainty — if your evidence is weak, say so\n"
    "3. Seek compromise — identify what you can concede without sacrificing accuracy\n"
    "4. Focus on the business outcome — what serves the CEO's decision-making best?\n\n"
    "Respond with ONLY a valid JSON object. No markdown, no explanation outside JSON.\n"
    "Required fields: proposed_value (dict), evidence_cited (list), "
    "concessions (list), rationale (str)"
)

EVALUATION_SYSTEM_PROMPT = (
    "You are an AI agent evaluating another agent's negotiation position. "
    "Your goal is to reach the most accurate conclusion, not to reject reflexively.\n\n"
    "Rules:\n"
    "1. If their evidence is stronger than yours — accept (verdict: 'accept')\n"
    "2. If you see a middle ground — counter with a specific compromise "
    "(verdict: 'counter')\n"
    "3. Only reject if you have CONCRETE contradicting evidence "
    "(verdict: 'reject_with_evidence')\n"
    "4. Always list areas of agreement — this builds toward compromise\n"
    "5. Be specific with numbers — vague disagreement is not allowed\n\n"
    "Respond with ONLY a valid JSON object. No markdown, no explanation outside JSON.\n"
    "Required fields: verdict (str), reasoning (str), areas_of_agreement (list)\n"
    "Conditional: counter_proposal (if counter), rejection_evidence (if reject)"
)
