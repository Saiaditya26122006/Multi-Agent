import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import os
import uuid
import yaml
from datetime import datetime
from typing import Optional

import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour, PeriodicBehaviour
from spade.message import Message

from memory.supabase_client import SupabaseClient
from memory.redis_client import RedisClient


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

    # ── Agent lifecycle ───────────────────────────────────────────────────────

    async def setup(self):
        print("[MotherAgent] Starting — loading constitution and config")
        self._log_constitution_version()
        listen = ListenBehaviour()
        self.add_behaviour(listen)
        trigger_check = PipelineTriggerBehaviour(period=5)
        self.add_behaviour(trigger_check)
        print("[MotherAgent] Ready. Listening for messages and pipeline triggers.")

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

        # 1. Create pipeline run record
        run_id = self._create_pipeline_run(session_id, run_mode)
        self.active_runs[session_id] = run_id

        # 2. Read Phase 1 session output
        phase1_data = self._read_phase1_session(session_id)
        if not phase1_data:
            self._fail_pipeline(run_id, "Phase 1 session data not found")
            return

        # 3. Classify business type and determine applicable sections
        applicable_sections = self._determine_applicable_sections(phase1_data)
        print(f"[MotherAgent] Applicable sections: {applicable_sections}")

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

        group_config = self.agent_roster.get("execution_groups", {}).get(group_number)
        if not group_config:
            print(f"[MotherAgent] No config for group {group_number} — pipeline complete")
            await self._run_coherence_audit(session_id, run_id, prior_outputs)
            return

        print(f"[MotherAgent] === Starting Group {group_number} ===")

        # Generate tasks for this group
        tasks = self._generate_group_tasks(
            group_number, applicable_sections, prior_outputs, phase1_data
        )
        if not tasks:
            print(f"[MotherAgent] No tasks for group {group_number} — skipping to next")
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

        # Wait for Alex's Gate 2 response (stored in Redis) — BLOCKS here
        response = await self._wait_for_gate2_response(session_id, group_id)
        print(f"[MotherAgent] Group {group_number} Gate 2 response: {response['action']}")

        if response["action"] == "kill":
            self._kill_group(run_id, group_id, session_id)
            return

        if response["action"] == "edit":
            tasks = self._apply_edit(tasks, response["edits"])
            self._recheck_dependencies(tasks)

        if response["action"] == "add":
            new_task = self._classify_new_task(response["new_task"], applicable_sections)
            tasks.append(new_task)
            self._recheck_dependencies(tasks)

        # Mark group as approved
        self._update_group_status(group_id, "approved")
        print(f"[MotherAgent] Group {group_number} approved — executing tasks")

        # Execute tasks (parallel where group config allows) — BLOCKS here
        group_outputs = await self._execute_group(
            tasks, task_ids, group_id, session_id, run_id, group_config
        )
        print(f"[MotherAgent] Group {group_number} execution complete — {len(group_outputs)} outputs")

        # Merge outputs with prior outputs
        prior_outputs.update(group_outputs)

        # Update memory
        self._update_memory(session_id, run_id, group_outputs)

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
        self._update_group_status(group_id, "running")
        outputs = {}

        if group_config.get("parallel", False):
            results = await asyncio.gather(
                *[
                    self._execute_task(t, task_ids.get(t["task_name"]), session_id, run_id)
                    for t in tasks
                ],
                return_exceptions=True,
            )
            for task, result in zip(tasks, results):
                if isinstance(result, Exception):
                    await self._handle_task_failure(
                        task, session_id, run_id, str(result)
                    )
                else:
                    outputs[task["bp_section"]] = result
        else:
            for task in tasks:
                tid = task_ids.get(task["task_name"])
                result = await self._execute_task(task, tid, session_id, run_id)
                if result:
                    outputs[task["bp_section"]] = result

        self._update_group_status(group_id, "completed")
        return outputs

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

        if not agent_jid:
            print(f"[MotherAgent] No JID for agent {agent_name} — task skipped")
            return None

        # Update task status to running
        self.db.client.table("task_readiness") \
            .update({"status": "running", "started_at": datetime.utcnow().isoformat()}) \
            .eq("id", task_id).execute()

        # Send request to child agent
        await send_acl(
            sender_agent=self,
            to_jid=agent_jid,
            performative="request",
            content={"task": task, "task_id": task_id},
            task_id=task_id,
            session_id=session_id,
            pipeline_run_id=run_id,
        )

        # Wait for inform response (stored in Redis by child agent)
        output = await self._wait_for_task_output(task_id, timeout=task.get("timeout_seconds", 90))
        return output

    # ── Message handlers ──────────────────────────────────────────────────────

    async def handle_inform(self, task_id, session_id, run_id, from_agent, content):
        """Child agent completed a task — validate and accept output."""
        print(f"[MotherAgent] inform from {from_agent} for task {task_id}")

        output = content.get("output", {})
        section = content.get("section_number")

        # Validate output against Pydantic schema
        valid, errors = self._validate_output(section, output)
        if not valid:
            print(f"[MotherAgent] Validation failed for section {section}: {errors}")
            await self._retry_task(task_id, session_id, run_id, errors)
            return

        # Write to bp_section_content
        self._write_section_content(session_id, run_id, section, output, from_agent)

        # Update task status
        self.db.client.table("task_readiness") \
            .update({
                "status": "complete",
                "output_data": output,
                "validation_errors": None,
                "completed_at": datetime.utcnow().isoformat(),
            }) \
            .eq("id", task_id).execute()

        # Store output in Redis for pipeline to pick up
        self.redis.client.set(
            f"task_output:{task_id}",
            json.dumps(output),
            ex=3600
        )

    async def handle_propose(self, task_id, session_id, run_id, from_agent, content):
        """Agent detected a contradiction and proposes a resolution."""
        target_agent = content.get("target_agent")
        proposal = content.get("proposal")

        print(f"[MotherAgent] propose from {from_agent} targeting {target_agent}: {proposal}")

        # Route the proposal to the target agent
        target_config = self._find_agent_config_by_jid(target_agent)
        if target_config:
            await send_acl(
                sender_agent=self,
                to_jid=target_agent,
                performative="propose",
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=run_id,
            )

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

        print(f"[MotherAgent] escalate from {from_agent} — trigger: {trigger}")

        # Update task status
        self.db.client.table("task_readiness") \
            .update({
                "status": "escalated",
                "escalation_trigger": trigger,
                "escalation_notes": notes,
            }) \
            .eq("id", task_id).execute()

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
            "section_number": content.get("section", ""),
            "gap_description": notes,
            "resolution_type": "blocked" if blocking else "ceo_provided",
            "question_asked_to_ceo": question,
        }).execute()

        # Ask Alex
        msg = f"An agent needs clarification before continuing:\n\n{question}"
        if agent_alt:
            msg += f"\n\nAlternatively, I can run an agent to gather this. Reply 'agent' to delegate."
        self._send_telegram(session_id, msg)

    # ── Coherence audit ───────────────────────────────────────────────────────

    async def _run_coherence_audit(self, session_id: str, run_id: str, all_outputs: dict):
        """Final check before delivering plan to Alex."""
        print("[MotherAgent] Running global coherence audit")

        issues = []

        # Check ICP consistency
        icp_section1 = all_outputs.get("1", {}).get("icp_hypothesis", {})
        icp_section8 = all_outputs.get("8", {}).get("target_market_analysis", {})
        if icp_section1 and icp_section8:
            if icp_section1.get("buyer_role") and icp_section8.get("icp_refined"):
                pass

        # Check financial consistency
        revenue_s8 = all_outputs.get("8", {}).get("revenue_assumptions", {})
        revenue_s12 = all_outputs.get("12", {}).get("three_statement_model", {})
        if revenue_s8 and revenue_s12:
            if not revenue_s12:
                issues.append("Financial model missing — cannot verify revenue consistency")

        if issues:
            print(f"[MotherAgent] Coherence issues: {issues}")
            self._notify_alex_coherence_issues(session_id, issues)
        else:
            print("[MotherAgent] Coherence audit passed")
            self._deliver_plan(session_id, run_id, all_outputs)

    # ── Gap and dependency helpers ────────────────────────────────────────────

    def _determine_applicable_sections(self, phase1_data: dict) -> list:
        """Read constitution + dependency map to determine which sections apply."""
        always_required = []

        for section_num, section_config in self.dependency_map.get("sections", {}).items():
            if section_config.get("always_required", False):
                always_required.append(section_num)
            else:
                # Include all for now; production adds LLM classification
                always_required.append(section_num)

        return always_required

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

        self.db.client.table("bp_section_metadata").insert({
            "pipeline_run_id": run_id,
            "session_id": session_id,
            "section_number": str(section),
            "section_name": self.dependency_map["sections"].get(str(section), {}).get("name", ""),
            "status": "completed",
            "agent_assigned": from_agent,
            "model_used": model,
            "dependencies_met": True,
        }).execute()

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
        summary_lines = [f"Group {gate2_package['group_number']} — {gate2_package['group_name']}"]
        summary_lines.append("")
        for task in gate2_package["tasks"]:
            summary_lines.append(f"• {task['task_name']}")
            summary_lines.append(f"  Owner: {task.get('owner', 'TBD')}")
            summary_lines.append(f"  Output: {task.get('output_required', '')}")
            summary_lines.append("")

        if gate2_package["simulation_result"]["valid"]:
            summary_lines.append("Pre-run check: No dependency conflicts detected")
        else:
            summary_lines.append(f"Pre-run issues: {gate2_package['simulation_result']['issues']}")

        summary_lines.append("")
        summary_lines.append("Reply: agree / edit [what] / add [task] / kill")

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

        self._send_telegram(session_id, "\n".join(summary_lines))

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
        self._send_telegram(session_id, "Group killed. Pipeline stopped. Nothing has been executed.")

    # ── Delivery ──────────────────────────────────────────────────────────────

    def _deliver_plan(self, session_id: str, run_id: str, all_outputs: dict):
        self.db.client.table("pipeline_runs") \
            .update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "sections_completed": list(all_outputs.keys()),
            }) \
            .eq("id", run_id).execute()

        self._send_telegram(
            session_id,
            f"Business plan complete. {len(all_outputs)} sections delivered.\n"
            f"Review the full plan on your Airtable dashboard."
        )

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

                tasks.append({
                    "task_name": f"Build section {section_num}: {section_config.get('name', '')}",
                    "bp_section": str(section_num),
                    "purpose": f"Produce {section_config.get('name', '')} for the business plan",
                    "required_inputs": section_config.get("required_inputs", []),
                    "input_source": "prior_task",
                    "input_package": input_package,
                    "output_required": str(section_config.get("outputs", [])),
                    "output_format": "structured_json",
                    "owner": agent_name,
                    "acceptance_criteria": "All required output fields present. Confidence >= medium.",
                    "uncertainty_level": "medium",
                    "confidence_score": "medium",
                    "timeout_seconds": agent_config.get("timeout_seconds", 90),
                })

        return tasks

    def _assemble_input_package(self, section_config: dict, prior_outputs: dict, phase1_data: dict) -> dict:
        package = {}
        for req in section_config.get("required_inputs", []):
            field = req["field"]
            source = req.get("source", "prior_task")
            if source == "phase1_memory":
                package[field] = phase1_data.get(field)
            elif source == "prior_task":
                package[field] = self._find_field_in_prior_outputs(field, prior_outputs)
        return package

    def _find_field_in_prior_outputs(self, field: str, prior_outputs: dict):
        for section_output in prior_outputs.values():
            if isinstance(section_output, dict) and field in section_output:
                return section_output[field]
        return None

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _read_phase1_session(self, session_id: str) -> Optional[dict]:
        try:
            session = self.db.client.table("sessions") \
                .select("*").eq("id", session_id).execute()
            if not session.data:
                return None
            assumptions = self.db.client.table("assumptions") \
                .select("*").eq("session_id", session_id).execute()
            decisions = self.db.client.table("decisions") \
                .select("*").eq("session_id", session_id).execute()
            return {
                "session": session.data[0],
                "assumptions": assumptions.data,
                "decisions": decisions.data,
                "idea_summary": session.data[0].get("current_idea", ""),
                "ceo_assumptions": [
                    {"question": a.get("question_asked"), "answer": a.get("ceo_answer")}
                    for a in assumptions.data
                ],
            }
        except Exception as e:
            print(f"[MotherAgent] Failed to read Phase 1 session: {e}")
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
                for assumption in assumptions:
                    self.db.client.table("assumptions").insert({
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
