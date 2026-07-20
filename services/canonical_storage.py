"""
Canonical Storage Service

Ensures every entity has one canonical table with knowledge_base as indexed view.

Entity Type          Canonical Table         Indexed In KB
──────────────────────────────────────────────────────────
Assumption           assumptions             YES (search)
Decision             decisions               YES (search)
Agent Output         agent_outputs           YES (search)
Task                 tasks                   NO (actions not evidence)
Contradiction/Gap    bp12_register           (governance, not KB)
"""

import json
import logging
from datetime import datetime
from typing import Tuple
from memory.supabase_client import supabase as db

logger = logging.getLogger(__name__)


class CanonicalStorageError(Exception):
    """Raised when canonical storage fails"""
    pass


class CanonicalStorage:
    """
    Stores entities in their canonical tables + KB index.
    Provides bidirectional linking between canonical tables and knowledge_base.
    """

    def __init__(self):
        """Initialize with database client"""
        self.db = db

    def store_assumption(
        self,
        session_id: str,
        content: str,
        status: str,  # "validated" or "assumed_not_clarified"
        source: str,  # "ceo_input" or "agent_inferred"
        confidence: float = 0.5,
        created_by: str = "system",
    ) -> Tuple[str, str]:
        """
        Store assumption in BOTH canonical table + KB index.

        Returns: (assumption_id, knowledge_base_id)

        Example:
            canonical = CanonicalStorage()
            assumption_id, kb_id = canonical.store_assumption(
                session_id="sess-123",
                content="Market size is €50M",
                status="validated",
                source="ceo_input",
                confidence=0.9,
                created_by="Alex",
            )
        """

        try:
            logger.info(f"[Canonical] Storing assumption: {content[:50]}...")

            # STEP 1: Insert into canonical assumptions table
            assumption_data = {
                "session_id": session_id,
                "content": content,
                "status": status,
                "source": source,
                "confidence": confidence,
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }

            assumption = self.db.table("assumptions").insert(assumption_data).execute()

            if not assumption.data:
                raise CanonicalStorageError("Failed to insert assumption into canonical table")

            assumption_id = assumption.data[0]["id"]
            logger.info(f"[Canonical] Assumption created: {assumption_id}")

            # STEP 2: Create knowledge_base entry (indexed view)
            logger.info(f"[Canonical] Creating KB index entry for assumption {assumption_id}")

            kb_data = {
                "content": content,
                "source_type": "assumption_lifecycle",
                "epistemic_status": "ASSUMPTION",
                "assumption_id": assumption_id,  # Link back to canonical
                "session_id": session_id,
                "confidence": confidence,
                "metadata": {
                    "original_source": source,
                    "status_at_time": status,
                    "created_by": created_by,
                },
                "created_at": datetime.utcnow().isoformat(),
            }

            kb_entry = self.db.table("knowledge_base").insert(kb_data).execute()

            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry")

            kb_id = kb_entry.data[0]["id"]
            logger.info(f"[Canonical] KB entry created: {kb_id}")

            # STEP 3: Link both ways (bidirectional reference)
            logger.info(f"[Canonical] Creating bidirectional links")

            self.db.table("assumptions").update({
                "knowledge_base_id": kb_id
            }).eq("id", assumption_id).execute()

            logger.info(f"[Canonical] Assumption {assumption_id} <-> KB {kb_id} linked successfully")

            return assumption_id, kb_id

        except Exception as e:
            logger.error(f"[Canonical] Error storing assumption: {e}")
            raise CanonicalStorageError(f"Failed to store assumption: {str(e)}")

    def store_decision(
        self,
        session_id: str,
        section_id: str,
        title: str,
        reasoning: str,
        decision_type: str,  # "yes", "adjust", "kill"
        version: int = 1,
        created_by: str = "ceo",
    ) -> Tuple[str, str]:
        """
        Store decision in BOTH canonical table + KB index.

        Returns: (decision_id, knowledge_base_id)

        Example:
            canonical = CanonicalStorage()
            decision_id, kb_id = canonical.store_decision(
                session_id="sess-123",
                section_id="BP.1",
                title="Go with SaaS pricing",
                reasoning="Market expects SaaS, not perpetual",
                decision_type="yes",
                created_by="Alex",
            )
        """

        try:
            logger.info(f"[Canonical] Storing decision: {title}")

            # STEP 1: Insert into canonical decisions table
            decision_data = {
                "session_id": session_id,
                "section_id": section_id,
                "title": title,
                "reasoning": reasoning,
                "status": decision_type,
                "version": version,
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }

            decision = self.db.table("decisions").insert(decision_data).execute()

            if not decision.data:
                raise CanonicalStorageError("Failed to insert decision into canonical table")

            decision_id = decision.data[0]["id"]
            logger.info(f"[Canonical] Decision created: {decision_id}")

            # STEP 2: Create knowledge_base entry
            kb_data = {
                "content": f"{title}: {reasoning}",
                "source_type": "decision",
                "epistemic_status": "CONFIRMED",  # Decisions are confirmed
                "decision_id": decision_id,  # Link back to canonical
                "session_id": session_id,
                "confidence": 0.95,  # CEO decisions are high confidence
                "metadata": {
                    "decision_type": decision_type,
                    "affects_section": section_id,
                    "version": version,
                    "created_by": created_by,
                },
                "created_at": datetime.utcnow().isoformat(),
            }

            kb_entry = self.db.table("knowledge_base").insert(kb_data).execute()

            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry for decision")

            kb_id = kb_entry.data[0]["id"]
            logger.info(f"[Canonical] KB entry created: {kb_id}")

            # STEP 3: Link both ways
            self.db.table("decisions").update({
                "knowledge_base_id": kb_id
            }).eq("id", decision_id).execute()

            logger.info(f"[Canonical] Decision {decision_id} <-> KB {kb_id} linked successfully")

            return decision_id, kb_id

        except Exception as e:
            logger.error(f"[Canonical] Error storing decision: {e}")
            raise CanonicalStorageError(f"Failed to store decision: {str(e)}")

    def store_agent_output(
        self,
        session_id: str,
        run_id: str,
        section_id: str,
        agent_name: str,
        output_json: dict,
        confidence: float = 0.5,
    ) -> Tuple[str, str]:
        """
        Store agent output in BOTH canonical table + KB index.

        Returns: (output_id, knowledge_base_id)

        Example:
            canonical = CanonicalStorage()
            output_id, kb_id = canonical.store_agent_output(
                session_id="sess-123",
                run_id="run-456",
                section_id="BP.1",
                agent_name="opportunity_analyst",
                output_json={"market_size": "€50M", "growth": "23%"},
                confidence=0.75,
            )
        """

        try:
            logger.info(f"[Canonical] Storing agent output from {agent_name}")

            # STEP 1: Insert into canonical agent_outputs table
            output_data = {
                "session_id": session_id,
                "run_id": run_id,
                "section_id": section_id,
                "agent_name": agent_name,
                "output_json": output_json,
                "confidence": confidence,
                "created_at": datetime.utcnow().isoformat(),
            }

            output = self.db.table("agent_outputs").insert(output_data).execute()

            if not output.data:
                raise CanonicalStorageError("Failed to insert output into canonical table")

            output_id = output.data[0]["id"]
            logger.info(f"[Canonical] Agent output created: {output_id}")

            # STEP 2: Create knowledge_base entry
            output_text = json.dumps(output_json, indent=2)

            kb_data = {
                "content": output_text,
                "source_type": "agent_insight",
                "epistemic_status": "INFERRED",  # Agent-produced, not yet validated
                "output_id": output_id,  # Link back to canonical
                "session_id": session_id,
                "agent_name": agent_name,
                "confidence": confidence,
                "metadata": {
                    "section": section_id,
                    "run_id": run_id,
                    "agent_name": agent_name,
                },
                "created_at": datetime.utcnow().isoformat(),
            }

            kb_entry = self.db.table("knowledge_base").insert(kb_data).execute()

            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry for output")

            kb_id = kb_entry.data[0]["id"]
            logger.info(f"[Canonical] KB entry created: {kb_id}")

            # STEP 3: Link both ways
            self.db.table("agent_outputs").update({
                "knowledge_base_id": kb_id
            }).eq("id", output_id).execute()

            logger.info(f"[Canonical] Agent output {output_id} <-> KB {kb_id} linked successfully")

            return output_id, kb_id

        except Exception as e:
            logger.error(f"[Canonical] Error storing agent output: {e}")
            raise CanonicalStorageError(f"Failed to store agent output: {str(e)}")

    def create_task(
        self,
        session_id: str,
        title: str,
        task_type: str,  # interview, research, validation, followup
        relates_to_node: str = None,
        relates_to_chunk_id: str = None,
        priority: int = 3,
        created_by: str = "system",
    ) -> str:
        """
        Create a task (NOT stored in KB, only in tasks table).
        Tasks are ACTIONS, not EVIDENCE.

        Returns: task_id

        Example:
            canonical = CanonicalStorage()
            task_id = canonical.create_task(
                session_id="sess-123",
                title="Schedule customer interviews",
                task_type="interview",
                relates_to_node="BP.5",
                priority=1,
                created_by="mother_agent",
            )
        """

        try:
            logger.info(f"[Canonical] Creating task: {title}")

            task_data = {
                "session_id": session_id,
                "title": title,
                "task_type": task_type,
                "relates_to_node": relates_to_node,
                "relates_to_chunk": relates_to_chunk_id,
                "priority": priority,
                "status": "open",
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }

            task = self.db.table("tasks").insert(task_data).execute()

            if not task.data:
                raise CanonicalStorageError("Failed to create task")

            task_id = task.data[0]["id"]
            logger.info(f"[Canonical] Task created: {task_id}")

            # NOTE: Task is NOT stored in KB (tasks are actions, not evidence)

            return task_id

        except Exception as e:
            logger.error(f"[Canonical] Error creating task: {e}")
            raise CanonicalStorageError(f"Failed to create task: {str(e)}")

    def file_contradiction(
        self,
        session_id: str,
        bp_node: str,  # "BP.12" or auto-assign
        issue_type: str,  # "contradiction", "gap", "escalation", "error"
        title: str,
        chunk1_id: str,
        chunk2_id: str = None,
        description: str = None,
        run_id: str = None,
    ) -> str:
        """
        File a contradiction/gap/escalation to BP.12 register.
        This creates a GOVERNANCE record, not a KB entry.

        Returns: bp12_record_id

        Example:
            canonical = CanonicalStorage()
            bp12_id = canonical.file_contradiction(
                session_id="sess-123",
                bp_node="BP.12",
                issue_type="contradiction",
                title="Price conflict",
                chunk1_id="chunk-1",
                chunk2_id="chunk-2",
                description="BP.9 says €100, BP.12 says €150",
            )
        """

        try:
            logger.info(f"[Canonical] Filing {issue_type} to BP.12: {title}")

            # Auto-assign BP.12 node if needed
            if bp_node == "BP.12":
                count_result = self.db.table("bp12_register")\
                    .select("id", count="exact")\
                    .eq("session_id", session_id)\
                    .execute()

                count = count_result.count if hasattr(count_result, 'count') else 0
                next_num = count + 1
                bp_node = f"BP.12.{next_num}"
                logger.info(f"[Canonical] Auto-assigned BP node: {bp_node}")

            bp12_data = {
                "session_id": session_id,
                "run_id": run_id,
                "bp_node": bp_node,
                "issue_type": issue_type,
                "title": title,
                "description": description,
                "primary_chunk_id": chunk1_id,
                "conflicting_chunk_id": chunk2_id,
                "status": "open",
                "created_at": datetime.utcnow().isoformat(),
            }

            record = self.db.table("bp12_register").insert(bp12_data).execute()

            if not record.data:
                raise CanonicalStorageError("Failed to file to BP.12")

            record_id = record.data[0]["id"]
            logger.info(f"[Canonical] BP.12 record created: {record_id} at {bp_node}")

            return record_id

        except Exception as e:
            logger.error(f"[Canonical] Error filing to BP.12: {e}")
            raise CanonicalStorageError(f"Failed to file to BP.12: {str(e)}")

    def verify_canonical_structure(self) -> dict:
        """
        Verify that canonical structure is intact.
        Used for health checks and tests.

        Returns: {
            "structure_valid": bool,
            "linked_count": int,
            "orphaned_count": int,
        }
        """

        try:
            logger.info("[Canonical] Verifying canonical structure...")

            # Check for linked assumptions
            linked = self.db.table("assumptions")\
                .select("id", count="exact")\
                .not_.is_("knowledge_base_id", "null")\
                .execute()

            linked_count = linked.count if hasattr(linked, 'count') else 0

            # Check for orphaned KB entries
            orphaned = self.db.table("knowledge_base")\
                .select("id", count="exact")\
                .is_("assumption_id", "null")\
                .is_("decision_id", "null")\
                .is_("output_id", "null")\
                .is_("task_id", "null")\
                .is_("bp12_record_id", "null")\
                .execute()

            orphaned_count = orphaned.count if hasattr(orphaned, 'count') else 0

            logger.info(f"[Canonical] Structure valid. Linked: {linked_count}, Orphaned: {orphaned_count}")

            return {
                "structure_valid": True,
                "linked_count": linked_count,
                "orphaned_count": orphaned_count,
            }

        except Exception as e:
            logger.error(f"[Canonical] Error verifying structure: {e}")
            return {
                "structure_valid": False,
                "error": str(e),
            }
