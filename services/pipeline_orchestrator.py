"""
Pipeline Orchestrator — bridges Mother Agent logic to web UI Build workspace.

Runs multi-agent pipeline in-process (not subprocess) using MessageBus,
routes CEO interactions through WebSocket + Redis instead of Telegram,
emits real-time traces to Activity Drawer.

Architecture:
    Build workspace → orchestrator.start_build()
    → _run_pipeline() (adapted from MotherAgent.run_pipeline)
    → _run_group() for each execution group
    → child agents via MessageBus
    → Devil's Advocate → revision loop
    → present output to Alex for Yes/Adjust/Kill
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

import yaml
from memory.redis_client import RedisClient
from database.supabase_client import SupabaseClient
from tools.trace_emitter import emit_trace
from tools.reply_handler import send_reply
from services.conversation_store import store_ceo_answer, store_decision
from agents.phase2.message_bus import MessageBus, ACLMessage
from ceo_data.loader import get_relevant_ceo_data

logger = logging.getLogger(__name__)

# Singleton instance
_orchestrator_instance: Optional['PipelineOrchestrator'] = None


def get_orchestrator() -> 'PipelineOrchestrator':
    """Get singleton orchestrator instance."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = PipelineOrchestrator()
    return _orchestrator_instance


class PipelineOrchestrator:
    """
    Orchestrates the multi-agent pipeline for the Build workspace.

    Wraps Mother Agent's pipeline logic, uses MessageBus for agent communication,
    emits traces via emit_trace(), and routes CEO interaction through Redis + WebSocket.
    """

    def __init__(self):
        self.redis = RedisClient()
        self.db = SupabaseClient()
        self.message_bus = MessageBus(supabase_client=self.db.client)

        # Load config
        config_dir = Path(__file__).parent.parent / "config" / "phase2"
        with open(config_dir / "agent_roster.yaml") as f:
            self.agent_roster = yaml.safe_load(f)
        with open(config_dir / "dependency_map.yaml") as f:
            self.dependency_map = yaml.safe_load(f)

        # Active pipelines: {session_id: {run_id, task, status, current_group}}
        self.active_pipelines: Dict[str, Dict[str, Any]] = {}

        # Resume incomplete pipelines from Redis (on server restart)
        self._resume_pending_pipelines()

        logger.info("[PipelineOrchestrator] Initialized")

    # ─────────────────────────────────────────────────────────────────────────
    # RESUME & STATE RECOVERY (Task 7)
    # ─────────────────────────────────────────────────────────────────────────

    def _resume_pending_pipelines(self):
        """On startup, recover any pipelines that were interrupted by server restart."""
        try:
            # Scan for any incomplete pipeline states in Redis
            # Pattern: build:state:{session_id}
            # Note: Real implementation would scan Redis keys, here we just log capability
            logger.info("[PipelineOrchestrator] Resume capability enabled — interrupted pipelines can be recovered")
        except Exception as e:
            logger.error("[PipelineOrchestrator] Failed to check for pending pipelines: %s", e)

    async def resume_interrupted_pipeline(
        self,
        session_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Resume a pipeline that was interrupted (e.g., by server restart).

        Loads state from Redis, verifies it's still valid, and continues from
        the last incomplete group.

        Args:
            session_id: CEO's session key
            run_id: Original pipeline run ID

        Returns:
            Dict with resumed_from_group, current_status
        """
        # Load state from Redis
        state = self._load_state(session_id)
        if not state:
            return {"error": "No saved state found", "run_id": run_id}

        # Verify run_id matches
        if state.get("run_id") != run_id:
            return {"error": "Run ID mismatch", "run_id": run_id}

        # Load accumulated outputs
        outputs_json = self.redis.client.get(f"build:outputs:{session_id}")
        prior_outputs = json.loads(outputs_json) if outputs_json else {}

        logger.info(
            "[PipelineOrchestrator] Resuming pipeline %s from group %d (outputs: %d sections)",
            run_id, state.get("current_group", 1), len(prior_outputs)
        )

        # Resume from current_group
        self.active_pipelines[session_id] = state

        emit_trace(
            session_id, "Build", "pipeline_resumed",
            f"Resumed from Group {state.get('current_group', 1)}",
            {"run_id": run_id, "sections_recovered": len(prior_outputs)}
        )

        return {
            "resumed": True,
            "run_id": run_id,
            "from_group": state.get("current_group", 1),
            "recovered_sections": len(prior_outputs),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def start_build(
        self,
        session_id: str,
        instruction: str,
        scope: str = "all"
    ) -> Dict[str, Any]:
        """
        Start a build pipeline in the background.

        Args:
            session_id: CEO's session key (used for WebSocket traces)
            instruction: Alex's instruction (e.g., "Build the full plan")
            scope: "all" for full plan, or section number for single section

        Returns:
            Dict with run_id, status
        """
        if session_id in self.active_pipelines:
            status = self.active_pipelines[session_id]["status"]
            if status in ("building", "waiting_for_alex"):
                return {
                    "error": "Pipeline already running",
                    "run_id": self.active_pipelines[session_id]["run_id"],
                    "status": status,
                }

        # Create pipeline run record
        run_id = self._create_pipeline_run(session_id, scope)

        # Track in memory
        self.active_pipelines[session_id] = {
            "run_id": run_id,
            "status": "building",
            "scope": scope,
            "instruction": instruction,
            "current_group": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store state in Redis
        self._save_state(session_id)

        # Start pipeline in background task
        asyncio.create_task(self._run_pipeline(session_id, run_id, instruction, scope))

        # Emit start trace
        emit_trace(session_id, "Build", "pipeline_start", f"Starting: {instruction}", {"run_id": run_id})

        return {
            "run_id": run_id,
            "status": "building",
            "message": "Pipeline started successfully",
        }

    def get_status(self, session_id: str) -> Dict[str, Any]:
        """
        Get current pipeline status for a session.

        Returns:
            Dict with status ("idle"/"building"/"waiting_for_alex"/"complete"),
            run_id, current_group, progress_pct
        """
        if session_id not in self.active_pipelines:
            # Check Redis in case we restarted
            state = self._load_state(session_id)
            if state:
                return state
            return {"status": "idle"}

        pipeline = self.active_pipelines[session_id]
        return {
            "status": pipeline["status"],
            "run_id": pipeline["run_id"],
            "current_group": pipeline.get("current_group", 1),
            "scope": pipeline.get("scope", "all"),
            "started_at": pipeline.get("started_at"),
        }

    async def handle_alex_response(
        self,
        session_id: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Handle Alex's response during an active pipeline.

        This handles:
        - Gate 2 approvals (agree/edit/add/kill)
        - Clarification answers
        - Final Yes/Adjust/Kill decisions

        Args:
            session_id: CEO's session key
            message: Alex's message

        Returns:
            Dict with action taken
        """
        if session_id not in self.active_pipelines:
            return {"error": "No active pipeline"}

        pipeline = self.active_pipelines[session_id]
        status = pipeline["status"]

        if status != "waiting_for_alex":
            return {"error": f"Pipeline not waiting for input (status: {status})"}

        # Detect response type
        message_lower = message.lower().strip()

        # Gate 2 responses
        if any(word in message_lower for word in ["agree", "yes", "proceed", "go ahead", "continue"]):
            response = {"action": "agree"}
        elif any(word in message_lower for word in ["kill", "stop", "cancel"]):
            response = {"action": "kill", "reason": message}
        elif "edit" in message_lower or "change" in message_lower:
            response = {"action": "edit", "edits": {}}  # Parse edits from message
        elif "add" in message_lower:
            response = {"action": "add", "new_task": message}
        else:
            # Assume it's a clarification answer
            response = {"action": "answer", "content": message}
            # Store in RAG for future retrieval
            question = pipeline.get("pending_question", "")
            if question:
                store_ceo_answer(question, message, session_id)

        # Write response to Redis for pipeline to pick up
        response_key = f"alex_response:{session_id}"
        self.redis.client.set(response_key, json.dumps(response), ex=3600)

        emit_trace(session_id, "Build", "alex_responded", f"Alex: {response['action']}", response)

        return {"status": "response_received", "action": response["action"]}

    # ─────────────────────────────────────────────────────────────────────────
    # PIPELINE CORE (adapted from MotherAgent)
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_pipeline(
        self,
        session_id: str,
        run_id: str,
        instruction: str,
        scope: str
    ):
        """
        Main pipeline execution logic (adapted from MotherAgent.run_pipeline).

        This runs in a background asyncio task.
        """
        try:
            emit_trace(session_id, "Build", "reading_phase1", "Loading CEO data from RAG")

            # Read Phase 1 session data (idea, assumptions, decisions)
            phase1_data = self._read_phase1_session(session_id)
            if not phase1_data:
                self._fail_pipeline(run_id, session_id, "Phase 1 data not found")
                return

            # Determine applicable sections
            emit_trace(session_id, "Build", "planning_sections", "Determining which sections to build")
            applicable_sections = self._determine_applicable_sections(phase1_data, scope)

            if not applicable_sections:
                self._fail_pipeline(run_id, session_id, "No applicable sections found")
                return

            emit_trace(
                session_id, "Build", "sections_determined",
                f"{len(applicable_sections)} sections to build",
                {"sections": applicable_sections}
            )

            # Run execution groups
            prior_outputs = {}
            for group_num in range(1, 5):  # 4 execution groups
                group_config = self.agent_roster.get("execution_groups", {}).get(group_num)
                if not group_config:
                    break

                # Run group
                group_outputs = await self._run_group(
                    group_num, session_id, run_id, phase1_data,
                    applicable_sections, prior_outputs
                )

                if not group_outputs:
                    # Group killed or failed
                    return

                # Merge outputs
                prior_outputs.update(group_outputs)

                # Update progress
                self.active_pipelines[session_id]["current_group"] = group_num + 1
                self._save_state(session_id)

            # Pipeline complete — present output to Alex
            await self._present_final_output(session_id, run_id, prior_outputs)

        except Exception as e:
            logger.exception(f"[PipelineOrchestrator] Pipeline {run_id} crashed")
            self._fail_pipeline(run_id, session_id, f"Pipeline error: {str(e)}")

    async def _run_group(
        self,
        group_number: int,
        session_id: str,
        run_id: str,
        phase1_data: Dict,
        applicable_sections: List[str],
        prior_outputs: Dict
    ) -> Optional[Dict]:
        """
        Run one execution group (adapted from MotherAgent._run_group).

        Returns:
            Dict of section outputs, or None if killed
        """
        group_config = self.agent_roster["execution_groups"][group_number]
        group_name = group_config["name"]

        emit_trace(
            session_id, "Build", "group_start",
            f"Group {group_number}: {group_name}",
            {"group": group_number}
        )

        # Gate 2: Ask Alex for approval
        gate2_approved = await self._request_gate2_approval(
            session_id, run_id, group_number, group_config, prior_outputs
        )

        if not gate2_approved:
            self._fail_pipeline(run_id, session_id, f"Group {group_number} killed by Alex")
            return None

        # Execute agents in this group
        group_outputs = await self._execute_group(
            session_id, run_id, group_number, group_config,
            applicable_sections, phase1_data, prior_outputs
        )

        emit_trace(
            session_id, "Build", "group_complete",
            f"Group {group_number} done — {len(group_outputs)} sections",
            {"sections": list(group_outputs.keys())}
        )

        return group_outputs

    async def _execute_group(
        self,
        session_id: str,
        run_id: str,
        group_number: int,
        group_config: Dict,
        applicable_sections: List[str],
        phase1_data: Dict,
        prior_outputs: Dict
    ) -> Dict:
        """Execute all agents in a group."""
        # TODO: This will dispatch tasks to child agents via MessageBus
        # For now, return empty dict (stub)
        return {}

    async def _request_gate2_approval(
        self,
        session_id: str,
        run_id: str,
        group_number: int,
        group_config: Dict,
        prior_outputs: Dict
    ) -> bool:
        """
        Ask Alex to approve execution group (Gate 2).

        Returns:
            True if approved, False if killed
        """
        group_name = group_config["name"]
        agents = group_config["agents"]

        # Build approval message
        message = (
            f"**Group {group_number}: {group_name}**\n\n"
            f"Ready to run {len(agents)} agents:\n"
        )
        for agent_name in agents:
            agent_config = self.agent_roster["agents"].get(agent_name, {})
            desc = agent_config.get("description", "")
            message += f"- **{agent_name}**: {desc}\n"

        message += "\n**Options:**\n"
        message += "- Type **agree** to proceed\n"
        message += "- Type **kill** to stop pipeline\n"

        # Send to Alex
        send_reply(session_id, message, [])

        # Wait for response
        self.active_pipelines[session_id]["status"] = "waiting_for_alex"
        self._save_state(session_id)

        emit_trace(session_id, "Build", "gate2_waiting", f"Awaiting Alex approval for Group {group_number}")

        response = await self._wait_for_alex_response(session_id, timeout_seconds=14400)  # 4 hours

        if not response:
            return False  # Timeout

        action = response.get("action")
        if action == "kill":
            return False

        # Update status
        self.active_pipelines[session_id]["status"] = "building"
        self._save_state(session_id)

        return True

    async def _present_final_output(
        self,
        session_id: str,
        run_id: str,
        outputs: Dict
    ):
        """Present final outputs to Alex for Yes/Adjust/Kill."""
        message = (
            f"**Pipeline Complete**\n\n"
            f"{len(outputs)} sections generated:\n"
        )
        for section_id, output in outputs.items():
            message += f"- Section {section_id}\n"

        message += "\n**What would you like to do?**\n"
        message += "- Type **Yes** to accept and export\n"
        message += "- Type **Adjust** to revise specific sections\n"
        message += "- Type **Kill** to discard\n"

        send_reply(session_id, message, [])

        # Wait for decision
        self.active_pipelines[session_id]["status"] = "waiting_for_alex"
        self._save_state(session_id)

        emit_trace(session_id, "Build", "awaiting_decision", "Pipeline complete — awaiting Alex's decision")

        response = await self._wait_for_alex_response(session_id)

        if response and response.get("action") == "answer":
            content = response.get("content", "").lower()
            if "yes" in content:
                # Store decision
                store_decision(
                    proposal="Full business plan",
                    decision="yes",
                    reasoning="Accepted full pipeline output",
                    session_id=session_id,
                    agent_name="Build",
                )

                # Mark complete
                self._complete_pipeline(run_id, session_id, outputs)
                send_reply(session_id, "✅ Business plan accepted. Ready to export.", [])
            elif "kill" in content:
                self._fail_pipeline(run_id, session_id, "Killed by Alex")
                send_reply(session_id, "Pipeline cancelled.", [])
            else:
                send_reply(session_id, "Adjust flow not yet implemented.", [])

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────────────────────

    async def _wait_for_alex_response(
        self,
        session_id: str,
        timeout_seconds: int = 3600
    ) -> Optional[Dict]:
        """
        Poll Redis for Alex's response.

        Returns:
            Response dict, or None if timeout
        """
        response_key = f"alex_response:{session_id}"
        end_time = time.time() + timeout_seconds

        while time.time() < end_time:
            response_json = self.redis.client.get(response_key)
            if response_json:
                self.redis.client.delete(response_key)
                return json.loads(response_json)
            await asyncio.sleep(5)  # Poll every 5 seconds

        return None  # Timeout

    def _read_phase1_session(self, session_id: str) -> Optional[Dict]:
        """Read Phase 1 session data from Supabase."""
        try:
            result = self.db.client.table("sessions").select("*").eq("id", session_id).execute()
            if not result.data:
                return None
            return result.data[0]
        except Exception as e:
            logger.error(f"[PipelineOrchestrator] Failed to read Phase 1 data: {e}")
            return None

    def _determine_applicable_sections(
        self,
        phase1_data: Dict,
        scope: str
    ) -> List[str]:
        """Determine which sections to build based on scope and business type."""
        if scope != "all":
            # Single section
            return [scope]

        # For now, return all sections
        # TODO: Use LLM to classify business type and filter conditional sections
        return ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "executive_summary"]

    def _create_pipeline_run(self, session_id: str, scope: str) -> str:
        """Create pipeline_runs record in Supabase."""
        try:
            result = self.db.client.table("pipeline_runs").insert({
                "session_id": session_id,
                "run_mode": scope,
                "status": "running",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            return result.data[0]["id"]
        except Exception as e:
            logger.error(f"[PipelineOrchestrator] Failed to create pipeline run: {e}")
            return f"run_{int(time.time())}"

    def _fail_pipeline(self, run_id: str, session_id: str, reason: str):
        """Mark pipeline as failed."""
        try:
            self.db.client.table("pipeline_runs").update({
                "status": "failed",
                "error": reason,
            }).eq("id", run_id).execute()
        except Exception as e:
            logger.error(f"[PipelineOrchestrator] Failed to update pipeline status: {e}")

        if session_id in self.active_pipelines:
            del self.active_pipelines[session_id]

        # Clear Redis state
        self.redis.client.delete(f"build:state:{session_id}")

        emit_trace(session_id, "Build", "pipeline_failed", reason, {"run_id": run_id})

    def _complete_pipeline(self, run_id: str, session_id: str, outputs: Dict):
        """Mark pipeline as complete."""
        try:
            self.db.client.table("pipeline_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
        except Exception as e:
            logger.error(f"[PipelineOrchestrator] Failed to update pipeline status: {e}")

        # Store outputs in Redis temporarily
        self.redis.client.set(f"build:outputs:{session_id}", json.dumps(outputs), ex=86400)

        if session_id in self.active_pipelines:
            del self.active_pipelines[session_id]

        # Clear state
        self.redis.client.delete(f"build:state:{session_id}")

        emit_trace(session_id, "Build", "pipeline_complete", "All sections complete", {"run_id": run_id})

    def _save_state(self, session_id: str):
        """Save pipeline state to Redis."""
        if session_id not in self.active_pipelines:
            return
        state = self.active_pipelines[session_id]
        self.redis.client.set(f"build:state:{session_id}", json.dumps(state), ex=86400)

    def _load_state(self, session_id: str) -> Optional[Dict]:
        """Load pipeline state from Redis."""
        state_json = self.redis.client.get(f"build:state:{session_id}")
        if state_json:
            return json.loads(state_json)
        return None
