"""
Council Agent — 5-persona deliberation gate for high-stakes business plan sections.

Sits between gated child agents and the Mother Agent. Reviews outputs through
5 lenses (Skeptic, Architect, Visionary, Stranger, Operator), synthesizes a
verdict, and either passes the output to Mother or sends revision instructions
back to the child agent.

Personas run on Haiku (parallel), synthesizer runs on Sonnet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import os
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from memory.supabase_client import SupabaseClient
from agents.phase2.llm_utils import strip_markdown_json, signal_ready
from config.phase2.council_config import (
    COUNCIL_PERSONAS,
    MAX_COUNCIL_REVISIONS,
    SYNTHESIZER_PROMPT,
)
from schemas.outputs.council_agent import PersonaCritique, CouncilReport, CouncilVerdict

logger = logging.getLogger(__name__)


class ListenBehaviour(CyclicBehaviour):

    async def run(self):
        msg = await self.receive(timeout=5)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        task_id = msg.get_metadata("task_id")
        session_id = msg.get_metadata("session_id")
        pipeline_run_id = msg.get_metadata("pipeline_run_id")
        content = json.loads(msg.body)
        sender = str(msg.sender)

        if performative == "inform":
            await self.agent.handle_review(
                task_id, session_id, pipeline_run_id, sender, content
            )


class CouncilAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.db = SupabaseClient()
        self.haiku_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.sonnet_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[CouncilAgent] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "council_agent")

    async def _send_msg(self, msg: Message):
        class _Send(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _Send()
        self.add_behaviour(b)
        await b.join(timeout=10)

    async def handle_review(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        sender: str,
        content: dict,
    ):
        """Receive output from a gated child agent and run council review."""
        output = content.get("output", {})
        section_number = content.get("section_number", "")
        agent_name = content.get("agent_name", sender.split("@")[0])
        is_revision = content.get("is_revision", False)

        attempt_key = f"council_attempt:{task_id}"
        attempt = int(self.redis.client.get(attempt_key) or 1)
        if is_revision:
            attempt = int(self.redis.client.get(attempt_key) or 1)

        section_name = self._get_section_name(section_number)
        logger.info(
            "[CouncilAgent] Reviewing section %s from %s (attempt %d)",
            section_number, agent_name, attempt,
        )

        self._notify_alex_review_start(session_id, section_name)

        cross_context = content.get("cross_section_context", {})
        cross_context_str = json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk in (
                "confidence_score", "opportunity_description", "strategic_implications",
                "revenue_assumptions", "break_even_analysis",
            )} for k, v in cross_context.items() if isinstance(v, dict)},
            default=str,
        )[:4000] if cross_context else "No other sections available yet."

        output_json = json.dumps(output, indent=2, default=str)[:6000]

        reviews = await asyncio.gather(
            self._run_persona("skeptic", section_number, section_name, agent_name, output_json, cross_context_str),
            self._run_persona("architect", section_number, section_name, agent_name, output_json, cross_context_str),
            self._run_persona("visionary", section_number, section_name, agent_name, output_json, cross_context_str),
            self._run_persona("stranger", section_number, section_name, agent_name, output_json, cross_context_str),
            self._run_persona("operator", section_number, section_name, agent_name, output_json, cross_context_str),
        )

        verdict = await self._synthesize(reviews)

        self._notify_alex_deliberation(session_id, section_name, reviews, verdict)

        report = CouncilReport(
            section_number=section_number,
            agent_name=agent_name,
            attempt=attempt,
            score=verdict.score,
            decision=verdict.decision,
            critiques=[PersonaCritique(**r) for r in reviews],
            improvements_made=verdict.improvements if is_revision else [],
            revision_instructions=verdict.feedback if verdict.decision == "revise" else None,
        )
        self._store_report(session_id, pipeline_run_id, report)

        if verdict.decision == "pass":
            logger.info("[CouncilAgent] Section %s PASSED (score %.1f)", section_number, verdict.score)
            self._notify_alex_pass(session_id, section_name, verdict, is_revision)
            await self._forward_to_mother(
                task_id, session_id, pipeline_run_id, section_number, output, verdict
            )

        elif verdict.decision == "revise" and attempt < MAX_COUNCIL_REVISIONS:
            logger.info("[CouncilAgent] Section %s needs REVISION (attempt %d)", section_number, attempt)
            self.redis.client.set(attempt_key, str(attempt + 1), ex=3600)
            self._notify_alex_revise(session_id, section_name, verdict)
            await self._send_back_to_child(
                task_id, session_id, pipeline_run_id, sender, output, verdict, reviews
            )

        else:
            if attempt >= MAX_COUNCIL_REVISIONS:
                logger.warning(
                    "[CouncilAgent] Section %s hit max revisions — passing with warnings",
                    section_number,
                )
                self._notify_alex_escalate(session_id, section_name, verdict)
            await self._forward_to_mother(
                task_id, session_id, pipeline_run_id, section_number, output, verdict
            )

    async def _run_persona(
        self,
        persona_key: str,
        section_number: str,
        section_name: str,
        agent_name: str,
        output_json: str,
        cross_context_str: str,
    ) -> dict:
        """Run a single persona review using Haiku."""
        persona = COUNCIL_PERSONAS[persona_key]
        user_prompt = persona["user_prompt_template"].format(
            section_name=section_name,
            section_number=section_number,
            agent_name=agent_name,
            output_json=output_json,
            cross_context=cross_context_str,
        )

        try:
            response = self.bedrock.converse(
                modelId=self.haiku_model,
                system=[{"text": persona["system_prompt"]}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 512},
            )
            raw = response["output"]["message"]["content"][0]["text"]
            text = strip_markdown_json(raw)
            parsed = json.loads(text)
            return {
                "persona": persona_key,
                "top_finding": parsed.get("top_finding", "No issues found"),
                "severity": parsed.get("severity", "none"),
                "detail": parsed.get("detail", ""),
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("[CouncilAgent] Persona %s parse failed: %s", persona_key, e)
            return {
                "persona": persona_key,
                "top_finding": "Review could not be completed",
                "severity": "none",
                "detail": str(e),
            }
        except Exception as e:
            logger.error("[CouncilAgent] Persona %s LLM failed: %s", persona_key, e)
            return {
                "persona": persona_key,
                "top_finding": "Review could not be completed",
                "severity": "none",
                "detail": str(e),
            }

    async def _synthesize(self, reviews: list) -> CouncilVerdict:
        """Synthesize 5 persona reviews into a single verdict using Sonnet."""
        reviews_json = json.dumps(reviews, indent=2)
        prompt = SYNTHESIZER_PROMPT.format(reviews_json=reviews_json)

        try:
            response = self.bedrock.converse(
                modelId=self.sonnet_model,
                system=[{"text": "You synthesize multiple review perspectives into one actionable verdict. Respond with ONLY valid JSON."}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 1024},
            )
            raw = response["output"]["message"]["content"][0]["text"]
            text = strip_markdown_json(raw)
            parsed = json.loads(text)
            return CouncilVerdict(
                decision=parsed.get("decision", "pass"),
                score=float(parsed.get("score", 7.0)),
                critical_count=int(parsed.get("critical_count", 0)),
                minor_count=int(parsed.get("minor_count", 0)),
                feedback=parsed.get("feedback", ""),
                improvements=parsed.get("improvements", []),
            )
        except Exception as e:
            logger.error("[CouncilAgent] Synthesizer failed: %s — defaulting to pass", e)
            critical_count = sum(1 for r in reviews if r.get("severity") == "critical")
            minor_count = sum(1 for r in reviews if r.get("severity") == "minor")
            decision = "revise" if critical_count > 0 or minor_count >= 3 else "pass"
            score = 10.0 - (critical_count * 2) - (minor_count * 0.5)
            return CouncilVerdict(
                decision=decision,
                score=max(score, 0.0),
                critical_count=critical_count,
                minor_count=minor_count,
                feedback="Synthesizer LLM failed — verdict based on severity counts.",
                improvements=[],
            )

    async def _forward_to_mother(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        section_number: str,
        output: dict,
        verdict: CouncilVerdict,
    ):
        """Forward approved output to Mother Agent via Redis task_output key."""
        output["_council_score"] = verdict.score
        output["_council_decision"] = verdict.decision
        if verdict.decision != "pass":
            output["_council_warnings"] = verdict.feedback

        self.redis.client.set(
            f"task_output:{task_id}",
            json.dumps(output, default=str),
            ex=3600,
        )

    async def _send_back_to_child(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        child_jid: str,
        original_output: dict,
        verdict: CouncilVerdict,
        reviews: list,
    ):
        """Send revision instructions back to the originating child agent."""
        msg = Message(to=child_jid)
        msg.set_metadata("performative", "revise")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "revision_instructions": verdict.feedback,
            "original_output": original_output,
            "persona_critiques": reviews,
            "council_score": verdict.score,
        })
        await self._send_msg(msg)

    def _store_report(self, session_id: str, pipeline_run_id: str, report: CouncilReport):
        """Store council report in Supabase and Redis."""
        report_dict = report.model_dump()
        try:
            self.db.client.table("council_reports").insert({
                "session_id": session_id,
                "pipeline_run_id": pipeline_run_id,
                "section_number": report.section_number,
                "agent_name": report.agent_name,
                "attempt": report.attempt,
                "score": report.score,
                "decision": report.decision,
                "critiques": report_dict["critiques"],
                "improvements_made": report.improvements_made,
                "revision_instructions": report.revision_instructions,
            }).execute()
        except Exception as e:
            logger.error("[CouncilAgent] Failed to store report in Supabase: %s", e)

        redis_key = f"session:{session_id}:council:{report.section_number}"
        self.redis.client.set(redis_key, json.dumps(report_dict, default=str), ex=86400)

    def _notify_alex_review_start(self, session_id: str, section_name: str):
        """Tell Alex the Council is reviewing a section."""
        self._send_telegram(session_id, f"\U0001f50d Council is reviewing: {section_name}")

    def _notify_alex_deliberation(
        self,
        session_id: str,
        section_name: str,
        reviews: list,
        verdict: CouncilVerdict,
    ):
        """Send full Council deliberation to Alex via Telegram."""
        icons = {
            "skeptic": "⚠️",
            "architect": "\U0001f3d7️",
            "visionary": "\U0001f4a1",
            "stranger": "❓",
            "operator": "\U0001f527",
        }
        lines = [f"\U0001f4cb Council Review: {section_name}\n"]
        for review in reviews:
            persona = review["persona"]
            icon = icons.get(persona, "•")
            finding = review["top_finding"][:120]
            severity = review.get("severity", "none")
            sev_tag = f" [{severity.upper()}]" if severity != "none" else ""
            lines.append(f"┊ {icon} {persona.title()}: {finding}{sev_tag}")

        if verdict.decision == "pass":
            lines.append(f"\n✅ Verdict: PASS (score {verdict.score:.1f}/10)")
        else:
            lines.append(f"\n\U0001f504 Verdict: REVISE ({verdict.critical_count} critical issues)")

        self._send_telegram(session_id, "\n".join(lines))

    def _notify_alex_pass(
        self, session_id: str, section_name: str, verdict: CouncilVerdict, is_revision: bool
    ):
        """Notify Alex that a section passed."""
        suffix = " (Revised)" if is_revision else ""
        improvements = ""
        if verdict.improvements:
            improvements = "\nImprovements:\n" + "\n".join(f"• {i}" for i in verdict.improvements[:5])
        self._send_telegram(
            session_id,
            f"✅ Council: {section_name}{suffix} — Score {verdict.score:.1f}/10{improvements}",
        )

    def _notify_alex_revise(self, session_id: str, section_name: str, verdict: CouncilVerdict):
        """Notify Alex that a section is being sent back for revision."""
        self._send_telegram(
            session_id,
            f"\U0001f504 Council: {section_name} sent back for revision\n"
            f"Issues: {verdict.feedback[:200]}",
        )

    def _notify_alex_escalate(self, session_id: str, section_name: str, verdict: CouncilVerdict):
        """Notify Alex that max revisions were hit."""
        self._send_telegram(
            session_id,
            f"⚠️ Council: {section_name} hit max revisions — passing with warnings\n"
            f"Score: {verdict.score:.1f}/10\nRemaining issues: {verdict.feedback[:200]}",
        )

    def _send_telegram(self, session_id: str, message: str):
        """Send message to Alex via Telegram."""
        try:
            from tools.telegram_handler import send_message
            session = self.db.client.table("sessions") \
                .select("telegram_chat_id") \
                .eq("id", session_id).execute()
            if session.data:
                chat_id = session.data[0].get("telegram_chat_id")
                if chat_id:
                    asyncio.create_task(send_message(chat_id, message))
        except Exception as e:
            logger.warning("[CouncilAgent] Telegram send failed: %s", e)

    def _get_section_name(self, section_number: str) -> str:
        """Map section number to human name."""
        names = {
            "1": "The Opportunity",
            "3": "External Environment",
            "4": "Organisation & Team",
            "5": "SWOT Analysis",
            "8": "Marketing Strategy",
            "10": "Operations",
            "12": "Financial Plan",
            "13": "Launch & Contingency",
            "executive_summary": "Executive Summary",
        }
        return names.get(section_number, f"Section {section_number}")

    def _log_event(self, session_id: str, action: str, detail: str):
        """Log council event."""
        try:
            self.db.client.table("events_logs").insert({
                "agent_name": "council_agent",
                "action": action,
                "session_id": session_id,
                "output_summary": detail[:500],
            }).execute()
        except Exception as e:
            logger.warning("[CouncilAgent] Event log failed: %s", e)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("COUNCIL_AGENT_JID")
    password = os.getenv("COUNCIL_AGENT_PASSWORD")
    if not jid or not password:
        raise ValueError("COUNCIL_AGENT_JID and COUNCIL_AGENT_PASSWORD must be set")
    agent = CouncilAgent(jid=jid, password=password)
    await agent.start(auto_register=True)
    try:
        while agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
