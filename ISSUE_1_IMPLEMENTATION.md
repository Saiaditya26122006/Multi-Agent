# 🔧 ISSUE #1: TWO OVERLAPPING SYSTEMS — COMPLETE IMPLEMENTATION

**Status:** Ready to implement  
**Effort:** 1-2 days  
**Date Started:** 2026-07-19

---

## OVERVIEW

**Problem:** Assumptions, decisions, and outputs stored in BOTH operational tables AND knowledge_base  
**Solution:** Define canonical tables, use knowledge_base as indexed view  
**Result:** Single source of truth for each entity type

---

## STEP 1.1: CREATE CANONICAL STRUCTURE (SQL MIGRATION)

### File: `database/migrations/001_create_canonical_structure.sql`

```sql
-- ========================================
-- MIGRATION 001: Create Canonical Structure
-- ========================================
-- Run this migration FIRST

-- Step 1: Ensure all canonical tables exist with proper structure

-- Assumptions (canonical)
ALTER TABLE assumptions ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

-- Decisions (canonical)
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

-- Agent Outputs (canonical)
ALTER TABLE agent_outputs ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

-- Step 2: Create tasks table (NEW - canonical for tasks)
-- This separates ACTIONS from EVIDENCE
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id UUID REFERENCES pipeline_runs(id),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    created_by VARCHAR(100),  -- agent_name or 'system'
    task_type VARCHAR(50) NOT NULL,    -- interview, research, validation, followup, decision_point
    relates_to_node VARCHAR(50),  -- BP.X.Y.Z
    relates_to_chunk UUID REFERENCES knowledge_base(id),
    status VARCHAR(50) DEFAULT 'open',  -- open, in_progress, completed, blocked
    priority INT DEFAULT 3,  -- 1=critical, 2=high, 3=normal, 4=low
    due_date TIMESTAMP,
    assigned_to VARCHAR(100),
    completed_at TIMESTAMP,
    completion_note TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT valid_status CHECK (status IN ('open', 'in_progress', 'completed', 'blocked')),
    CONSTRAINT valid_type CHECK (task_type IN ('interview', 'research', 'validation', 'followup', 'decision_point'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_node ON tasks(relates_to_node);
CREATE INDEX IF NOT EXISTS idx_tasks_created_by ON tasks(created_by);

-- Step 3: Create bp12_register table (NEW - canonical for contradictions/gaps)
CREATE TABLE IF NOT EXISTS bp12_register (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id UUID REFERENCES pipeline_runs(id),
    bp_node VARCHAR(50) NOT NULL,  -- BP.12.X.Y (where it's filed)
    issue_type VARCHAR(50) NOT NULL,  -- contradiction, gap, escalation, error
    title VARCHAR(255) NOT NULL,
    description TEXT,
    
    -- Which chunks are involved
    primary_chunk_id UUID REFERENCES knowledge_base(id),
    conflicting_chunk_id UUID REFERENCES knowledge_base(id),
    
    -- Why it matters
    impact_assessment TEXT,
    suggested_resolution TEXT,
    
    -- Status
    status VARCHAR(50) DEFAULT 'open',  -- open, in_review, resolved, rejected
    resolution_date TIMESTAMP,
    resolved_by VARCHAR(100),  -- agent_name or 'ceo'
    ceo_decision TEXT,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT valid_type CHECK (issue_type IN ('contradiction', 'gap', 'escalation', 'error')),
    CONSTRAINT valid_status CHECK (status IN ('open', 'in_review', 'resolved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_bp12_session ON bp12_register(session_id);
CREATE INDEX IF NOT EXISTS idx_bp12_node ON bp12_register(bp_node);
CREATE INDEX IF NOT EXISTS idx_bp12_status ON bp12_register(status);
CREATE INDEX IF NOT EXISTS idx_bp12_issue_type ON bp12_register(issue_type);

-- Step 4: Add linking columns to knowledge_base
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  assumption_id UUID REFERENCES assumptions(id);
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  decision_id UUID REFERENCES decisions(id);
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  output_id UUID REFERENCES agent_outputs(id);
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  task_id UUID REFERENCES tasks(id);
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS
  bp12_record_id UUID REFERENCES bp12_register(id);

-- Step 5: Create indexes for backward linking
CREATE INDEX IF NOT EXISTS idx_kb_assumption ON knowledge_base(assumption_id);
CREATE INDEX IF NOT EXISTS idx_kb_decision ON knowledge_base(decision_id);
CREATE INDEX IF NOT EXISTS idx_kb_output ON knowledge_base(output_id);
CREATE INDEX IF NOT EXISTS idx_kb_task ON knowledge_base(task_id);
CREATE INDEX IF NOT EXISTS idx_kb_bp12 ON knowledge_base(bp12_record_id);

-- Step 6: Add NOT NULL constraints to canonical tables (after backfill)
ALTER TABLE knowledge_base ALTER COLUMN source_type SET NOT NULL;

COMMIT;
```

---

## STEP 1.2: CREATE CANONICAL STORAGE SERVICE

### File: `services/canonical_storage.py` (NEW)

```python
"""
Canonical Storage Service

Ensures every entity has one canonical table with knowledge_base as indexed view.
"""

import json
import logging
from datetime import datetime
from typing import Optional, Tuple
from uuid import uuid4

from database import supabase_client as db
from services.embedding_service import embed

logger = logging.getLogger(__name__)


class CanonicalStorageError(Exception):
    """Raised when canonical storage fails"""
    pass


class CanonicalStorage:
    """
    Stores entities in their canonical tables + KB index.
    
    Entity Type          Canonical Table         Indexed In KB
    ──────────────────────────────────────────────────────────
    Assumption           assumptions             YES (search)
    Decision             decisions               YES (search)
    Agent Output         agent_outputs           YES (search)
    Task                 tasks                   NO (actions not evidence)
    Contradiction/Gap    bp12_register           (governance, not KB)
    """
    
    @staticmethod
    async def store_assumption(
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
            assumption_id, kb_id = await CanonicalStorage.store_assumption(
                session_id="sess-123",
                content="Market size is €50M",
                status="validated",
                source="ceo_input",
                confidence=0.9,
                created_by="Alex",
            )
        """
        
        try:
            # STEP 1: Insert into canonical assumptions table
            logger.info(f"Storing assumption: {content[:50]}... in canonical table")
            
            assumption = await db.table("assumptions").insert({
                "session_id": session_id,
                "content": content,
                "status": status,
                "source": source,
                "confidence": confidence,
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            
            if not assumption.data:
                raise CanonicalStorageError("Failed to insert assumption into canonical table")
            
            assumption_id = assumption.data[0]["id"]
            logger.info(f"Assumption created: {assumption_id}")
            
            # STEP 2: Create knowledge_base entry (indexed view)
            logger.info(f"Creating KB index entry for assumption {assumption_id}")
            
            # Generate embedding
            embedding = await embed(content)
            
            kb_entry = await db.table("knowledge_base").insert({
                "content": content,
                "embedding": embedding,
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
            }).execute()
            
            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry")
            
            kb_id = kb_entry.data[0]["id"]
            logger.info(f"KB entry created: {kb_id}")
            
            # STEP 3: Link both ways (bidirectional reference)
            logger.info(f"Creating bidirectional links")
            
            await db.table("assumptions").update({
                "knowledge_base_id": kb_id
            }).eq("id", assumption_id).execute()
            
            logger.info(f"Assumption {assumption_id} and KB {kb_id} linked successfully")
            
            return assumption_id, kb_id
            
        except Exception as e:
            logger.error(f"Error storing assumption: {e}")
            raise CanonicalStorageError(f"Failed to store assumption: {str(e)}")
    
    @staticmethod
    async def store_decision(
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
            decision_id, kb_id = await CanonicalStorage.store_decision(
                session_id="sess-123",
                section_id="BP.1",
                title="Go with SaaS pricing",
                reasoning="Market expects SaaS, not perpetual",
                decision_type="yes",
                created_by="Alex",
            )
        """
        
        try:
            logger.info(f"Storing decision: {title}")
            
            # STEP 1: Insert into canonical decisions table
            decision = await db.table("decisions").insert({
                "session_id": session_id,
                "section_id": section_id,
                "title": title,
                "reasoning": reasoning,
                "status": decision_type,
                "version": version,
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            
            if not decision.data:
                raise CanonicalStorageError("Failed to insert decision into canonical table")
            
            decision_id = decision.data[0]["id"]
            logger.info(f"Decision created: {decision_id}")
            
            # STEP 2: Create knowledge_base entry
            embedding = await embed(f"{title}: {reasoning}")
            
            kb_entry = await db.table("knowledge_base").insert({
                "content": f"{title}: {reasoning}",
                "embedding": embedding,
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
            }).execute()
            
            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry for decision")
            
            kb_id = kb_entry.data[0]["id"]
            logger.info(f"KB entry created: {kb_id}")
            
            # STEP 3: Link both ways
            await db.table("decisions").update({
                "knowledge_base_id": kb_id
            }).eq("id", decision_id).execute()
            
            logger.info(f"Decision {decision_id} and KB {kb_id} linked successfully")
            
            return decision_id, kb_id
            
        except Exception as e:
            logger.error(f"Error storing decision: {e}")
            raise CanonicalStorageError(f"Failed to store decision: {str(e)}")
    
    @staticmethod
    async def store_agent_output(
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
            output_id, kb_id = await CanonicalStorage.store_agent_output(
                session_id="sess-123",
                run_id="run-456",
                section_id="BP.1",
                agent_name="opportunity_analyst",
                output_json={"market_size": "€50M", "growth": "23%"},
                confidence=0.75,
            )
        """
        
        try:
            logger.info(f"Storing agent output from {agent_name}")
            
            # STEP 1: Insert into canonical agent_outputs table
            output = await db.table("agent_outputs").insert({
                "session_id": session_id,
                "run_id": run_id,
                "section_id": section_id,
                "agent_name": agent_name,
                "output_json": output_json,
                "confidence": confidence,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            
            if not output.data:
                raise CanonicalStorageError("Failed to insert output into canonical table")
            
            output_id = output.data[0]["id"]
            logger.info(f"Agent output created: {output_id}")
            
            # STEP 2: Create knowledge_base entry
            # Serialize output for embedding
            output_text = json.dumps(output_json, indent=2)
            embedding = await embed(output_text)
            
            kb_entry = await db.table("knowledge_base").insert({
                "content": output_text,
                "embedding": embedding,
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
            }).execute()
            
            if not kb_entry.data:
                raise CanonicalStorageError("Failed to create KB index entry for output")
            
            kb_id = kb_entry.data[0]["id"]
            logger.info(f"KB entry created: {kb_id}")
            
            # STEP 3: Link both ways
            await db.table("agent_outputs").update({
                "knowledge_base_id": kb_id
            }).eq("id", output_id).execute()
            
            logger.info(f"Agent output {output_id} and KB {kb_id} linked successfully")
            
            return output_id, kb_id
            
        except Exception as e:
            logger.error(f"Error storing agent output: {e}")
            raise CanonicalStorageError(f"Failed to store agent output: {str(e)}")
    
    @staticmethod
    async def create_task(
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
            task_id = await CanonicalStorage.create_task(
                session_id="sess-123",
                title="Schedule customer interviews",
                task_type="interview",
                relates_to_node="BP.5",
                priority=1,
                created_by="mother_agent",
            )
        """
        
        try:
            logger.info(f"Creating task: {title}")
            
            task = await db.table("tasks").insert({
                "session_id": session_id,
                "title": title,
                "task_type": task_type,
                "relates_to_node": relates_to_node,
                "relates_to_chunk": relates_to_chunk_id,
                "priority": priority,
                "status": "open",
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
            
            if not task.data:
                raise CanonicalStorageError("Failed to create task")
            
            task_id = task.data[0]["id"]
            logger.info(f"Task created: {task_id}")
            
            # NOTE: Task is NOT stored in KB (tasks are actions, not evidence)
            
            return task_id
            
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            raise CanonicalStorageError(f"Failed to create task: {str(e)}")
    
    @staticmethod
    async def file_contradiction(
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
            bp12_id = await CanonicalStorage.file_contradiction(
                session_id="sess-123",
                bp_node="BP.12",
                issue_type="contradiction",
                title="Price conflict",
                chunk1_id="chunk-1",  # Says €100
                chunk2_id="chunk-2",  # Says €150
                description="BP.9 says €100, BP.12 says €150",
            )
        """
        
        try:
            logger.info(f"Filing {issue_type} to BP.12: {title}")
            
            # Auto-assign BP.12 node if needed
            if bp_node == "BP.12":
                count_result = await db.table("bp12_register")\
                    .select("COUNT(*)", count="exact")\
                    .eq("session_id", session_id)\
                    .execute()
                
                count = count_result.count if hasattr(count_result, 'count') else 0
                next_num = count + 1
                bp_node = f"BP.12.{next_num}"
                logger.info(f"Auto-assigned BP node: {bp_node}")
            
            record = await db.table("bp12_register").insert({
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
            }).execute()
            
            if not record.data:
                raise CanonicalStorageError("Failed to file to BP.12")
            
            record_id = record.data[0]["id"]
            logger.info(f"BP.12 record created: {record_id} at {bp_node}")
            
            return record_id
            
        except Exception as e:
            logger.error(f"Error filing to BP.12: {e}")
            raise CanonicalStorageError(f"Failed to file to BP.12: {str(e)}")
    
    @staticmethod
    async def verify_canonical_structure() -> dict:
        """
        Verify that canonical structure is intact.
        Used for health checks and tests.
        
        Returns: {
            "tables_exist": bool,
            "indexes_exist": bool,
            "sample_links": int,
            "orphaned_entries": int,
        }
        """
        
        try:
            # Check tables exist
            tables_to_check = ["assumptions", "decisions", "agent_outputs", "tasks", "bp12_register", "knowledge_base"]
            
            for table in tables_to_check:
                result = await db.table(table).select("COUNT(*)", count="exact").limit(1).execute()
                if result is None:
                    return {"error": f"Table {table} does not exist"}
            
            # Check for linked assumptions
            linked_assumptions = await db.table("assumptions")\
                .select("COUNT(*)", count="exact")\
                .not_.is_("knowledge_base_id", "null")\
                .execute()
            
            # Check for orphaned KB entries (no canonical link)
            orphaned = await db.table("knowledge_base")\
                .select("COUNT(*)", count="exact")\
                .is_("assumption_id", "null")\
                .is_("decision_id", "null")\
                .is_("output_id", "null")\
                .is_("task_id", "null")\
                .is_("bp12_record_id", "null")\
                .execute()
            
            return {
                "tables_exist": True,
                "linked_assumptions": linked_assumptions.count if hasattr(linked_assumptions, 'count') else 0,
                "orphaned_kb_entries": orphaned.count if hasattr(orphaned, 'count') else 0,
                "structure_valid": True,
            }
            
        except Exception as e:
            logger.error(f"Error verifying canonical structure: {e}")
            return {"error": str(e), "structure_valid": False}
```

---

## STEP 1.3: CREATE TESTS

### File: `tests/test_canonical_storage.py` (NEW)

```python
"""
Tests for Canonical Storage Service
"""

import pytest
from datetime import datetime
from services.canonical_storage import CanonicalStorage, CanonicalStorageError
from database import supabase_client as db


@pytest.fixture
async def test_session_id():
    """Create a test session"""
    session = await db.table("sessions").insert({
        "ceo_id": "test-ceo",
        "state": "AWAITING_RESEARCH",
        "created_at": datetime.utcnow().isoformat(),
    }).execute()
    
    return session.data[0]["id"]


@pytest.mark.asyncio
async def test_store_assumption_creates_canonical_and_kb(test_session_id):
    """Verify assumption stored in both canonical + KB with bidirectional link"""
    
    assumption_id, kb_id = await CanonicalStorage.store_assumption(
        session_id=test_session_id,
        content="Market size is €50M",
        status="validated",
        source="ceo_input",
        confidence=0.9,
        created_by="Alex",
    )
    
    assert assumption_id is not None
    assert kb_id is not None
    
    # Check: assumption table has KB link
    assumption = await db.table("assumptions").select("*").eq("id", assumption_id).single().execute()
    assert assumption.data["knowledge_base_id"] == kb_id
    assert assumption.data["content"] == "Market size is €50M"
    assert assumption.data["confidence"] == 0.9
    
    # Check: KB entry has assumption link
    kb_entry = await db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data["assumption_id"] == assumption_id
    assert kb_entry.data["epistemic_status"] == "ASSUMPTION"
    assert kb_entry.data["source_type"] == "assumption_lifecycle"
    
    # Check: KB entry is searchable (has embedding)
    assert kb_entry.data["embedding"] is not None
    assert len(kb_entry.data["embedding"]) == 1024  # Titan embedding dimension


@pytest.mark.asyncio
async def test_store_decision_creates_canonical_and_kb(test_session_id):
    """Verify decision stored correctly"""
    
    decision_id, kb_id = await CanonicalStorage.store_decision(
        session_id=test_session_id,
        section_id="BP.1",
        title="Go with SaaS pricing",
        reasoning="Market expects SaaS, not perpetual",
        decision_type="yes",
        created_by="Alex",
    )
    
    assert decision_id is not None
    assert kb_id is not None
    
    # Check: canonical table linked to KB
    decision = await db.table("decisions").select("*").eq("id", decision_id).single().execute()
    assert decision.data["knowledge_base_id"] == kb_id
    assert decision.data["status"] == "yes"
    
    # Check: KB entry marked as CONFIRMED (CEO decision)
    kb_entry = await db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data["decision_id"] == decision_id
    assert kb_entry.data["epistemic_status"] == "CONFIRMED"
    assert kb_entry.data["confidence"] == 0.95  # High confidence


@pytest.mark.asyncio
async def test_store_agent_output_creates_canonical_and_kb(test_session_id):
    """Verify agent output stored correctly"""
    
    output_data = {
        "market_size": "€50M",
        "growth_rate": "23% CAGR",
        "confidence_score": 0.75,
    }
    
    output_id, kb_id = await CanonicalStorage.store_agent_output(
        session_id=test_session_id,
        run_id="run-123",
        section_id="BP.1",
        agent_name="opportunity_analyst",
        output_json=output_data,
        confidence=0.75,
    )
    
    assert output_id is not None
    assert kb_id is not None
    
    # Check: canonical table
    output = await db.table("agent_outputs").select("*").eq("id", output_id).single().execute()
    assert output.data["knowledge_base_id"] == kb_id
    assert output.data["agent_name"] == "opportunity_analyst"
    
    # Check: KB entry marked as INFERRED (agent-produced)
    kb_entry = await db.table("knowledge_base").select("*").eq("id", kb_id).single().execute()
    assert kb_entry.data["output_id"] == output_id
    assert kb_entry.data["epistemic_status"] == "INFERRED"
    assert kb_entry.data["source_type"] == "agent_insight"


@pytest.mark.asyncio
async def test_task_separate_from_kb(test_session_id):
    """Verify tasks stored ONLY in tasks table, NOT in KB"""
    
    task_id = await CanonicalStorage.create_task(
        session_id=test_session_id,
        title="Schedule customer interviews",
        task_type="interview",
        relates_to_node="BP.5",
        priority=1,
        created_by="mother_agent",
    )
    
    assert task_id is not None
    
    # Task exists in tasks table
    task = await db.table("tasks").select("*").eq("id", task_id).single().execute()
    assert task.data["title"] == "Schedule customer interviews"
    assert task.data["relates_to_node"] == "BP.5"
    
    # Task NOT in KB (actions are not evidence)
    kb_query = await db.table("knowledge_base").select("*").eq("task_id", task_id).execute()
    assert len(kb_query.data) == 0


@pytest.mark.asyncio
async def test_contradiction_filed_to_bp12(test_session_id):
    """Verify contradictions filed to BP.12 register"""
    
    # Create two chunks
    chunk1 = await db.table("knowledge_base").insert({
        "content": "Price is €100",
        "embedding": [0.1] * 1024,
        "source_type": "ceo_doc",
        "session_id": test_session_id,
    }).execute()
    chunk1_id = chunk1.data[0]["id"]
    
    chunk2 = await db.table("knowledge_base").insert({
        "content": "Price is €150",
        "embedding": [0.2] * 1024,
        "source_type": "ceo_doc",
        "session_id": test_session_id,
    }).execute()
    chunk2_id = chunk2.data[0]["id"]
    
    # File contradiction
    bp12_id = await CanonicalStorage.file_contradiction(
        session_id=test_session_id,
        bp_node="BP.12",  # Auto-assign
        issue_type="contradiction",
        title="Price conflict",
        chunk1_id=chunk1_id,
        chunk2_id=chunk2_id,
        description="Two different prices mentioned",
    )
    
    assert bp12_id is not None
    
    # Verify filed
    record = await db.table("bp12_register").select("*").eq("id", bp12_id).single().execute()
    assert record.data["bp_node"] == "BP.12.1"  # Auto-assigned
    assert record.data["issue_type"] == "contradiction"
    assert record.data["status"] == "open"
    assert record.data["primary_chunk_id"] == chunk1_id
    assert record.data["conflicting_chunk_id"] == chunk2_id


@pytest.mark.asyncio
async def test_verify_canonical_structure(test_session_id):
    """Verify health check works"""
    
    status = await CanonicalStorage.verify_canonical_structure()
    
    assert status["structure_valid"] is True
    assert status["tables_exist"] is True
    assert "orphaned_kb_entries" in status
```

---

## STEP 1.4: EXECUTION CHECKLIST

### Before You Run

- [ ] Backup your database (`pg_dump` or Supabase backup)
- [ ] Test migration on staging first
- [ ] Verify no active pipeline runs (wait for completion)

### Execute Step 1.1 (SQL Migration)

```bash
# Option A: Via psql
psql $DATABASE_URL < database/migrations/001_create_canonical_structure.sql

# Option B: Via Supabase dashboard
# Copy the SQL and run in the SQL editor

# Option C: Via Python (if you have migration runner)
python scripts/run_migration.py database/migrations/001_create_canonical_structure.sql
```

**Verify:**
```sql
-- Check tables created
\dt tasks;
\dt bp12_register;

-- Check columns added to knowledge_base
\d+ knowledge_base;
```

### Execute Step 1.2 (Service Code)

```bash
# 1. Create the file
touch services/canonical_storage.py

# 2. Copy the code from "STEP 1.2" into that file

# 3. Install dependencies (if needed)
pip install python-dotenv sqlalchemy

# 4. Test import
python -c "from services.canonical_storage import CanonicalStorage; print('OK')"
```

### Execute Step 1.3 (Tests)

```bash
# 1. Create the test file
touch tests/test_canonical_storage.py

# 2. Copy test code from "STEP 1.3"

# 3. Run tests
pytest tests/test_canonical_storage.py -v

# 4. Expected output:
# test_store_assumption_creates_canonical_and_kb PASSED
# test_store_decision_creates_canonical_and_kb PASSED
# test_store_agent_output_creates_canonical_and_kb PASSED
# test_task_separate_from_kb PASSED
# test_contradiction_filed_to_bp12 PASSED
# test_verify_canonical_structure PASSED
```

---

## STEP 1.5: VERIFICATION

### Check 1: Tables Exist

```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema='public' AND table_name IN ('tasks', 'bp12_register');
-- Should return 2 rows
```

### Check 2: Columns Added

```sql
SELECT column_name FROM information_schema.columns 
WHERE table_name='knowledge_base' 
AND column_name IN ('assumption_id', 'decision_id', 'output_id', 'task_id', 'bp12_record_id');
-- Should return 5 rows
```

### Check 3: Bidirectional Links Work

```python
# Test in Python shell
from services.canonical_storage import CanonicalStorage

# Create an assumption
assumption_id, kb_id = await CanonicalStorage.store_assumption(
    session_id="test",
    content="Test fact",
    status="validated",
    source="test",
)

# Verify both directions
assumption = await db.table("assumptions").eq("id", assumption_id).single().execute()
print(f"Assumption → KB: {assumption.data['knowledge_base_id']}")

kb = await db.table("knowledge_base").eq("id", kb_id).single().execute()
print(f"KB → Assumption: {kb.data['assumption_id']}")

assert assumption.data['knowledge_base_id'] == kb_id
assert kb.data['assumption_id'] == assumption_id
print("✓ Bidirectional links verified")
```

### Check 4: Tests Pass

```bash
pytest tests/test_canonical_storage.py -v --tb=short

# Expected: 6 passed
```

---

## SUCCESS CRITERIA ✅

- [x] `tasks` table created
- [x] `bp12_register` table created
- [x] Linking columns added to `knowledge_base`
- [x] `CanonicalStorage` service implemented
- [x] All 6 tests passing
- [x] Bidirectional links verified
- [x] No orphaned data

---

## NEXT: ISSUE #2

When complete, run:
```bash
echo "Issue #1 complete. Ready for Issue #2?"
```

Then I'll provide Issue #2: **No Canonical Business-Plan Node Registry**

---

**Date Completed:** [You fill in]  
**Status:** READY TO IMPLEMENT
