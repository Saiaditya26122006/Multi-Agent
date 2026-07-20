-- Migration: Add session state columns to replace Redis
-- Purpose: Move temporary session gates from Redis to Supabase
-- This migration adds columns to track temporary session state

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS awaiting_clarification BOOLEAN DEFAULT false;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS awaiting_approval BOOLEAN DEFAULT false;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS gate2_active BOOLEAN DEFAULT false;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS clarification_data JSONB;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS challenge_pending_task_id TEXT;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS adjust_pending_task_id TEXT;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS last_question TEXT;

-- Index for fast state lookups
CREATE INDEX IF NOT EXISTS idx_sessions_awaiting_clarification
  ON sessions(awaiting_clarification)
  WHERE awaiting_clarification = true;

CREATE INDEX IF NOT EXISTS idx_sessions_awaiting_approval
  ON sessions(awaiting_approval)
  WHERE awaiting_approval = true;

CREATE INDEX IF NOT EXISTS idx_sessions_gate2_active
  ON sessions(gate2_active)
  WHERE gate2_active = true;

-- Log migration
SELECT now() as migration_timestamp, 'Added session state columns' as status;
