-- ========================================
-- MIGRATION 001: Create Canonical Structure
-- ========================================
-- Run this in Supabase SQL Editor
-- This separates tasks from evidence and creates governance structures

-- Step 1: Add linking columns to existing canonical tables
ALTER TABLE assumptions ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

ALTER TABLE agent_outputs ADD COLUMN IF NOT EXISTS
  knowledge_base_id UUID REFERENCES knowledge_base(id);

-- Step 2: Create tasks table (NEW - canonical for tasks/actions)
-- Tasks are ACTION ITEMS, not evidence, so they don't go in knowledge_base
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id UUID REFERENCES pipeline_runs(id),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    created_by VARCHAR(100),  -- agent_name or 'system'
    task_type VARCHAR(50) NOT NULL,
    relates_to_node VARCHAR(50),  -- BP.X.Y.Z
    relates_to_chunk UUID REFERENCES knowledge_base(id),
    status VARCHAR(50) DEFAULT 'open',
    priority INT DEFAULT 3,
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

-- Step 3: Create bp12_register table (NEW - canonical for contradictions/gaps/governance)
-- BP.12 is the governance node - handles contradictions, gaps, escalations
-- Note: Making session_id optional since foreign key might fail if sessions table structure differs
CREATE TABLE IF NOT EXISTS bp12_register (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID,  -- Made optional to avoid FK constraint issues
    run_id UUID,      -- Made optional for flexibility
    bp_node VARCHAR(50) NOT NULL,  -- BP.12.X.Y (where it's filed)
    issue_type VARCHAR(50) NOT NULL,  -- contradiction, gap, escalation, error
    title VARCHAR(255) NOT NULL,
    description TEXT,
    primary_chunk_id UUID,  -- Made optional
    conflicting_chunk_id UUID,  -- Made optional
    impact_assessment TEXT,
    suggested_resolution TEXT,
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

-- Optional: Add foreign key constraints if tables exist
-- Run these separately if you get errors
-- ALTER TABLE bp12_register ADD CONSTRAINT fk_bp12_session
--     FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE;
-- ALTER TABLE bp12_register ADD CONSTRAINT fk_bp12_run
--     FOREIGN KEY (run_id) REFERENCES pipeline_runs(id);
-- ALTER TABLE bp12_register ADD CONSTRAINT fk_bp12_primary_chunk
--     FOREIGN KEY (primary_chunk_id) REFERENCES knowledge_base(id);
-- ALTER TABLE bp12_register ADD CONSTRAINT fk_bp12_conflicting_chunk
--     FOREIGN KEY (conflicting_chunk_id) REFERENCES knowledge_base(id);

-- Step 4: Add linking columns to knowledge_base
-- These link KB back to canonical tables (reverse direction)
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

-- Step 5: Create indexes for backward linking (KB → canonical)
CREATE INDEX IF NOT EXISTS idx_kb_assumption ON knowledge_base(assumption_id);
CREATE INDEX IF NOT EXISTS idx_kb_decision ON knowledge_base(decision_id);
CREATE INDEX IF NOT EXISTS idx_kb_output ON knowledge_base(output_id);
CREATE INDEX IF NOT EXISTS idx_kb_task ON knowledge_base(task_id);
CREATE INDEX IF NOT EXISTS idx_kb_bp12 ON knowledge_base(bp12_record_id);

-- VERIFICATION QUERIES (run these to confirm migration worked)
--
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema='public' AND table_name IN ('tasks', 'bp12_register');
-- Expected: 2 rows (tasks, bp12_register)
--
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name='knowledge_base'
-- AND column_name IN ('assumption_id', 'decision_id', 'output_id', 'task_id', 'bp12_record_id');
-- Expected: 5 rows (all columns should exist)
