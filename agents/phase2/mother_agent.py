import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import os
import time
import uuid
import yaml
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour, PeriodicBehaviour
from spade.message import Message

import boto3

from memory.supabase_client import SupabaseClient
from memory.redis_client import RedisClient
from tools.trace_emitter import emit_trace
from agents.phase2.intelligence_engine import IntelligenceEngine
from agents.phase2.learning_engine import LearningEngine
from agents.phase2.document_compiler import DocumentCompiler
from agents.phase2.pipeline_checkpoints import should_continue_pipeline, evaluate_checkpoint
from agents.phase2.negotiation import NegotiationManager, should_negotiate
from agents.phase2.quality_gate import QualityGate
from agents.phase2.coherence_auditor import CoherenceAuditor
from agents.phase2.conflict_resolver import ConflictResolver
from config.phase2.council_config import COUNCIL_GATED_SECTIONS
from ceo_data.loader import load_all_ceo_data, get_relevant_ceo_data


def load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


DEPENDENCY_MAP = load_yaml("config/phase2/dependency_map.yaml")
AGENT_ROSTER = load_yaml("config/phase2/agent_roster.yaml")
GAP_RULES = load_yaml("config/phase2/gap_resolution_rules.yaml")

CONSTITUTION_PATH = "operating-rules/system_constitution.md"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: load constitution text
# ─────────────────────────────────────────────────────────────────────────────
def load_constitution() -> str:
    with open(CONSTITUTION_PATH, "r") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: send ACL message between Spade agents
# ─────────────────────────────────────────────────────────────────────────────
async def send_acl(
    sender,
    to_jid: str,
    performative: str,
    content: dict,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
):
    msg = Message(to=to_jid)
    msg.set_metadata("performative", performative)
    msg.set_metadata("task_id", task_id or "")
    msg.set_metadata("session_id", session_id or "")
    msg.set_metadata("pipeline_run_id", pipeline_run_id or "")
    msg.body = json.dumps(content)

    if isinstance(sender, Agent):
        class _SendBehaviour(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _SendBehaviour()
        sender.add_behaviour(b)
        await b.join(timeout=10)
    else:
        await sender.send(msg)

    try:
        db = SupabaseClient()
        from_jid = str(sender.agent.jid) if hasattr(sender, "agent") else str(sender.jid)
        db.client.table("agent_messages").insert({
            "from_agent": from_jid,
            "to_agent": to_jid,
            "performative": performative,
            "content": content,
            "pipeline_run_id": pipeline_run_id,
            "session_id": session_id,
            "task_id": task_id,
        }).execute()
    except Exception as e:
        print(f"[send_acl] Failed to log message: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour 1: CyclicBehaviour — listens for all incoming agent messages
# ─────────────────────────────────────────────────────────────────────────────
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
        from_agent = str(msg.sender)

        print(f"[MotherAgent] Received {performative} from {from_agent}")

        if performative == "inform":
            await self.agent.handle_inform(
                task_id, session_id, pipeline_run_id, from_agent, content
            )
        elif performative == "propose":
            await self.agent.handle_propose(
                task_id, session_id, pipeline_run_id, from_agent, content
            )
        elif performative == "refuse":
            await self.agent.handle_refuse(
                task_id, session_id, pipeline_run_id, from_agent, content
            )
        elif performative == "escalate":
            await self.agent.handle_escalate(
                task_id, session_id, pipeline_run_id, from_agent, content
            )
        elif performative == "status_update":
            await self.agent.handle_status_update(
                task_id, session_id, pipeline_run_id, from_agent, content
            )


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour 2: OneShotBehaviour — task planning for one session
# ─────────────────────────────────────────────────────────────────────────────
class PlanBehaviour(OneShotBehaviour):

    def __init__(self, session_id: str, run_mode: str = "full_pipeline"):
        super().__init__()
        self.session_id = session_id
        self.run_mode = run_mode

    async def run(self):
        await self.agent.run_pipeline(self.session_id, self.run_mode)


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour 3: PeriodicBehaviour — polls Redis for pipeline triggers
# ─────────────────────────────────────────────────────────────────────────────
class PipelineTriggerBehaviour(PeriodicBehaviour):

    async def run(self):
        if self.agent.active_runs:
            return

        keys = self.agent.redis.client.keys("pipeline_trigger:*")
        if not keys:
            return

        key = keys[0]
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        session_id = key.replace("pipeline_trigger:", "")
        run_mode_raw = self.agent.redis.client.get(key)
        run_mode = "full_pipeline"
        if run_mode_raw:
            run_mode = run_mode_raw.decode("utf-8") if isinstance(run_mode_raw, bytes) else run_mode_raw
        self.agent.redis.client.delete(key)
        print(f"[MotherAgent] Pipeline trigger found for session {session_id} ({run_mode})")
        self.agent.start_pipeline(session_id, run_mode)


# ─────────────────────────────────────────────────────────────────────────────
# Mother Agent
# ─────────────────────────────────────────────────────────────────────────────
class MotherAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.db = SupabaseClient()
        self.redis = RedisClient()
        self.constitution = load_constitution()
        self.dependency_map = DEPENDENCY_MAP
        self.agent_roster = AGENT_ROSTER
        self.gap_rules = GAP_RULES
        self.active_runs: dict = {}
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)
        self.learning = LearningEngine(self.redis, self.db)
        self.compiler = DocumentCompiler(self.bedrock, self.model_id)
        self.quality_gate = QualityGate()
        self.coherence_auditor = CoherenceAuditor()
        self.negotiation_manager = NegotiationManager(self.bedrock, self.model_id)
        self.conflict_resolver = ConflictResolver()

    # ── Agent lifecycle ───────────────────────────────────────────────────────

    async def setup(self):
        print("[MotherAgent] Starting — loading constitution and config")
        self._log_constitution_version()
        listen = ListenBehaviour()
        self.add_behaviour(listen)
        trigger = PipelineTriggerBehaviour(period=5)
        self.add_behaviour(trigger)

        # TASK 1: Auto-resume incomplete pipeline runs on startup
        await self._check_and_resume_incomplete_runs()

        print("[MotherAgent] Ready. Listening for messages and pipeline triggers.")

    async def _check_and_resume_incomplete_runs(self):
        """Check for incomplete pipeline runs from last 24h and resume them."""
        try:
            cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            result = self.db.client.table("pipeline_runs") \
                .select("*") \
                .not_.in_("status", ["completed", "failed"]) \
                .gte("created_at", cutoff) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()

            if not result.data:
                print("[MotherAgent] No incomplete pipeline runs to resume")
                return

            run = result.data[0]
            run_id = run["id"]
            session_id = run["session_id"]

            # Find last completed group
            groups = self.db.client.table("execution_groups") \
                .select("*") \
                .eq("pipeline_run_id", run_id) \
                .order("group_number", desc=True) \
                .execute()

            last_completed_group = 0
            for g in groups.data:
                if g["status"] == "completed":
                    last_completed_group = g["group_number"]
                    break

            resume_from = last_completed_group + 1
            print(f"[MotherAgent] Resuming pipeline run {run_id} from Group {resume_from}")

            # Load prior outputs from completed sections
            prior_outputs = self._load_prior_outputs(run_id)

            # Read phase1 data
            phase1_data = self._read_phase1_session(session_id)
            if not phase1_data:
                print(f"[MotherAgent] Cannot resume — Phase 1 data not found for session {session_id}")
                return

            applicable_sections = self._determine_applicable_sections(phase1_data)
            self.active_runs[session_id] = run_id

            # Resume from the next group
            await self._run_group(
                group_number=resume_from,
                session_id=session_id,
                run_id=run_id,
                phase1_data=phase1_data,
                applicable_sections=applicable_sections,
                prior_outputs=prior_outputs,
            )

        except Exception as e:
            print(f"[MotherAgent] Error checking for incomplete runs: {e}")
            import traceback
            traceback.print_exc()

    def _load_prior_outputs(self, run_id: str) -> dict:
        """Load outputs from completed sections for pipeline resumption."""
        prior_outputs = {}
        try:
            sections = self.db.client.table("bp_section_content") \
                .select("section_number, content") \
                .eq("pipeline_run_id", run_id) \
                .execute()

            for s in sections.data:
                section_num = s.get("section_number")
                content = s.get("content")
                if section_num and content:
                    prior_outputs[section_num] = content

            print(f"[MotherAgent] Loaded {len(prior_outputs)} prior outputs for resume")
        except Exception as e:
            print(f"[MotherAgent] Error loading prior outputs: {e}")

        return prior_outputs

    def _start_child_agents_sync(self):
        import subprocess
        import time
        agents_to_start = [
            ("opportunity_analyst", "agents/phase2/opportunity_analyst.py"),
            ("entrepreneur_team", "agents/phase2/entrepreneur_team.py"),
            ("environment_research", "agents/phase2/environment_research.py"),
            ("organisation_designer", "agents/phase2/organisation_designer.py"),
            ("rd_technology", "agents/phase2/rd_technology.py"),
            ("swot_synthesizer", "agents/phase2/swot_synthesizer.py"),
            ("alliances", "agents/phase2/alliances.py"),
            ("marketing_strategy", "agents/phase2/marketing_strategy.py"),
            ("quality_management", "agents/phase2/quality_management.py"),
            ("operations", "agents/phase2/operations.py"),
            ("hr_plan", "agents/phase2/hr_plan.py"),
            ("financial_modelling", "agents/phase2/financial_modelling.py"),
            ("launch_contingency", "agents/phase2/launch_contingency.py"),
            ("exit_strategy", "agents/phase2/exit_strategy.py"),
            ("summary_agent", "agents/phase2/summary_agent.py"),
            ("devils_advocate", "agents/phase2/devils_advocate.py"),
            ("council_agent", "agents/phase2/council_agent.py"),
        ]
        self._child_processes = []
        for agent_name, agent_path in agents_to_start:
            proc = subprocess.Popen(
                ["python3", agent_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._child_processes.append(proc)
            logger.info("[MotherAgent] Started child agent: %s (pid %d)", agent_path, proc.pid)

        logger.info("[MotherAgent] All child agents launched — waiting for readiness probes")
        self._wait_for_child_readiness(
            [name for name, _ in agents_to_start],
            timeout=30,
        )

    def _wait_for_child_readiness(self, agent_names: list, timeout: int = 30):
        """Poll Redis for readiness keys set by child agents on XMPP connect."""
        import time
        start = time.time()
        ready = set()
        while time.time() - start < timeout:
            for name in agent_names:
                if name in ready:
                    continue
                key = f"agent_ready:{name}"
                if self.redis.client.get(key):
                    ready.add(name)
                    logger.info("[MotherAgent] Child ready: %s", name)
            if len(ready) == len(agent_names):
                logger.info("[MotherAgent] All %d child agents ready", len(ready))
                return
            time.sleep(1)

        not_ready = set(agent_names) - ready
        logger.warning(
            "[MotherAgent] Timeout waiting for agents: %s — proceeding anyway",
            ", ".join(not_ready),
        )

    async def stop(self):
        if hasattr(self, "_child_processes"):
            for proc in self._child_processes:
                try:
                    proc.terminate()
                except Exception:
                    pass
        await super().stop()

    def _log_constitution_version(self):
        """Log which constitution version is active at startup."""
        try:
            self.db.client.table("constitution_versions") \
                .select("version") \
                .eq("is_active", True) \
                .execute()
        except Exception as e:
            print(f"[MotherAgent] Constitution version check failed: {e}")

    # ── Public entry point ────────────────────────────────────────────────────

    def start_pipeline(self, session_id: str, run_mode: str = "full_pipeline"):
        """Called externally when Gate 1 fires. Adds PlanBehaviour."""
        plan = PlanBehaviour(session_id=session_id, run_mode=run_mode)
        self.add_behaviour(plan)

    # ── Pipeline orchestration ────────────────────────────────────────────────

    async def run_pipeline(self, session_id: str, run_mode: str):
        print(f"[MotherAgent] Starting {run_mode} for session {session_id}")
        trace_key = self._get_trace_key(session_id)

        # 1. Create pipeline run record
        run_id = self._create_pipeline_run(session_id, run_mode)
        self.active_runs[session_id] = run_id
        emit_trace(trace_key, "Mother", "pipeline_start", f"Pipeline started — mode: {run_mode}", {"run_id": run_id})

        # 2. Read Phase 1 session output
        phase1_data = self._read_phase1_session(session_id)
        if not phase1_data:
            emit_trace(trace_key, "Mother", "pipeline_failed", "Phase 1 data not found")
            self._fail_pipeline(run_id, "Phase 1 session data not found")
            return

        # 3. Classify business type and determine applicable sections
        emit_trace(trace_key, "Mother", "classifying_sections", "Determining applicable sections via LLM")
        applicable_sections = self._determine_applicable_sections(phase1_data)
        print(f"[MotherAgent] Applicable sections: {applicable_sections}")
        emit_trace(trace_key, "Mother", "sections_classified", f"{len(applicable_sections)} sections applicable", {"sections": applicable_sections})

        # 4. Generate opening narrative and send to Alex via Telegram
        narrative = self._build_opening_narrative(applicable_sections, phase1_data)
        self._send_telegram(session_id, narrative)

        # 5. Generate tasks for Group 1 only (progressive — not all groups upfront)
        await self._run_group(
            group_number=1,
            session_id=session_id,
            run_id=run_id,
            phase1_data=phase1_data,
            applicable_sections=applicable_sections,
        )

    async def _run_group(
        self,
        group_number: int,
        session_id: str,
        run_id: str,
        phase1_data: dict,
        applicable_sections: list,
        prior_outputs: dict = None,
    ):
        if prior_outputs is None:
            prior_outputs = {}

        trace_key = self._get_trace_key(session_id)
        group_config = self.agent_roster.get("execution_groups", {}).get(group_number)
        if not group_config:
            print(f"[MotherAgent] No config for group {group_number} — pipeline complete")
            emit_trace(trace_key, "Mother", "all_groups_done", "All execution groups complete — running coherence audit")
            await self._run_coherence_audit(session_id, run_id, prior_outputs)
            return

        print(f"[MotherAgent] === Starting Group {group_number} ===")
        emit_trace(trace_key, "Mother", "group_start", f"Group {group_number}: {group_config.get('name', '')}", {"group": group_number})

        # Generate tasks for this group
        tasks = self._generate_group_tasks(
            group_number, applicable_sections, prior_outputs, phase1_data
        )
        if not tasks:
            print(f"[MotherAgent] No tasks for group {group_number} — skipping to next")
            emit_trace(trace_key, "Mother", "group_skip", f"Group {group_number} has no tasks — skipping")
            await self._run_group(
                group_number + 1, session_id, run_id,
                phase1_data, applicable_sections, prior_outputs
            )
            return

        # Write tasks to task_readiness table
        task_ids = self._write_tasks(tasks, session_id, run_id, group_number)

        # Run dependency pre-simulation
        sim_result = self._run_pre_simulation(tasks)

        # Build Gate 2 approval package
        gate2_package = self._build_gate2_package(
            group_number, group_config, tasks, sim_result, prior_outputs
        )

        # Create execution group record
        group_id = self._create_execution_group(run_id, group_number, group_config, gate2_package)

        # Send Gate 2 approval request to Alex
        self._request_gate2_approval(session_id, run_id, group_id, gate2_package)
        print(f"[MotherAgent] Group {group_number} Gate 2 sent — waiting for Alex")
        emit_trace(trace_key, "Mother", "gate2_waiting", f"Group {group_number} awaiting Alex approval", {"tasks": [t["task_name"] for t in tasks]})

        # Wait for Alex's Gate 2 response (stored in Redis) — BLOCKS here
        response = await self._wait_for_gate2_response(session_id, group_id)
        print(f"[MotherAgent] Group {group_number} Gate 2 response: {response['action']}")
        emit_trace(trace_key, "Mother", "gate2_response", f"Alex responded: {response['action']}", {"action": response["action"]})

        if response["action"] == "kill":
            self._kill_group(run_id, group_id, session_id)
            return

        if response["action"] == "edit":
            tasks = self._apply_edit(tasks, response["edits"])
            self._recheck_dependencies(tasks)
            for task_name, edit_details in response.get("edits", {}).items():
                self.learning.record_edit(
                    session_id=session_id,
                    section_number=str(group_number),
                    field_edited=task_name,
                    original_value=str(edit_details.get("original", "")),
                    new_value=str(edit_details.get("new", edit_details)),
                )

        if response["action"] == "add":
            new_task = self._classify_new_task(response["new_task"], applicable_sections)
            tasks.append(new_task)
            self._recheck_dependencies(tasks)

        # Mark group as approved
        self._update_group_status(group_id, "approved")
        print(f"[MotherAgent] Group {group_number} approved — executing tasks")
        emit_trace(trace_key, "Mother", "group_executing", f"Group {group_number} executing {len(tasks)} tasks")

        # Execute tasks (parallel where group config allows) — BLOCKS here
        group_outputs = await self._execute_group(
            tasks, task_ids, group_id, session_id, run_id, group_config
        )
        print(f"[MotherAgent] Group {group_number} execution complete — {len(group_outputs)} outputs")
        emit_trace(trace_key, "Mother", "group_complete", f"Group {group_number} done — {len(group_outputs)} sections produced", {"sections": list(group_outputs.keys())})

        # Merge outputs with prior outputs
        prior_outputs.update(group_outputs)

        # Update memory
        self._update_memory(session_id, run_id, group_outputs)

        # KILL CHECKPOINTS — evaluate after critical sections
        for section_key, section_output in group_outputs.items():
            if isinstance(section_output, dict):
                should_continue, checkpoint_result = should_continue_pipeline(
                    str(section_key), section_output, prior_outputs
                )
                if not should_continue and checkpoint_result:
                    logger.warning(
                        "[MotherAgent] Kill checkpoint triggered at section %s: %s",
                        section_key, checkpoint_result.message,
                    )
                    emit_trace(
                        trace_key, "Mother", "kill_checkpoint",
                        f"Section {section_key}: {checkpoint_result.message}",
                        {"evidence": checkpoint_result.evidence},
                    )
                    self._send_telegram(
                        session_id,
                        f"⚠️ CHECKPOINT: {checkpoint_result.message}\n\n"
                        f"Evidence: {json.dumps(checkpoint_result.evidence, default=str)[:500]}\n\n"
                        "Reply 'continue', 'pivot', or 'kill'.",
                    )
                    # Wait for CEO response
                    checkpoint_response = await self._wait_for_checkpoint_response(
                        session_id, str(section_key), timeout=7200
                    )
                    if checkpoint_response == "kill":
                        self._fail_pipeline(run_id, f"Killed at checkpoint section {section_key}")
                        return
                    # "continue" or "pivot" — proceed

        # COHERENCE AUDIT — check for cross-section contradictions
        audit_result = self.coherence_auditor.audit(prior_outputs)
        if audit_result.contradictions:
            logger.info(
                "[MotherAgent] Coherence audit found %d contradictions",
                len(audit_result.contradictions),
            )
            # Attempt negotiation-based resolution
            resolutions = await self.conflict_resolver.resolve(
                audit_result.contradictions,
                self.negotiation_manager,
                self.bedrock,
                self.model_id,
            )
            for resolution in resolutions:
                if resolution.get("outcome") == "deadlock":
                    self._send_telegram(
                        session_id,
                        f"Unresolved conflict: {resolution.get('escalation_message', '')}",
                    )

        print(f"[MotherAgent] === Group {group_number} done. Advancing to Group {group_number + 1} ===")

        # Advance to next group — only reached after everything above completes
        await self._run_group(
            group_number + 1, session_id, run_id,
            phase1_data, applicable_sections, prior_outputs
        )

    async def _execute_group(
        self,
        tasks: list,
        task_ids: dict,
        group_id: str,
        session_id: str,
        run_id: str,
        group_config: dict,
    ) -> dict:
        """TASK 5: Fault-tolerant group execution — one agent crash doesn't kill the pipeline."""
        self._update_group_status(group_id, "running")
        outputs = {}

        if group_config.get("parallel", False):
            results = await asyncio.gather(
                *[
                    self._execute_task_safe(t, task_ids.get(t["task_name"]), session_id, run_id)
                    for t in tasks
                ],
                return_exceptions=True,
            )
            for task, result in zip(tasks, results):
                if isinstance(result, Exception):
                    print(f"[MotherAgent] Task {task['task_name']} failed: {result}")
                    fallback = self._generate_fallback_output(task["bp_section"], "task_failure", str(result))
                    if fallback:
                        outputs[task["bp_section"]] = fallback
                elif result:
                    outputs[task["bp_section"]] = result
        else:
            for task in tasks:
                tid = task_ids.get(task["task_name"])
                result = await self._execute_task_safe(task, tid, session_id, run_id)
                if result:
                    outputs[task["bp_section"]] = result
                else:
                    # Store fallback so downstream agents have something
                    fallback = self._generate_fallback_output(task["bp_section"], "task_timeout", "No response")
                    if fallback:
                        outputs[task["bp_section"]] = fallback
                        print(f"[MotherAgent] Using fallback output for section {task['bp_section']}")

        self._update_group_status(group_id, "completed")
        return outputs

    async def _execute_task_safe(
        self,
        task: dict,
        task_id: str,
        session_id: str,
        run_id: str,
    ) -> Optional[dict]:
        """Wrap _execute_task in try/except to prevent single task from crashing pipeline."""
        try:
            return await self._execute_task(task, task_id, session_id, run_id)
        except Exception as e:
            print(f"[MotherAgent] Task execution error for {task.get('task_name', 'unknown')}: {e}")
            logger.exception("Task execution failed")
            self.db.client.table("task_readiness") \
                .update({
                    "status": "failed",
                    "validation_errors": str(e),
                    "completed_at": datetime.utcnow().isoformat(),
                }) \
                .eq("id", task_id).execute()
            return None

    async def _execute_task(
        self,
        task: dict,
        task_id: str,
        session_id: str,
        run_id: str,
    ) -> Optional[dict]:
        agent_name = task.get("owner")
        agent_config = self.agent_roster["agents"].get(agent_name, {})
        agent_jid = os.getenv(agent_config.get("jid_env", ""), "")
        trace_key = self._get_trace_key(session_id)
        trace_agent = agent_config.get("trace_name", agent_name.replace("_", " ").title().replace(" ", ""))
        section = task.get("bp_section", "")
        is_council_gated = str(section) in COUNCIL_GATED_SECTIONS

        if not agent_jid:
            print(f"[MotherAgent] No JID for agent {agent_name} — task skipped")
            return None

        # Update task status to running
        self.db.client.table("task_readiness") \
            .update({"status": "running", "started_at": datetime.utcnow().isoformat()}) \
            .eq("id", task_id).execute()

        emit_trace(trace_key, trace_agent, "processing", f"Working on section {section}", {"task_id": task_id})

        # Send request to child agent
        await send_acl(
            self,
            to_jid=agent_jid,
            performative="request",
            content={"task": task, "task_id": task_id},
            task_id=task_id,
            session_id=session_id,
            pipeline_run_id=run_id,
        )

        # Council-gated sections get more time (child + council review)
        timeout = task.get("timeout_seconds", 90)
        if is_council_gated:
            timeout = timeout + 120

        # Wait for output in Redis (set by child directly or by Council after review)
        output = await self._wait_for_task_output(task_id, timeout=timeout)

        if output:
            confidence = output.get("confidence_score", "unknown") if isinstance(output, dict) else "unknown"
            emit_trace(trace_key, trace_agent, "complete", f"Section {section} done — confidence: {confidence}", {"confidence": confidence})

            # Council-gated sections: already reviewed by Council, run evidence
            # grading and finalize inline (they skip handle_inform DA path)
            if is_council_gated and isinstance(output, dict):
                output = await self._post_process_council_output(
                    task_id, session_id, run_id, section, output, agent_name
                )
        else:
            emit_trace(trace_key, trace_agent, "timeout", f"Section {section} timed out")

        return output

    async def _post_process_council_output(
        self,
        task_id: str,
        session_id: str,
        run_id: str,
        section: str,
        output: dict,
        agent_name: str,
    ) -> dict:
        """Post-process council-reviewed output: evidence grading, write to DB, finalize."""
        trace_key = self._get_trace_key(session_id)

        # Grade evidence on assumptions
        assumptions = output.get("assumptions_used", output.get("assumption_log", []))
        if assumptions:
            available_evidence = output.get("ceo_provided_data", {})
            graded = await self.intelligence.grade_evidence(assumptions, available_evidence)
            if graded and graded != assumptions:
                output["assumptions_used"] = graded
                emit_trace(trace_key, "Mother", "evidence_graded", f"Section {section}: assumptions re-graded")

        # Enforce confidence ceiling
        section_config = self.dependency_map["sections"].get(str(section), {})
        prior_outputs = self._load_prior_outputs(run_id)
        ceiling = self._compute_confidence_ceiling(section_config, prior_outputs)
        if ceiling:
            confidence_rank = {"high": 3, "medium": 2, "low": 1}
            current = output.get("confidence_score", "medium")
            if confidence_rank.get(current, 2) > confidence_rank.get(ceiling, 2):
                logger.info("[MotherAgent] Council output confidence capped: %s → %s", current, ceiling)
                output["confidence_score"] = ceiling
                output["_confidence_capped"] = True

        # Constitution enforcement
        violations = self._enforce_constitution(section, output)
        if violations:
            output["_constitution_warnings"] = violations

        # Write to Supabase
        self._write_section_content(session_id, run_id, section, output, agent_name)
        self._finalize_task(task_id, session_id, run_id, section, output)

        return output

    # ── Message handlers ──────────────────────────────────────────────────────

    async def handle_inform(self, task_id, session_id, run_id, from_agent, content):
        """Child agent completed a task — validate, challenge via DA, then accept."""
        print(f"[MotherAgent] inform from {from_agent} for task {task_id}")
        trace_key = self._get_trace_key(session_id)

        # Handle Devil's Advocate responses (not section outputs)
        if content.get("agent") == "devils_advocate":
            await self._handle_da_response(task_id, session_id, run_id, content.get("output", {}))
            return

        output = content.get("output", {})
        section = content.get("section_number")
        emit_trace(trace_key, "Mother", "inform_received", f"Section {section} output received from {from_agent}", {"section": section})

        # Validate output against Pydantic schema
        valid, errors = self._validate_output(section, output)
        if not valid:
            print(f"[MotherAgent] Validation failed for section {section}: {errors}")
            await self._retry_task(task_id, session_id, run_id, errors)
            return

        # Constitution enforcement
        violations = self._enforce_constitution(section, output)
        if violations:
            logger.warning("[MotherAgent] Constitution violations in section %s: %s", section, violations)
            emit_trace(trace_key, "Mother", "constitution_violation", f"Section {section}: {violations[0]}")
            if isinstance(output, dict):
                output["_constitution_warnings"] = violations

        # Enforce confidence ceiling — hard cap regardless of what LLM returned
        if isinstance(output, dict):
            section_config = self.dependency_map["sections"].get(str(section), {})
            prior_outputs = self._load_prior_outputs(run_id)
            ceiling = self._compute_confidence_ceiling(section_config, prior_outputs)
            if ceiling:
                confidence_rank = {"high": 3, "medium": 2, "low": 1}
                current = output.get("confidence_score", "medium")
                if confidence_rank.get(current, 2) > confidence_rank.get(ceiling, 2):
                    logger.info("[MotherAgent] Confidence capped: %s → %s (ceiling from upstream)", current, ceiling)
                    output["confidence_score"] = ceiling
                    output["_confidence_capped"] = True

        # Send to Devil's Advocate for challenge (non-blocking — store pending and continue)
        da_jid = os.getenv("DEVILS_ADVOCATE_JID", "")
        if da_jid and str(section) != "executive_summary":
            emit_trace(trace_key, "Mother", "da_review_start", f"Sending section {section} to Devil's Advocate")
            self.redis.client.set(
                f"da_pending:{task_id}",
                json.dumps({
                    "section": section,
                    "output": output,
                    "from_agent": from_agent,
                    "session_id": session_id,
                    "run_id": run_id,
                }, default=str),
                ex=3600,
            )
            prior_outputs = self._load_prior_outputs(run_id)
            da_input = {
                "section_number": str(section),
                "section_output": output,
                "reasoning_trace": output.get("reasoning_trace", {}),
                "cross_section_context": prior_outputs,
            }
            await send_acl(
                self,
                to_jid=da_jid,
                performative="request",
                content={"task": {"input_package": da_input, "acceptance_criteria": "Challenge all claims"}},
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=run_id,
            )
            return

        # Write to bp_section_content (executive summary skips DA)
        self._write_section_content(session_id, run_id, section, output, from_agent)
        self._finalize_task(task_id, session_id, run_id, section, output)

    async def handle_propose(self, task_id, session_id, run_id, from_agent, content):
        """Agent detected a contradiction and proposes a resolution."""
        target_agent = content.get("target_agent")
        proposal = content.get("proposal")

        print(f"[MotherAgent] propose from {from_agent} targeting {target_agent}: {proposal}")

        # Route the proposal to the target agent
        target_config = self._find_agent_config_by_jid(target_agent)
        if target_config:
            await send_acl(
                self,
                to_jid=target_agent,
                performative="propose",
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=run_id,
            )

    async def handle_status_update(self, task_id, session_id, run_id, from_agent, content):
        """Handle status update from child/quality agents (e.g., Council revision loop)."""
        status = content.get("status", "")
        section = content.get("section", "")
        message = content.get("message", "")

        logger.info(
            "[MotherAgent] Status update from %s — task %s: %s",
            from_agent, task_id, status
        )

        # Reset task TTL if still active (prevents orphaned tasks during revision loops)
        if status == "council_revising":
            attempt = content.get("revision_attempt", 0)
            # Extend task readiness TTL by 1 hour per revision attempt
            try:
                self.db.client.table("task_readiness") \
                    .update({
                        "status": "council_revising",
                        "updated_at": datetime.utcnow().isoformat(),
                    }) \
                    .eq("id", task_id).execute()
                logger.info(
                    "[MotherAgent] Extended task %s TTL — Council revision attempt %d",
                    task_id, attempt
                )
            except Exception as e:
                logger.warning("[MotherAgent] Failed to update task status: %s", e)

    async def handle_refuse(self, task_id, session_id, run_id, from_agent, content):
        """Agent refused a proposal — escalate to Alex if needed."""
        print(f"[MotherAgent] refuse from {from_agent}")
        original_proposer = content.get("original_proposer")
        reason = content.get("reason", "No reason provided")

        conflict_summary = (
            f"Contradiction between {from_agent} and {original_proposer}: {reason}"
        )
        self._notify_alex_conflict(session_id, conflict_summary, content)

    async def handle_escalate(self, task_id, session_id, run_id, from_agent, content):
        """Child agent hit one of the 3 escalation triggers."""
        trigger = content.get("trigger")
        notes = content.get("notes", "")
        section = content.get("section", "") or content.get("section_number", "")
        trace_key = self._get_trace_key(session_id)

        print(f"[MotherAgent] escalate from {from_agent} — trigger: {trigger}")
        emit_trace(trace_key, "Mother", "escalation", f"Section {section} escalated: {trigger}", {"from": from_agent, "trigger": trigger})

        # SPECIAL HANDLING: Quality gate failure (Devil's Advocate or Council Agent)
        if trigger == "quality_gate_failure":
            logger.error(
                "[MotherAgent] QUALITY GATE FAILURE — Section %s from %s",
                section, from_agent
            )
            # Pause pipeline immediately
            self.redis.client.set(
                f"pipeline:{run_id}:status",
                "paused_quality_gate",
                ex=86400
            )
            # Notify CEO with urgency
            self._send_telegram(
                session_id,
                f"🚨 CRITICAL: Quality gate failed for Section {section}\n\n"
                f"Reason: {notes}\n\n"
                f"Pipeline PAUSED. Manual review required before continuing.\n\n"
                f"Reply 'continue' to override and proceed, or 'abort' to stop pipeline."
            )
            # Store escalation in DB
            self.db.client.table("task_readiness") \
                .update({
                    "status": "paused_quality_gate",
                    "escalation_trigger": trigger,
                    "escalation_notes": notes,
                }) \
                .eq("id", task_id).execute()

            # Wait for CEO decision
            override_decision = await self._wait_for_ceo_override(session_id, run_id, timeout=86400)
            if override_decision == "continue":
                logger.warning("[MotherAgent] CEO override — continuing despite quality gate failure")
                self._send_telegram(session_id, "⚠️ Continuing with CEO override. Quality issues noted.")
                # Store original output from quality gate and proceed
                output = content.get("output", {})
                if output:
                    self._write_section_content(session_id, run_id, section, output, from_agent)
                    self.redis.client.set(
                        f"task_output:{task_id}",
                        json.dumps(output),
                        ex=3600
                    )
                return
            elif override_decision == "abort":
                logger.error("[MotherAgent] CEO aborted pipeline due to quality gate failure")
                self._send_telegram(session_id, "🛑 Pipeline aborted by CEO.")
                self.redis.client.set(f"pipeline:{run_id}:status", "aborted", ex=86400)
                return
            else:
                # Timeout — default to paused
                logger.warning("[MotherAgent] CEO did not respond to quality gate failure — pipeline remains paused")
                self._send_telegram(session_id, "⏸️ No response received. Pipeline remains paused.")
                return

        # Update task status
        self.db.client.table("task_readiness") \
            .update({
                "status": "escalated",
                "escalation_trigger": trigger,
                "escalation_notes": notes,
            }) \
            .eq("id", task_id).execute()

        # TASK 2: Store minimal valid fallback output so downstream agents have data
        fallback_output = self._generate_fallback_output(section, trigger, notes)
        if fallback_output:
            self._write_section_content(session_id, run_id, section, fallback_output, from_agent)
            self.redis.client.set(
                f"task_output:{task_id}",
                json.dumps(fallback_output),
                ex=3600
            )
            print(f"[MotherAgent] Stored fallback output for section {section}")

        # Determine resolution path from gap_resolution_rules
        gap_key = content.get("gap_key")
        gap_rule = self.gap_rules.get("gaps", {}).get(gap_key, {})

        question = gap_rule.get("question_to_ceo", notes)
        agent_alt = gap_rule.get("agent_alternative")
        blocking = gap_rule.get("blocking", False)

        # Log gap resolution
        self.db.client.table("gap_resolutions").insert({
            "pipeline_run_id": run_id,
            "session_id": session_id,
            "task_id": task_id,
            "section_number": section,
            "gap_description": notes,
            "resolution_type": "blocked" if blocking else "ceo_provided",
            "question_asked_to_ceo": question,
        }).execute()

        # Set Redis key for clarification routing (TASK 6)
        try:
            sess = self.db.client.table("sessions") \
                .select("telegram_chat_id") \
                .eq("id", session_id).execute()
            if sess.data:
                chat_id = sess.data[0].get("telegram_chat_id")
                if chat_id:
                    self.redis.client.set(
                        f"awaiting_clarification:{chat_id}",
                        json.dumps({
                            "task_id": task_id,
                            "session_id": session_id,
                            "run_id": run_id,
                            "section": section,
                            "question": question,
                        }),
                        ex=14400
                    )
        except Exception as e:
            print(f"[MotherAgent] Failed to set clarification key: {e}")

        # Ask Alex
        msg = f"An agent needs clarification before continuing:\n\n{question}"
        if agent_alt:
            msg += f"\n\nAlternatively, I can run an agent to gather this. Reply 'agent' to delegate."
        self._send_telegram(session_id, msg)

        # Wait for Alex's clarification response and store it for task resume
        clarification = await self._wait_for_clarification(task_id, timeout=7200)
        if clarification:
            clar_type = clarification.get("type", "")
            if clar_type == "ceo_answer":
                self.redis.client.set(
                    f"clarification_data:{task_id}",
                    json.dumps({"answer": clarification["answer"], "gap_key": gap_key, "section": section}),
                    ex=3600,
                )
                self.db.client.table("gap_resolutions") \
                    .update({"resolution_type": "ceo_provided", "ceo_answer": clarification["answer"]}) \
                    .eq("task_id", task_id).execute()
                emit_trace(trace_key, "Mother", "clarification_received", f"Alex answered for section {section}")
            elif clar_type == "delegate_to_agent":
                emit_trace(trace_key, "Mother", "clarification_delegated", f"Alex delegated section {section} to agent")

    async def _handle_da_response(self, task_id: str, session_id: str, run_id: str, da_output: dict):
        """Process Devil's Advocate verdict and decide whether to accept/revise/reject the section."""
        trace_key = self._get_trace_key(session_id)
        verdict = da_output.get("verdict", "pass")
        section = da_output.get("section_number", "unknown")
        challenges = da_output.get("challenges", [])

        emit_trace(
            trace_key, "Mother", "da_verdict",
            f"DA verdict for section {section}: {verdict} ({len(challenges)} challenges)",
            {"verdict": verdict, "challenges_count": len(challenges)},
        )

        # Retrieve the pending section output
        pending_raw = self.redis.client.get(f"da_pending:{task_id}")
        if not pending_raw:
            logger.warning("[MotherAgent] DA response for task %s but no pending data found", task_id)
            return

        if isinstance(pending_raw, bytes):
            pending_raw = pending_raw.decode("utf-8")
        pending = json.loads(pending_raw)
        output = pending["output"]
        from_agent = pending["from_agent"]
        self.redis.client.delete(f"da_pending:{task_id}")

        if verdict == "pass":
            # Calibrate confidence based on DA assessment
            calibrated = await self.intelligence.calibrate_confidence(output, da_output)
            if isinstance(output, dict) and calibrated != output.get("confidence_score"):
                logger.info("[MotherAgent] Confidence recalibrated: %s → %s", output.get("confidence_score"), calibrated)
                output["confidence_score"] = calibrated
                output["_da_recalibrated"] = True

            # So-What Filter — does this section actually help Alex make a decision?
            agent_role = self._get_agent_role_for_section(section)
            so_what_critique = await self.intelligence.apply_so_what_filter(output, agent_role)
            if so_what_critique:
                logger.warning("[MotherAgent] Section %s failed So-What filter: %s", section, so_what_critique)
                emit_trace(trace_key, "Mother", "so_what_fail", f"Section {section}: {so_what_critique[:100]}")
                output["_so_what_warning"] = so_what_critique

            # Hypothesis testing — validate funnel math and unit economics
            failed_hypotheses = await self.intelligence.validate_hypotheses(output, agent_role)
            if failed_hypotheses:
                logger.warning("[MotherAgent] Section %s has %d failed hypotheses", section, len(failed_hypotheses))
                emit_trace(trace_key, "Mother", "hypothesis_fail", f"Section {section}: {len(failed_hypotheses)} failed checks")
                output["_hypothesis_warnings"] = [
                    f"[{h.get('hypothesis', '')}] {h.get('explanation', '')}"
                    for h in failed_hypotheses
                ]

            # Evidence grading — verify assumption confidence labels are honest
            assumptions = output.get("assumptions_used", output.get("assumption_log", []))
            if assumptions:
                available_evidence = output.get("ceo_provided_data", {})
                graded = await self.intelligence.grade_evidence(assumptions, available_evidence)
                if graded and graded != assumptions:
                    output["assumptions_used"] = graded
                    emit_trace(trace_key, "Mother", "evidence_graded", f"Section {section}: assumptions re-graded")

            # Record DA accuracy for learning
            for challenge in challenges:
                self.learning.record_da_accuracy(
                    session_id=session_id,
                    section_number=str(section),
                    challenge_type=challenge.get("challenge_type", "unknown"),
                    was_valid=challenge.get("severity") != "low",
                )

            output["_da_verdict"] = "pass"
            self._write_section_content(session_id, run_id, section, output, from_agent)
            self._finalize_task(task_id, session_id, run_id, section, output)

        elif verdict == "revise":
            high_challenges = [c for c in challenges if c.get("severity") == "high"]
            revision_feedback = "\n".join(
                f"- [{c.get('challenge_type')}] {c.get('explanation')} → Fix: {c.get('suggested_fix')}"
                for c in (high_challenges or challenges[:3])
            )
            logger.info("[MotherAgent] Section %s needs revision — %d challenges", section, len(challenges))
            emit_trace(trace_key, "Mother", "da_revise", f"Section {section} sent back for revision")

            for challenge in challenges:
                self.learning.record_da_accuracy(
                    session_id=session_id,
                    section_number=str(section),
                    challenge_type=challenge.get("challenge_type", "unknown"),
                    was_valid=True,
                )

            # Check retry count — max 1 revision loop to avoid infinite cycles
            revision_key = f"da_revision_count:{task_id}"
            revision_count = int(self.redis.client.get(revision_key) or 0)

            if revision_count < 1:
                # Re-dispatch to child agent with challenges as hard constraints
                self.redis.client.set(revision_key, str(revision_count + 1), ex=3600)
                await self._redispatch_with_feedback(
                    task_id, session_id, run_id, section, output, from_agent, challenges, revision_feedback
                )
            else:
                # Already revised once — accept with warnings
                logger.info("[MotherAgent] Section %s already revised once — accepting with warnings", section)
                output["_da_verdict"] = "revise"
                output["_da_challenges"] = [c.get("explanation", "") for c in challenges[:5]]
                calibrated = await self.intelligence.calibrate_confidence(output, da_output)
                output["confidence_score"] = calibrated
                self._write_section_content(session_id, run_id, section, output, from_agent)
                self._finalize_task(task_id, session_id, run_id, section, output)

                challenge_text = "\n".join(f"• {c.get('explanation', '')[:100]}" for c in challenges[:3])
                self._send_telegram(
                    session_id,
                    f"Section {section} has issues flagged by review:\n{challenge_text}\n\n"
                    f"Confidence downgraded to: {calibrated}. Will note in final plan."
                )

        elif verdict == "reject":
            logger.warning("[MotherAgent] Section %s REJECTED by DA — %d high-severity issues", section, len(challenges))
            emit_trace(trace_key, "Mother", "da_reject", f"Section {section} rejected — escalating")

            for challenge in challenges:
                self.learning.record_da_accuracy(
                    session_id=session_id,
                    section_number=str(section),
                    challenge_type=challenge.get("challenge_type", "unknown"),
                    was_valid=True,
                )

            self.learning.record_rejection(
                session_id=session_id,
                section_number=str(section),
                reason=f"DA rejected: {len(challenges)} high-severity issues",
                ceo_feedback=da_output.get("summary", ""),
            )

            # Downgrade to low confidence and accept with heavy warnings
            output["_da_verdict"] = "reject"
            output["_da_challenges"] = [c.get("explanation", "") for c in challenges]
            output["confidence_score"] = "low"
            self._write_section_content(session_id, run_id, section, output, from_agent)
            self._finalize_task(task_id, session_id, run_id, section, output)

            challenge_text = "\n".join(f"• {c.get('explanation', '')[:100]}" for c in challenges[:5])
            self._send_telegram(
                session_id,
                f"Section {section} has serious issues:\n{challenge_text}\n\n"
                f"Marked as LOW confidence. Please review this section carefully."
            )

    async def _redispatch_with_feedback(
        self,
        task_id: str,
        session_id: str,
        run_id: str,
        section: str,
        original_output: dict,
        from_agent: str,
        challenges: list,
        revision_feedback: str,
    ):
        """Re-dispatch a section to its child agent with DA challenges as constraints."""
        trace_key = self._get_trace_key(session_id)
        emit_trace(trace_key, "Mother", "redispatch", f"Re-dispatching section {section} with {len(challenges)} challenges")

        agent_name = self._get_agent_name_for_section(section)
        if not agent_name:
            logger.warning("[MotherAgent] Cannot find agent for section %s — accepting with warnings", section)
            original_output["_da_verdict"] = "revise"
            original_output["_da_challenges"] = [c.get("explanation", "") for c in challenges[:5]]
            original_output["confidence_score"] = "low"
            self._write_section_content(session_id, run_id, section, original_output, from_agent)
            self._finalize_task(task_id, session_id, run_id, section, original_output)
            return

        agent_config = self.agent_roster["agents"][agent_name]
        agent_jid = os.getenv(agent_config.get("jid_env", ""), "")
        if not agent_jid:
            logger.warning("[MotherAgent] No JID for %s — accepting with warnings", agent_name)
            original_output["_da_verdict"] = "revise"
            original_output["confidence_score"] = "low"
            self._write_section_content(session_id, run_id, section, original_output, from_agent)
            self._finalize_task(task_id, session_id, run_id, section, original_output)
            return

        prior_outputs = self._load_prior_outputs(run_id)
        phase1_data = self._read_phase1_session(session_id) or {}
        section_config = self.dependency_map["sections"].get(str(section), {})
        input_package = self._assemble_input_package(section_config, prior_outputs, phase1_data)

        input_package["revision_required"] = True
        input_package["revision_feedback"] = revision_feedback
        input_package["challenges_to_fix"] = [
            {
                "claim": c.get("claim", ""),
                "type": c.get("challenge_type", ""),
                "fix": c.get("suggested_fix", ""),
            }
            for c in challenges
            if c.get("severity") in ("high", "medium")
        ]

        revision_task = {
            "task_name": f"Revise section {section} (DA feedback)",
            "bp_section": str(section),
            "purpose": f"Fix issues identified by Devil's Advocate review",
            "input_package": input_package,
            "output_format": "structured_json",
            "owner": agent_name,
            "acceptance_criteria": "Fix all high-severity challenges. Do not weaken analysis.",
            "timeout_seconds": agent_config.get("timeout_seconds", 90),
        }

        new_task_id = f"{task_id}_rev"
        self.redis.client.set(
            f"da_pending:{new_task_id}",
            json.dumps({
                "section": section,
                "output": original_output,
                "from_agent": from_agent,
                "session_id": session_id,
                "run_id": run_id,
            }, default=str),
            ex=3600,
        )

        await send_acl(
            self,
            to_jid=agent_jid,
            performative="request",
            content={"task": revision_task, "task_id": new_task_id},
            task_id=new_task_id,
            session_id=session_id,
            pipeline_run_id=run_id,
        )
        logger.info("[MotherAgent] Section %s re-dispatched to %s for revision", section, agent_name)

    def _get_agent_name_for_section(self, section: str) -> Optional[str]:
        """Look up which agent owns a given section number."""
        for name, config in self.agent_roster["agents"].items():
            if str(section) in [str(s) for s in config.get("sections_owned", [])]:
                return name
        return None

    def _get_agent_role_for_section(self, section: str) -> str:
        """Get the agent's role description for So-What filter context."""
        agent_name = self._get_agent_name_for_section(section)
        if agent_name:
            config = self.agent_roster["agents"].get(agent_name, {})
            return config.get("description", f"Agent for section {section}")
        return f"Agent for section {section}"

    def _finalize_task(self, task_id: str, session_id: str, run_id: str, section: str, output: dict):
        """Mark task complete and store output after DA review."""
        self.db.client.table("task_readiness") \
            .update({
                "status": "complete",
                "output_data": output,
                "validation_errors": None,
                "completed_at": datetime.utcnow().isoformat(),
            }) \
            .eq("id", task_id).execute()

        self.redis.client.set(
            f"task_output:{task_id}",
            json.dumps(output, default=str),
            ex=3600
        )

        self._notify_section_complete(session_id, section, output)

    def _generate_fallback_output(self, section: str, trigger: str, notes: str) -> Optional[dict]:
        """Generate minimal valid fallback output for escalated sections."""
        fallback_templates = {
            "3": {
                "pest_analysis": [],
                "five_forces": [],
                "risks_opportunities": {"risks": [], "opportunities": []},
                "confidence": "low",
                "escalation_note": f"Escalated: {trigger} — {notes}",
            },
            "5": {
                "strengths": [],
                "weaknesses": [],
                "opportunities": [],
                "threats": [],
                "strategic_priorities": [],
                "confidence": "low",
                "escalation_note": f"Escalated: {trigger} — {notes}",
            },
            "8": {
                "target_market_analysis": {},
                "positioning_statement": "Pending clarification",
                "channel_strategy": [],
                "confidence": "low",
                "escalation_note": f"Escalated: {trigger} — {notes}",
            },
            "12": {
                "three_statement_model": {},
                "key_assumptions": [],
                "scenario_analysis": {},
                "confidence": "low",
                "escalation_note": f"Escalated: {trigger} — {notes}",
            },
        }
        return fallback_templates.get(str(section))

    # ── Backward pass ────────────────────────────────────────────────────────

    async def _run_backward_pass(
        self,
        session_id: str,
        run_id: str,
        all_outputs: dict,
        issues: list,
        sections_to_regen: set,
    ):
        """Re-dispatch upstream sections that have conflicts with downstream sections.

        This is the core intelligence mechanism: when Financial discovers that Marketing's
        revenue assumptions produce a 24-month break-even, it routes back to Marketing
        with the specific conflict so Marketing can revise pricing/volume.
        """
        trace_key = self._get_trace_key(session_id)
        emit_trace(
            trace_key, "Mother", "backward_pass_start",
            f"Backward pass: re-dispatching {len(sections_to_regen)} sections",
            {"sections": list(sections_to_regen)},
        )
        logger.info("[MotherAgent] Backward pass — regenerating sections: %s", sections_to_regen)

        # Determine which section in each conflict is "upstream" (should be revised)
        # Strategy: the section with higher dependency depth is downstream (it's correct about the math),
        # so we revise the upstream section that fed it bad assumptions.
        sections_depth = {}
        for sec_num, sec_config in self.dependency_map.get("sections", {}).items():
            deps = sec_config.get("depends_on", [])
            sections_depth[sec_num] = len(deps)

        upstream_targets = set()
        for issue in issues:
            involved = issue.get("sections_involved", [])
            if len(involved) >= 2:
                # The section with fewer dependencies is upstream — revise it
                sorted_by_depth = sorted(involved, key=lambda s: sections_depth.get(str(s), 0))
                upstream_targets.add(str(sorted_by_depth[0]))
            else:
                upstream_targets.update(str(s) for s in involved)

        if not upstream_targets:
            upstream_targets = sections_to_regen

        phase1_data = self._read_phase1_session(session_id) or {}

        for section in upstream_targets:
            if section not in all_outputs:
                continue

            relevant_issues = [
                i for i in issues
                if str(section) in [str(s) for s in i.get("sections_involved", [])]
            ]
            if not relevant_issues:
                continue

            # Build revision feedback from the specific conflicts
            feedback_lines = []
            for issue in relevant_issues:
                other_sections = [s for s in issue.get("sections_involved", []) if str(s) != str(section)]
                feedback_lines.append(
                    f"- [{issue.get('type', 'conflict')}] {issue.get('description', '')} "
                    f"(conflicts with Section {', '.join(str(s) for s in other_sections)})"
                )

            revision_feedback = (
                "BACKWARD PASS — downstream sections found these conflicts with YOUR output:\n"
                + "\n".join(feedback_lines)
                + "\n\nRevise your output to resolve these conflicts. "
                "Use the hard_constraints values (from downstream) as the ground truth where numbers conflict."
            )

            agent_name = self._get_agent_name_for_section(section)
            if not agent_name:
                continue

            agent_config = self.agent_roster["agents"][agent_name]
            agent_jid = os.getenv(agent_config.get("jid_env", ""), "")
            if not agent_jid:
                continue

            section_config = self.dependency_map["sections"].get(str(section), {})
            input_package = self._assemble_input_package(section_config, all_outputs, phase1_data)
            input_package["revision_required"] = True
            input_package["revision_feedback"] = revision_feedback
            input_package["backward_pass"] = True

            task_id = f"backward_{run_id}_{section}"
            revision_task = {
                "task_name": f"Backward pass: revise section {section}",
                "bp_section": str(section),
                "purpose": "Resolve cross-section conflicts identified by coherence audit",
                "input_package": input_package,
                "output_format": "structured_json",
                "owner": agent_name,
                "acceptance_criteria": "Resolve all flagged conflicts. Numbers must match downstream sections.",
                "timeout_seconds": agent_config.get("timeout_seconds", 90),
            }

            emit_trace(
                trace_key, "Mother", "backward_dispatch",
                f"Backward pass: re-dispatching section {section} to {agent_name}",
            )

            await send_acl(
                self,
                to_jid=agent_jid,
                performative="request",
                content={"task": revision_task, "task_id": task_id},
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=run_id,
            )

            # Wait for revised output
            revised_output = await self._wait_for_task_output(task_id, timeout=agent_config.get("timeout_seconds", 90))
            if revised_output and isinstance(revised_output, dict):
                all_outputs[section] = revised_output
                self._write_section_content(session_id, run_id, section, revised_output, agent_name)
                logger.info("[MotherAgent] Backward pass: section %s revised successfully", section)
            else:
                logger.warning("[MotherAgent] Backward pass: section %s revision timed out — keeping original", section)

        # Re-run coherence audit after backward pass (delivers on this pass)
        await self._run_coherence_audit(session_id, run_id, all_outputs)

    # ── Coherence audit ───────────────────────────────────────────────────────

    async def _run_coherence_audit(self, session_id: str, run_id: str, all_outputs: dict):
        """LLM-powered cross-section coherence check before delivering to Alex."""
        logger.info("[MotherAgent] Running global coherence audit")
        trace_key = self._get_trace_key(session_id)
        emit_trace(trace_key, "Mother", "coherence_audit_start", f"Auditing {len(all_outputs)} sections for consistency")

        # Deduplicate assumptions across sections
        dedup_result = self._deduplicate_assumptions(all_outputs)
        if dedup_result["duplicates"] or dedup_result["conflicts"]:
            logger.info(
                "[MotherAgent] Assumptions: %d duplicates removed, %d conflicts flagged",
                len(dedup_result["duplicates"]), len(dedup_result["conflicts"]),
            )
            if dedup_result["conflicts"]:
                self._send_telegram(
                    session_id,
                    f"Found {len(dedup_result['conflicts'])} conflicting assumptions across sections:\n"
                    + "\n".join(f"• {c}" for c in dedup_result["conflicts"][:5])
                )

        section_summaries = {}
        for sec_num, output in all_outputs.items():
            if not isinstance(output, dict):
                continue
            summary_fields = {}
            for key in ("icp_hypothesis", "revenue_assumptions", "cac_assumptions",
                        "competitive_strategy", "target_market_analysis", "headcount_plan",
                        "break_even_analysis", "objectives", "three_statement_model",
                        "marketing_mix", "cost_structure", "confidence_score"):
                if key in output:
                    summary_fields[key] = output[key]
            if summary_fields:
                sec_name = self.dependency_map.get("sections", {}).get(str(sec_num), {}).get("name", sec_num)
                section_summaries[f"Section {sec_num} ({sec_name})"] = summary_fields

        if len(section_summaries) < 2:
            logger.info("[MotherAgent] Too few sections for coherence check — delivering")
            await self._deliver_plan(session_id, run_id, all_outputs)
            return

        truncated = json.dumps(section_summaries, indent=1, default=str)[:12000]

        prompt = f"""You are auditing a multi-section business plan for internal consistency.
Check for these specific contradictions:

1. REVENUE MISMATCH: Does the revenue model in Section 12 (financial plan) match the pricing/volume assumptions in Section 8 (marketing)?
2. ICP DRIFT: Is the ideal customer profile consistent between Section 1 (opportunity) and Section 8 (marketing)?
3. HEADCOUNT vs COST: Does the headcount plan (Section 11) match the personnel costs in the financial model (Section 12)?
4. TIMELINE CONFLICTS: Are launch dates and milestones consistent across Section 13 (start-up programme) and Section 12 (break-even timeline)?
5. SWOT ALIGNMENT: Do the strategies in Section 8 actually address the weaknesses/threats identified in Section 5 (SWOT)?
6. LOW CONFIDENCE SECTIONS: Flag any section with confidence_score "low" that feeds into a downstream section with confidence "high".

SECTION DATA:
{truncated}

Return ONLY valid JSON:
{{
  "passed": true/false,
  "issues": [
    {{"type": "revenue_mismatch|icp_drift|headcount_vs_cost|timeline_conflict|swot_alignment|confidence_gap", "description": "...", "sections_involved": ["1", "8"], "severity": "high|medium|low"}}
  ],
  "confidence_summary": {{"high": N, "medium": N, "low": N}},
  "overall_plan_confidence": "high|medium|low"
}}
If no issues found, return {{"passed": true, "issues": [], "confidence_summary": {{}}, "overall_plan_confidence": "high"}}"""

        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": "You are a rigorous business plan auditor. Find contradictions between sections. Be specific — cite the conflicting values. Respond with ONLY valid JSON."}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 2048},
            )
            raw = response["output"]["message"]["content"][0]["text"].strip()
            if raw.startswith("```"):
                first_nl = raw.index("\n") if "\n" in raw else 3
                raw = raw[first_nl + 1:]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            audit_result = json.loads(raw)
        except Exception as e:
            logger.error("[MotherAgent] Coherence audit LLM failed: %s — delivering anyway", e)
            await self._deliver_plan(session_id, run_id, all_outputs)
            return

        self.db.client.table("pipeline_runs") \
            .update({"coherence_audit": audit_result}) \
            .eq("id", run_id).execute()

        issues = audit_result.get("issues", [])
        high_severity = [i for i in issues if i.get("severity") == "high"]

        # Attempt one re-generation cycle for conflicting sections (max 1 retry)
        audit_version = self._get_audit_version(run_id)
        if high_severity and audit_version < 2:
            self._increment_audit_version(run_id)
            logger.warning("[MotherAgent] Coherence audit found %d high-severity issues — attempting regen", len(high_severity))
            emit_trace(trace_key, "Mother", "coherence_regen", f"Regenerating {len(high_severity)} conflicting sections")

            sections_to_regen = set()
            for issue in high_severity:
                for sec in issue.get("sections_involved", []):
                    sections_to_regen.add(sec)

            issue_text = "\n".join(
                f"• [{i['type']}] {i['description']} (Sections {', '.join(i.get('sections_involved', []))})"
                for i in high_severity
            )
            self._send_telegram(
                session_id,
                f"Coherence audit flagged {len(high_severity)} issue(s):\n\n{issue_text}\n\n"
                f"Attempting to regenerate conflicting sections..."
            )

            # Backward pass: re-dispatch conflicting upstream sections with specific fix instructions
            await self._run_backward_pass(session_id, run_id, all_outputs, high_severity, sections_to_regen)
            return

        if high_severity:
            logger.warning("[MotherAgent] Coherence audit: %d high-severity issues remain after regen", len(high_severity))
            issue_text = "\n".join(
                f"• [{i['type']}] {i['description']} (Sections {', '.join(i.get('sections_involved', []))})"
                for i in high_severity
            )
            confidence = audit_result.get("overall_plan_confidence", "unknown")
            self._send_telegram(
                session_id,
                f"Coherence audit flagged {len(high_severity)} issue(s):\n\n{issue_text}\n\n"
                f"Overall plan confidence: {confidence}\n\n"
                f"These inconsistencies remain after one revision pass. Please review."
            )

        confidence_summary = audit_result.get("confidence_summary", {})
        overall = audit_result.get("overall_plan_confidence", "medium")
        logger.info(
            "[MotherAgent] Coherence audit complete — confidence: %s, issues: %d",
            overall, len(issues),
        )
        emit_trace(trace_key, "Mother", "coherence_audit_done", f"Audit complete — {len(issues)} issues, confidence: {overall}", {"issues_count": len(issues), "overall_confidence": overall})

        await self._deliver_plan(session_id, run_id, all_outputs)

    def _deduplicate_assumptions(self, all_outputs: dict) -> dict:
        """Find duplicate and conflicting assumptions across all section outputs."""
        all_assumptions = []
        for sec_num, output in all_outputs.items():
            if not isinstance(output, dict):
                continue
            assumptions = output.get("assumptions_used", []) or output.get("assumption_log", [])
            for a in assumptions:
                if isinstance(a, dict):
                    stmt = a.get("statement", a.get("name", "")).lower().strip()
                    if stmt:
                        all_assumptions.append({
                            "statement": stmt,
                            "confidence": a.get("confidence", a.get("label", "")),
                            "source": a.get("source", a.get("label", "")),
                            "section": str(sec_num),
                        })

        seen = {}
        duplicates = []
        conflicts = []

        for a in all_assumptions:
            key = a["statement"][:80]
            if key in seen:
                prev = seen[key]
                if prev["confidence"] != a["confidence"] or prev["source"] != a["source"]:
                    conflicts.append(
                        f"'{a['statement'][:60]}' — Section {prev['section']} says {prev['confidence']}/{prev['source']}, "
                        f"Section {a['section']} says {a['confidence']}/{a['source']}"
                    )
                else:
                    duplicates.append(key)
            else:
                seen[key] = a

        return {"duplicates": duplicates, "conflicts": conflicts}

    def _get_audit_version(self, run_id: str) -> int:
        """Get current coherence audit iteration count."""
        key = f"audit_version:{run_id}"
        val = self.redis.client.get(key)
        if val:
            return int(val) if not isinstance(val, bytes) else int(val.decode("utf-8"))
        return 1

    def _increment_audit_version(self, run_id: str):
        """Increment audit version to track re-generation attempts."""
        key = f"audit_version:{run_id}"
        current = self._get_audit_version(run_id)
        self.redis.client.set(key, str(current + 1), ex=7200)

    # ── Gap and dependency helpers ────────────────────────────────────────────

    def _determine_applicable_sections(self, phase1_data: dict) -> list:
        """Use LLM to classify which conditional sections apply to this business."""
        always_required = []
        conditional_sections = {}

        for section_num, section_config in self.dependency_map.get("sections", {}).items():
            if section_config.get("always_required", False):
                always_required.append(section_num)
            else:
                conditional_sections[section_num] = section_config.get("condition", "")

        if not conditional_sections:
            return always_required

        idea = phase1_data.get("idea_summary", "")
        business_type = phase1_data.get("business_type", "")
        ceo_assumptions = phase1_data.get("ceo_assumptions", [])

        prompt = f"""Given this business idea, determine which optional business plan sections are applicable.

IDEA: {idea}
BUSINESS TYPE: {business_type}
CEO Q&A: {json.dumps(ceo_assumptions[:5], indent=2)}

CONDITIONAL SECTIONS:
"""
        for sec_num, condition in conditional_sections.items():
            sec_name = self.dependency_map["sections"][sec_num].get("name", "")
            prompt += f"- Section {sec_num} ({sec_name}): Include when: {condition}\n"

        prompt += """
Return ONLY a valid JSON object with one key "include" whose value is a list of section numbers (as strings) that should be included. Example: {"include": ["2", "4", "10"]}
Only include sections where the condition clearly applies based on the business idea."""

        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": "You classify business ideas to determine which business plan sections are relevant. Respond with ONLY valid JSON."}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 256},
            )
            raw = response["output"]["message"]["content"][0]["text"].strip()
            if raw.startswith("```"):
                first_nl = raw.index("\n") if "\n" in raw else 3
                raw = raw[first_nl + 1:]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            result = json.loads(raw)
            included = result.get("include", [])
            applicable = always_required + [s for s in included if s in conditional_sections]
            logger.info(
                "[MotherAgent] Section classification: always=%s conditional=%s",
                always_required, included,
            )
            return applicable
        except Exception as e:
            logger.error("[MotherAgent] Section classification LLM failed: %s — including all", e)
            return always_required + list(conditional_sections.keys())

    def _run_pre_simulation(self, tasks: list) -> dict:
        """Validate task sequencing — detect circular deps or missing inputs."""
        issues = []
        task_names = {t["task_name"] for t in tasks}

        for task in tasks:
            for dep in task.get("dependencies", []):
                if dep not in task_names:
                    issues.append(
                        f"Task '{task['task_name']}' depends on '{dep}' which is not in this group"
                    )

        return {
            "valid": len(issues) == 0,
            "issues": issues,
        }

    def _check_gaps(self, section_num: str, available_data: dict) -> list:
        """Check which required inputs for a section are missing."""
        section_config = self.dependency_map["sections"].get(str(section_num), {})
        required_inputs = section_config.get("required_inputs", [])
        gaps = []

        for req in required_inputs:
            field = req["field"]
            if field not in available_data:
                gap_rule = self.gap_rules.get("gaps", {}).get(field, {})
                gaps.append({
                    "field": field,
                    "description": req.get("description", ""),
                    "blocking": gap_rule.get("blocking", False),
                    "agent_alt": gap_rule.get("agent_alternative"),
                    "question": gap_rule.get("question_to_ceo", ""),
                })

        return gaps

    def _recheck_dependencies(self, tasks: list):
        """Re-run dependency analysis after an edit or add."""
        print("[MotherAgent] Re-checking dependencies after edit/add")
        result = self._run_pre_simulation(tasks)
        if not result["valid"]:
            print(f"[MotherAgent] Dependency issues after edit: {result['issues']}")

    # ── Supabase write helpers ────────────────────────────────────────────────

    def _create_pipeline_run(self, session_id: str, run_mode: str) -> str:
        result = self.db.client.table("pipeline_runs").insert({
            "session_id": session_id,
            "run_mode": run_mode,
            "status": "running",
            "constitution_version": "1.0",
        }).execute()
        run_id = result.data[0]["id"]
        print(f"[MotherAgent] Pipeline run created: {run_id}")
        return run_id

    def _create_execution_group(self, run_id, group_number, group_config, gate2_package) -> str:
        result = self.db.client.table("execution_groups").insert({
            "pipeline_run_id": run_id,
            "group_number": group_number,
            "group_name": group_config.get("name", f"Group {group_number}"),
            "status": "awaiting_approval",
            "gate2_package": gate2_package,
        }).execute()
        return result.data[0]["id"]

    def _update_group_status(self, group_id: str, status: str):
        update = {"status": status}
        if status == "running":
            update["started_at"] = datetime.utcnow().isoformat()
        if status in ("completed", "failed", "killed"):
            update["completed_at"] = datetime.utcnow().isoformat()
        self.db.client.table("execution_groups").update(update).eq("id", group_id).execute()

    def _write_tasks(self, tasks: list, session_id: str, run_id: str, group_number: int) -> dict:
        task_ids = {}
        for task in tasks:
            result = self.db.client.table("task_readiness").insert({
                "task_name": task["task_name"],
                "bp_section": task["bp_section"],
                "purpose": task.get("purpose", ""),
                "required_input": task.get("required_inputs", []),
                "input_source": task.get("input_source", "prior_task"),
                "output_required": task.get("output_required", ""),
                "output_format": task.get("output_format", "structured_json"),
                "owner": task.get("owner", ""),
                "acceptance_criteria": task.get("acceptance_criteria", ""),
                "uncertainty_level": task.get("uncertainty_level", "medium"),
                "ceo_approval_needed": task.get("ceo_approval_needed", False),
                "confidence_score": task.get("confidence_score", "medium"),
                "group_number": group_number,
                "status": "ready",
                "session_id": session_id,
                "pipeline_run_id": run_id,
            }).execute()
            task_ids[task["task_name"]] = result.data[0]["id"]
        return task_ids

    def _write_section_content(self, session_id, run_id, section, output, from_agent):
        agent_config = self._find_agent_config_by_name(from_agent)
        model = agent_config.get("model", "unknown") if agent_config else "unknown"

        self.db.client.table("bp_section_content").insert({
            "session_id": session_id,
            "pipeline_run_id": run_id,
            "section_number": str(section),
            "section_name": self.dependency_map["sections"].get(str(section), {}).get("name", ""),
            "content": output,
            "model_used": model,
            "validation_passed": True,
            "version": 1,
        }).execute()

        self.db.client.table("bp_section_metadata").upsert({
            "pipeline_run_id": run_id,
            "session_id": session_id,
            "section_number": str(section),
            "section_name": self.dependency_map["sections"].get(str(section), {}).get("name", ""),
            "status": "completed",
            "agent_assigned": from_agent,
            "model_used": model,
            "dependencies_met": True,
        }, on_conflict="pipeline_run_id,section_number").execute()

    # ── Telegram helpers ──────────────────────────────────────────────────────

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
            print(f"[MotherAgent] Telegram send failed: {e}")

    # ── Gate 2 helpers ────────────────────────────────────────────────────────

    def _build_opening_narrative(self, sections: list, phase1_data: dict) -> str:
        idea = phase1_data.get("idea_summary", "your approved idea")
        return (
            f"I'm going to build the business plan for: {idea}\n\n"
            f"I will run 4 groups in sequence:\n"
            f"Group 1 — Foundation: idea analysis, team capabilities, ICP\n"
            f"Group 2 — Evidence: market research, competitor analysis, environment\n"
            f"Group 3 — Strategy: SWOT synthesis, marketing plan, operating model\n"
            f"Group 4 — Financial and close: financial model, launch programme, executive summary\n\n"
            f"Ready to review the Group 1 tasks?"
        )

    def _build_gate2_package(self, group_number, group_config, tasks, sim_result, prior_outputs) -> dict:
        return {
            "group_number": group_number,
            "group_name": group_config.get("name", ""),
            "group_description": group_config.get("description", ""),
            "tasks": tasks,
            "sequence": [t["task_name"] for t in tasks],
            "simulation_result": sim_result,
            "prior_outputs_available": list(prior_outputs.keys()),
            "instructions": "Reply: agree / edit [what to change] / add [task description] / kill",
        }

    def _request_gate2_approval(self, session_id, run_id, group_id, gate2_package):
        lines = [
            f"{'━' * 36}",
            f"GROUP {gate2_package['group_number']} — {gate2_package['group_name']}",
            f"{'━' * 36}",
            "",
        ]

        for i, task in enumerate(gate2_package["tasks"], 1):
            task_id = task.get("task_id", f"T{i}")
            bp_section = task.get("bp_section", "?")
            exec_type = task.get("execution_type", "agent_executable")
            data_source = task.get("data_source")
            depends_on = task.get("depends_on", [])
            dep_reasoning = task.get("dependency_reasoning")
            ceiling = task.get("confidence_ceiling", {})

            # Task header
            lines.append(f"[{task_id}] {task['task_name']}")
            lines.append(f"  BP Section: §{bp_section}")
            lines.append(f"  Type: {exec_type}")

            # Data needed + source
            req_inputs = task.get("required_inputs", [])
            if req_inputs:
                data_fields = [inp.get("field", "") for inp in req_inputs[:3]]
                source_label = data_source if data_source else "prior sections"
                lines.append(
                    f"  Data needed: {', '.join(data_fields)}"
                )
                lines.append(f"  Best source: {source_label}")

            # Dependencies with reasoning
            if depends_on:
                dep_labels = [f"§{d}" for d in depends_on]
                lines.append(f"  Blocked by: {', '.join(dep_labels)}")
                if dep_reasoning:
                    lines.append(f"  Why: {dep_reasoning}")
            else:
                lines.append("  Blocked by: nothing (root task)")

            # Confidence ceiling
            if ceiling.get("capped"):
                lines.append(
                    f"  ⚠ Confidence capped at {ceiling['ceiling']}: "
                    f"{ceiling['reason']}"
                )

            lines.append("")

        # Pre-simulation check
        if gate2_package["simulation_result"]["valid"]:
            lines.append("Pre-run check: No dependency conflicts detected")
        else:
            lines.append(
                f"Pre-run issues: {gate2_package['simulation_result']['issues']}"
            )

        lines.append("")
        lines.append("Per task: approve / adjust [what] / kill / challenge [why]")
        lines.append("Or for whole group: agree / kill")

        self.redis.client.set(f"gate2_pending:{group_id}", "1", ex=14400)

        # Store active group for this chat so Telegram handler can route responses
        try:
            session = self.db.client.table("sessions") \
                .select("telegram_chat_id") \
                .eq("id", session_id).execute()
            if session.data:
                chat_id = session.data[0].get("telegram_chat_id")
                if chat_id:
                    self.redis.client.set(
                        f"gate2_active_group:{chat_id}", group_id, ex=14400
                    )
        except Exception as e:
            print(f"[MotherAgent] Failed to set gate2_active_group: {e}")

        self._send_telegram(session_id, "\n".join(lines))

    async def _wait_for_gate2_response(self, session_id: str, group_id: str) -> dict:
        """Poll Redis for Gate 2 response — set by Telegram callback handler."""
        timeout = 14400  # 4 hours
        elapsed = 0
        while elapsed < timeout:
            response = self.redis.client.get(f"gate2_response:{group_id}")
            if response:
                self.redis.client.delete(f"gate2_response:{group_id}")
                return json.loads(response)
            await asyncio.sleep(10)
            elapsed += 10
            if elapsed % 3600 == 0:
                self._send_telegram(session_id, "Waiting for your Group approval. Reply to continue.")
        return {"action": "kill"}

    async def _wait_for_task_output(self, task_id: str, timeout: int = 90) -> Optional[dict]:
        """Poll Redis for child agent output."""
        elapsed = 0
        while elapsed < timeout:
            result = self.redis.client.get(f"task_output:{task_id}")
            if result:
                self.redis.client.delete(f"task_output:{task_id}")
                return json.loads(result)
            await asyncio.sleep(2)
            elapsed += 2
        print(f"[MotherAgent] Task {task_id} timed out after {timeout}s")
        return None

    async def _wait_for_clarification(self, task_id: str, timeout: int = 7200) -> Optional[dict]:
        """Poll Redis for Alex's clarification response."""
        elapsed = 0
        while elapsed < timeout:
            result = self.redis.client.get(f"clarification_response:{task_id}")
            if result:
                self.redis.client.delete(f"clarification_response:{task_id}")
                if isinstance(result, bytes):
                    result = result.decode("utf-8")
                return json.loads(result)
            await asyncio.sleep(10)
            elapsed += 10
        print(f"[MotherAgent] Clarification for task {task_id} timed out after {timeout}s")
        return None

    async def _wait_for_ceo_override(self, session_id: str, run_id: str, timeout: int = 86400) -> Optional[str]:
        """Poll Redis for CEO's decision on quality gate failure: 'continue' or 'abort'."""
        key = f"quality_gate_override:{session_id}:{run_id}"
        elapsed = 0
        while elapsed < timeout:
            result = self.redis.client.get(key)
            if result:
                self.redis.client.delete(key)
                if isinstance(result, bytes):
                    result = result.decode("utf-8")
                decision = result.strip().lower()
                if decision in ("continue", "abort"):
                    return decision
            await asyncio.sleep(10)
            elapsed += 10
        logger.warning(
            "[MotherAgent] CEO override for quality gate timed out after %ds — defaulting to paused",
            timeout
        )
        return None

    async def _wait_for_checkpoint_response(
        self, session_id: str, section: str, timeout: int = 7200
    ) -> str:
        """Poll Redis for CEO's checkpoint response (continue/pivot/kill)."""
        key = f"checkpoint_response:{session_id}:{section}"
        elapsed = 0
        while elapsed < timeout:
            result = self.redis.client.get(key)
            if result:
                self.redis.client.delete(key)
                if isinstance(result, bytes):
                    result = result.decode("utf-8")
                return result.strip().lower()
            await asyncio.sleep(10)
            elapsed += 10
        logger.warning(
            "[MotherAgent] Checkpoint response timed out for section %s — continuing",
            section,
        )
        return "continue"

    # ── Retry and failure helpers ─────────────────────────────────────────────

    async def _retry_task(self, task_id: str, session_id: str, run_id: str, errors):
        """Increment retry count. Hard stop after 3 failures."""
        result = self.db.client.table("task_readiness") \
            .select("version").eq("id", task_id).execute()
        current_version = result.data[0].get("version", 1) if result.data else 1

        if current_version >= 3:
            self.db.client.table("task_readiness") \
                .update({"status": "needs_revision", "validation_errors": errors}) \
                .eq("id", task_id).execute()
            self._send_telegram(
                session_id,
                f"A task failed validation after 3 attempts. Pipeline paused.\n\nErrors: {errors}"
            )
        else:
            self.db.client.table("task_readiness") \
                .update({"version": current_version + 1, "status": "ready"}) \
                .eq("id", task_id).execute()

    async def _handle_task_failure(self, task: dict, session_id: str, run_id: str, error: str):
        self.db.client.table("task_readiness") \
            .update({"status": "needs_revision"}) \
            .eq("task_name", task["task_name"]) \
            .eq("session_id", session_id).execute()
        self._send_telegram(
            session_id,
            f"Task failed: {task['task_name']}\n\nError: {error}\n\nPipeline paused."
        )

    def _fail_pipeline(self, run_id: str, reason: str):
        self.db.client.table("pipeline_runs") \
            .update({"status": "failed", "failure_reason": reason}) \
            .eq("id", run_id).execute()
        print(f"[MotherAgent] Pipeline failed: {reason}")

    def _kill_group(self, run_id: str, group_id: str, session_id: str):
        self._update_group_status(group_id, "killed")
        self._fail_pipeline(run_id, "Alex killed the group")
        self.learning.record_rejection(
            session_id=session_id,
            section_number="group",
            reason="Alex killed the execution group",
            ceo_feedback="Group killed via Gate 2",
        )
        self._send_telegram(session_id, "Group killed. Pipeline stopped. Nothing has been executed.")

    # ── Delivery ──────────────────────────────────────────────────────────────

    async def _deliver_plan(self, session_id: str, run_id: str, all_outputs: dict):
        trace_key = self._get_trace_key(session_id)

        # Record acceptance in Learning Engine for all delivered sections
        for sec_num, output in all_outputs.items():
            if isinstance(output, dict):
                self.learning.record_acceptance(
                    session_id=session_id,
                    section_number=str(sec_num),
                    confidence_score=output.get("confidence_score", "medium"),
                    assumptions_count=len(output.get("assumptions_used", output.get("assumption_log", []))),
                    devils_advocate_verdict=output.get("_da_verdict", "not_reviewed"),
                )

        # Aggregate token usage across all sections
        total_input_tokens = 0
        total_output_tokens = 0
        for output in all_outputs.values():
            if isinstance(output, dict):
                total_input_tokens += output.get("input_tokens", 0)
                total_output_tokens += output.get("output_tokens", 0)

        # Compile narrative document
        coherence_audit = None
        try:
            run_data = self.db.client.table("pipeline_runs") \
                .select("coherence_audit").eq("id", run_id).execute()
            if run_data.data:
                coherence_audit = run_data.data[0].get("coherence_audit")
        except Exception:
            pass

        # Fetch council reports for this run
        council_reports = None
        try:
            cr_result = self.db.client.table("council_reports") \
                .select("*") \
                .eq("pipeline_run_id", run_id) \
                .order("section_number") \
                .execute()
            if cr_result.data:
                council_reports = cr_result.data
        except Exception:
            pass

        compiled_doc = None
        try:
            business_name = all_outputs.get("1", {}).get("opportunity_description", "The Business")[:80]
            compiled_doc = await self.compiler.compile(
                all_outputs, business_name, coherence_audit, council_reports
            )
        except Exception as e:
            logger.warning("[MotherAgent] Document compilation failed: %s — delivering JSON only", e)

        self.db.client.table("pipeline_runs") \
            .update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "sections_completed": list(all_outputs.keys()),
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }) \
            .eq("id", run_id).execute()

        # Store compiled document
        if compiled_doc:
            try:
                self.db.client.table("compiled_plans").insert({
                    "pipeline_run_id": run_id,
                    "session_id": session_id,
                    "format": "markdown",
                    "content": compiled_doc,
                }).execute()
            except Exception as e:
                logger.warning("[MotherAgent] Failed to store compiled plan: %s", e)

        emit_trace(trace_key, "Mother", "pipeline_complete", f"Business plan delivered — {len(all_outputs)} sections", {"sections": list(all_outputs.keys()), "total_tokens": total_input_tokens + total_output_tokens})

        total_tokens = total_input_tokens + total_output_tokens
        cost_note = ""
        if total_tokens > 0:
            cost_note = f"\nTokens used: {total_input_tokens:,} in / {total_output_tokens:,} out"

        doc_note = "\nFull narrative document compiled and ready for review." if compiled_doc else ""

        self._send_telegram(
            session_id,
            f"Business plan complete. {len(all_outputs)} sections delivered.{cost_note}{doc_note}\n"
            f"Review the full plan on your dashboard."
        )

    def _get_trace_key(self, session_id: str) -> str:
        """Get the WebSocket session key (telegram chat_id) for trace emission."""
        try:
            session = self.db.client.table("sessions") \
                .select("telegram_chat_id") \
                .eq("id", session_id).execute()
            if session.data:
                chat_id = session.data[0].get("telegram_chat_id")
                if chat_id:
                    return str(chat_id)
        except Exception:
            pass
        return session_id

    def _notify_section_complete(self, session_id: str, section: str, output: dict):
        """Send a concise progress notification for a completed section."""
        section_name = self.dependency_map.get("sections", {}).get(str(section), {}).get("name", f"Section {section}")
        confidence = output.get("confidence_score", "unknown") if isinstance(output, dict) else "unknown"
        uncertainties = output.get("uncertainties", []) if isinstance(output, dict) else []

        msg = f"Section {section} done — {section_name} (confidence: {confidence})"
        if uncertainties:
            msg += f"\nFlags: {uncertainties[0]}"

        self._send_telegram(session_id, msg)

    def _notify_alex_conflict(self, session_id: str, summary: str, content: dict):
        self._send_telegram(session_id, f"Agent conflict detected:\n\n{summary}\n\nReply with your decision.")

    def _notify_alex_coherence_issues(self, session_id: str, issues: list):
        self._send_telegram(session_id, "Coherence issues found:\n\n" + "\n".join(issues))

    # ── Task generation ───────────────────────────────────────────────────────

    def _generate_group_tasks(
        self,
        group_number: int,
        applicable_sections: list,
        prior_outputs: dict,
        phase1_data: dict,
    ) -> list:
        """Generate tasks for the given group from the dependency map."""
        group_config = self.agent_roster["execution_groups"][group_number]
        group_agents = group_config.get("agents", [])
        tasks = []

        for agent_name in group_agents:
            agent_config = self.agent_roster["agents"].get(agent_name, {})
            sections = agent_config.get("sections_owned", [])

            for section_num in sections:
                if str(section_num) not in applicable_sections:
                    continue
                section_config = self.dependency_map["sections"].get(str(section_num), {})

                # Build input package from available data
                input_package = self._assemble_input_package(
                    section_config, prior_outputs, phase1_data
                )

                # Derive execution_type from required_input sources
                required_inputs = section_config.get("required_inputs", [])
                execution_type = self._classify_execution_type(required_inputs)

                # Derive data_source from required_inputs with database annotation
                data_source = self._extract_data_source(required_inputs)

                # Derive dependency info for transparency
                depends_on = section_config.get("depends_on", [])
                dependency_reasoning = section_config.get("dependency_reasoning")

                # Confidence ceiling: capped if upstream deps are incomplete
                confidence_ceiling = self._compute_task_confidence_ceiling(
                    depends_on, prior_outputs
                )

                tasks.append({
                    "task_name": f"Build section {section_num}: {section_config.get('name', '')}",
                    "task_id": f"S{section_num}-G{group_number}",
                    "bp_section": str(section_num),
                    "purpose": f"Produce {section_config.get('name', '')} for the business plan",
                    "required_inputs": required_inputs,
                    "input_source": "prior_task",
                    "input_package": input_package,
                    "output_required": str(section_config.get("outputs", [])),
                    "output_format": "structured_json",
                    "owner": agent_name,
                    "acceptance_criteria": "All required output fields present. Confidence >= medium.",
                    "uncertainty_level": "medium",
                    "confidence_score": "medium",
                    "timeout_seconds": agent_config.get("timeout_seconds", 90),
                    "execution_type": execution_type,
                    "human_brief": None,
                    "data_source": data_source,
                    "depends_on": depends_on,
                    "dependency_reasoning": dependency_reasoning,
                    "confidence_ceiling": confidence_ceiling,
                })

        return tasks

    def _classify_execution_type(self, required_inputs: list) -> str:
        """Derive execution type from required_input source annotations."""
        sources = {inp.get("source", "") for inp in required_inputs}
        if "ceo_answer" in sources:
            return "human_interview"
        if "external_data" in sources or "web_search" in sources:
            return "data_retrieval"
        return "agent_executable"

    def _extract_data_source(self, required_inputs: list) -> str:
        """Extract the primary data source/database from required_inputs annotations."""
        for inp in required_inputs:
            db = inp.get("database")
            if db:
                return db
        for inp in required_inputs:
            src = inp.get("source", "")
            if src in ("external_data", "web_search"):
                return src
        return None

    def _compute_task_confidence_ceiling(
        self, depends_on: list, prior_outputs: dict
    ) -> dict:
        """Determine if confidence is capped by missing/low-quality upstream outputs."""
        if not depends_on:
            return {"capped": False, "ceiling": "high", "reason": None}

        missing_deps = [
            dep for dep in depends_on if dep not in prior_outputs
        ]
        if missing_deps:
            dep_names = [
                self.dependency_map["sections"].get(d, {}).get("name", f"§{d}")
                for d in missing_deps
            ]
            return {
                "capped": True,
                "ceiling": "medium",
                "reason": f"Blocked by incomplete upstream: {', '.join(dep_names)}",
            }
        return {"capped": False, "ceiling": "high", "reason": None}

    def _assemble_input_package(self, section_config: dict, prior_outputs: dict, phase1_data: dict) -> dict:
        package = {}
        section_num = section_config.get("section_number", "")

        # Inject learning context from past runs
        learning_ctx = self.learning.build_learning_context(str(section_num))
        if learning_ctx:
            package["learning_context"] = learning_ctx

        # Inject CEO-provided data relevant to this section
        ceo_data = get_relevant_ceo_data(str(section_num))
        if ceo_data:
            package["ceo_provided_data"] = ceo_data

        # Inject cross-section context from completed sections
        if prior_outputs:
            package["cross_section_context"] = {
                k: v for k, v in prior_outputs.items()
                if isinstance(v, dict)
            }

        for req in section_config.get("required_inputs", []):
            field = req["field"]
            source = req.get("source", "prior_task")
            if source == "phase1_memory":
                value = phase1_data.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    if field == "idea_summary":
                        value = (
                            phase1_data.get("idea_summary")
                            or phase1_data.get("market_scope")
                            or ""
                        )
                        if not value:
                            ceo_a = phase1_data.get("ceo_assumptions", [])
                            if ceo_a:
                                parts = [f"{a.get('question', '')}: {a.get('answer', '')}" for a in ceo_a if a.get("answer")]
                                value = "Business idea: " + "; ".join(parts) if parts else ""
                        if not value:
                            approved = phase1_data.get("approved_decision") or {}
                            value = approved.get("rationale", "") or approved.get("summary", "") or "Approved business idea — details pending"
                    elif field == "ceo_assumptions":
                        value = phase1_data.get("ceo_assumptions", [])
                    elif field == "approved_decision":
                        value = phase1_data.get("approved_decision") or {}
                    elif field == "business_type":
                        value = phase1_data.get("business_type", "saas")
                    elif field == "market_scope":
                        value = phase1_data.get("idea_summary", "") or phase1_data.get("market_scope", "General market")
                    elif field == "opportunity_description":
                        value = phase1_data.get("idea_summary", "") or "Business opportunity — from Phase 1"
                package[field] = value
            elif source == "prior_task":
                value = self._find_field_in_prior_outputs(field, prior_outputs)
                if value is None:
                    defaults = {
                        "pest_analysis": [],
                        "five_forces": [],
                        "risks_opportunities": {"risks": [], "opportunities": []},
                    }
                    value = defaults.get(field)
                package[field] = value

        # Hard constraint propagation — enforce numerical consistency across sections
        hard_constraints = self._extract_hard_constraints(prior_outputs)
        if hard_constraints:
            package["hard_constraints"] = hard_constraints

        # Confidence ceiling — agent cannot claim higher confidence than weakest upstream input
        confidence_ceiling = self._compute_confidence_ceiling(section_config, prior_outputs)
        if confidence_ceiling:
            package["confidence_ceiling"] = confidence_ceiling

        # Uncertainty propagation — upstream unknowns that this agent must be aware of
        propagated_uncertainties = self._propagate_uncertainties(section_config, prior_outputs)
        if propagated_uncertainties:
            package["upstream_uncertainties"] = propagated_uncertainties

        return package

    def _find_field_in_prior_outputs(self, field: str, prior_outputs: dict):
        for section_output in prior_outputs.values():
            if not isinstance(section_output, dict):
                continue
            if field in section_output:
                return section_output[field]
            for nested_value in section_output.values():
                if isinstance(nested_value, dict) and field in nested_value:
                    return nested_value[field]
        return None

    def _extract_hard_constraints(self, prior_outputs: dict) -> dict:
        """Extract binding numerical facts from upstream sections.

        These are numbers that downstream agents MUST use exactly — not reinterpret.
        Prevents contradictions like Marketing saying $200/unit but Financial assuming $150.
        """
        constraints = {}

        for section_num, output in prior_outputs.items():
            if not isinstance(output, dict):
                continue

            # Revenue assumptions from Marketing (Section 8)
            rev = output.get("revenue_assumptions")
            if isinstance(rev, dict):
                price = rev.get("price_per_unit")
                vol_y1 = rev.get("volume_year1")
                vol_y2 = rev.get("volume_year2")
                vol_y3 = rev.get("volume_year3")
                if price is not None:
                    constraints["price_per_unit"] = {"value": price, "source": f"Section {section_num}"}
                if vol_y1 is not None:
                    constraints["volume_year1"] = {"value": vol_y1, "source": f"Section {section_num}"}
                    if price is not None:
                        try:
                            constraints["revenue_year1"] = {
                                "value": float(price) * float(vol_y1),
                                "source": f"Section {section_num} (price × volume)",
                            }
                        except (TypeError, ValueError):
                            pass
                if vol_y2 is not None:
                    constraints["volume_year2"] = {"value": vol_y2, "source": f"Section {section_num}"}
                if vol_y3 is not None:
                    constraints["volume_year3"] = {"value": vol_y3, "source": f"Section {section_num}"}

            # CAC from Marketing
            cac = output.get("cac_assumptions")
            if isinstance(cac, dict) and cac.get("cac_estimate") is not None:
                constraints["cac_estimate"] = {"value": cac["cac_estimate"], "source": f"Section {section_num}"}

            # Headcount from Org Designer (Section 4/11)
            headcount = output.get("headcount_plan")
            if isinstance(headcount, dict):
                total = headcount.get("total_headcount") or headcount.get("year1_headcount")
                if total is not None:
                    constraints["headcount_year1"] = {"value": total, "source": f"Section {section_num}"}

            # Break-even from Financial (Section 12)
            be = output.get("break_even_analysis")
            if isinstance(be, dict) and be.get("break_even_month"):
                constraints["break_even_month"] = {"value": be["break_even_month"], "source": f"Section {section_num}"}

        return constraints

    def _compute_confidence_ceiling(self, section_config: dict, prior_outputs: dict) -> Optional[str]:
        """An agent's confidence cannot exceed the weakest confidence of its upstream dependencies.

        If your inputs are "low" confidence, your output cannot honestly be "high".
        """
        confidence_rank = {"high": 3, "medium": 2, "low": 1}
        depends_on = section_config.get("depends_on", [])

        if not depends_on:
            return None

        min_confidence = 3
        for dep_section in depends_on:
            dep_output = prior_outputs.get(str(dep_section), {})
            if isinstance(dep_output, dict):
                dep_confidence = dep_output.get("confidence_score", "medium")
                rank = confidence_rank.get(dep_confidence, 2)
                min_confidence = min(min_confidence, rank)

        rank_to_label = {3: "high", 2: "medium", 1: "low"}
        ceiling = rank_to_label.get(min_confidence, "medium")
        return ceiling

    def _propagate_uncertainties(self, section_config: dict, prior_outputs: dict) -> list:
        """Collect uncertainties from upstream sections that this section depends on.

        If Marketing flagged "pricing is uncertain — no market validation", then Financial
        must know it's building on shaky ground, not treat the price as gospel.
        """
        depends_on = section_config.get("depends_on", [])
        propagated = []

        for dep_section in depends_on:
            dep_output = prior_outputs.get(str(dep_section), {})
            if not isinstance(dep_output, dict):
                continue

            # Collect explicit uncertainties
            uncertainties = dep_output.get("uncertainties", [])
            for u in uncertainties[:3]:
                if isinstance(u, str):
                    propagated.append({"from_section": str(dep_section), "uncertainty": u})
                elif isinstance(u, dict):
                    propagated.append({"from_section": str(dep_section), "uncertainty": u.get("description", str(u))})

            # Collect low-confidence assumptions
            assumptions = dep_output.get("assumptions_used", [])
            for a in assumptions:
                if isinstance(a, dict) and a.get("confidence") == "low":
                    propagated.append({
                        "from_section": str(dep_section),
                        "uncertainty": f"Low-confidence assumption: {a.get('statement', a.get('assumption', str(a)))}",
                    })

            # Flag hypothesis warnings from upstream
            hyp_warnings = dep_output.get("_hypothesis_warnings", [])
            for hw in hyp_warnings[:2]:
                propagated.append({"from_section": str(dep_section), "uncertainty": f"Upstream math concern: {hw}"})

        return propagated[:10]

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _read_phase1_session(self, session_id: str) -> Optional[dict]:
        try:
            session = self.db.client.table("sessions") \
                .select("*").eq("id", session_id).execute()
            if not session.data:
                return None

            session_data = session.data[0]
            print(f"[MotherAgent] Session fields: {list(session_data.keys())}")

            messages = self.db.client.table("messages") \
                .select("*").eq("session_id", session_id) \
                .order("received_at", desc=False).execute()

            assumptions = self.db.client.table("assumptions") \
                .select("*").eq("session_id", session_id).execute()

            decisions = self.db.client.table("decisions") \
                .select("*").eq("session_id", session_id).execute()

            print(f"[MotherAgent] Messages: {len(messages.data)}")
            print(f"[MotherAgent] Assumptions: {len(assumptions.data)}")
            print(f"[MotherAgent] Decisions: {len(decisions.data)}")

            # The idea is the content of the first message in the session
            # TASK 4: Skip Gate 2 command messages when building idea_summary
            gate2_commands = {"agree", "kill", "edit", "add"}
            idea_summary = ""
            if messages.data:
                for msg in messages.data:
                    content = msg.get("content", "").strip()
                    if content.lower() not in gate2_commands and len(content) > 10:
                        idea_summary = content
                        break

            # Fallback: try session fields that may exist on newer schemas
            if not idea_summary:
                idea_summary = (
                    session_data.get("current_idea") or
                    session_data.get("idea") or
                    session_data.get("raw_idea") or
                    ""
                )

            # Last resort: build from assumptions Q&A
            if not idea_summary and assumptions.data:
                qa_parts = [
                    f"{a.get('question_asked', '')}: {a.get('ceo_answer', '')}"
                    for a in assumptions.data
                    if a.get("ceo_answer")
                ]
                if qa_parts:
                    idea_summary = "Business idea based on CEO answers: " + "; ".join(qa_parts)

            # Get approved decision
            approved_decision = None
            for d in decisions.data:
                if d.get("status") in ("approved", "APPROVED", "completed", "COMPLETED"):
                    approved_decision = d
                    break
            if not approved_decision and decisions.data:
                approved_decision = decisions.data[0]

            # Build Q&A from messages (answers) + assumptions (questions)
            import re as _re
            ceo_answers = [m.get("content", "") for m in messages.data[1:]]
            ceo_assumptions = []
            for i, a in enumerate(assumptions.data):
                statement = a.get("statement", "")
                q_match = _re.search(r"requires clarification about: (.+)$", statement)
                question = q_match.group(1) if q_match else a.get("question_asked", "")
                answer = ceo_answers[i] if i < len(ceo_answers) else a.get("ceo_answer", "")
                if answer:
                    ceo_assumptions.append({"question": question, "answer": answer})

            print(f"[MotherAgent] idea_summary: {idea_summary[:100] if idea_summary else 'EMPTY'}")
            print(f"[MotherAgent] ceo_assumptions: {len(ceo_assumptions)}")
            print(f"[MotherAgent] approved_decision: {approved_decision is not None}")

            return {
                "session": session_data,
                "assumptions": assumptions.data,
                "decisions": decisions.data,
                "idea_summary": idea_summary,
                "ceo_assumptions": ceo_assumptions,
                "approved_decision": approved_decision,
                "business_type": session_data.get("business_type", "saas"),
                "market_scope": idea_summary,
            }
        except Exception as e:
            print(f"[MotherAgent] Failed to read Phase 1 session: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _validate_output(self, section: str, output: dict) -> tuple:
        try:
            section_to_schema = {
                "1": "opportunity_analyst",
                "3": "environment_research",
                "4": "organisation_designer",
                "5": "swot_synthesizer",
                "8": "marketing_strategy",
                "10": "operations",
                "12": "financial_modelling",
                "13": "launch_contingency",
                "executive_summary": "summary_agent",
            }
            schema_name = section_to_schema.get(str(section))
            if not schema_name:
                return True, None

            module = __import__(
                f"schemas.outputs.{schema_name}",
                fromlist=[schema_name]
            )
            class_name = "".join(w.capitalize() for w in schema_name.split("_")) + "Output"
            schema_class = getattr(module, class_name)
            schema_class(**output)
            return True, None
        except Exception as e:
            return False, str(e)

    def _enforce_constitution(self, section: str, output: dict) -> list:
        """Check output against enforceable constitution rules. Returns list of violations."""
        violations = []
        if not isinstance(output, dict):
            return violations

        # Rule: Every assumption must have confidence + source labels
        assumptions = output.get("assumptions_used", []) or output.get("assumption_log", [])
        for i, a in enumerate(assumptions):
            if not isinstance(a, dict):
                continue
            if "confidence" not in a and "label" not in a:
                violations.append(f"Assumption {i+1} missing confidence label")
                break
            source = a.get("source") or a.get("label")
            valid_sources = {"validated", "alex_provided", "agent_inferred", "assumed"}
            if source and source not in valid_sources:
                violations.append(f"Assumption {i+1} has invalid source: '{source}'")
                break

        # Rule: Financial plan must have three_statement_model and break_even
        if str(section) == "12":
            if not output.get("three_statement_model"):
                violations.append("Financial plan missing three_statement_model")
            if not output.get("break_even_analysis"):
                violations.append("Financial plan missing break_even_analysis")

        # Rule: confidence_score must be present
        if "confidence_score" not in output and str(section) != "executive_summary":
            violations.append("Missing confidence_score field")

        # Rule: Never present assumed numbers as validated
        for a in assumptions:
            if not isinstance(a, dict):
                continue
            source = a.get("source") or a.get("label", "")
            conf = a.get("confidence", "")
            if source == "validated" and conf == "low":
                violations.append(f"Assumption claims 'validated' but has 'low' confidence — suspicious")
                break

        return violations

    def _find_agent_config_by_name(self, agent_name: str) -> Optional[dict]:
        return self.agent_roster["agents"].get(agent_name)

    def _find_agent_config_by_jid(self, jid: str) -> Optional[dict]:
        for name, config in self.agent_roster["agents"].items():
            env_jid = os.getenv(config.get("jid_env", ""), "")
            if env_jid == jid:
                return config
        return None

    def _apply_edit(self, tasks: list, edits: dict) -> list:
        for task in tasks:
            task_name = task["task_name"]
            if task_name in edits:
                task.update(edits[task_name])
        return tasks

    def _classify_new_task(self, new_task_description: str, applicable_sections: list) -> dict:
        return {
            "task_name": new_task_description,
            "bp_section": "custom",
            "purpose": "CEO-added task",
            "required_inputs": [],
            "input_source": "ceo_answer",
            "output_required": "Structured output as specified",
            "output_format": "structured_json",
            "owner": "mother_agent",
            "acceptance_criteria": "Output present and non-empty",
            "uncertainty_level": "medium",
            "confidence_score": "medium",
            "timeout_seconds": 90,
        }

    def _update_memory(self, session_id: str, run_id: str, group_outputs: dict):
        for section, output in group_outputs.items():
            if isinstance(output, dict):
                assumptions = output.get("assumptions_used", [])
                for i, assumption in enumerate(assumptions):
                    assumption_id = f"assumption_phase2_{session_id[:8]}_{int(time.time())}_{i}"
                    self.db.client.table("assumptions").insert({
                        "assumption_id": assumption_id,
                        "session_id": session_id,
                        "statement": assumption.get("statement", ""),
                        "confidence": assumption.get("confidence", "medium"),
                        "status": "active",
                    }).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — used when running the Mother Agent standalone
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    jid = os.getenv("MOTHER_AGENT_JID")
    password = os.getenv("MOTHER_AGENT_PASSWORD")

    if not jid or not password:
        raise ValueError("MOTHER_AGENT_JID and MOTHER_AGENT_PASSWORD must be set in .env")

    agent = MotherAgent(jid=jid, password=password)

    agent._start_child_agents_sync()

    await agent.start(auto_register=True)
    print("[MotherAgent] Running. Press Ctrl+C to stop.")

    try:
        while agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
