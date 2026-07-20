-- Multi-Agent AI System Database Schema
-- PostgreSQL schema for managing CEO context, sessions, research, decisions, and business planning

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- CORE CONTEXT TABLES
-- ============================================================================

-- Table 1: CEO Context
-- Stores CEO profile, preferences, and strategic context
CREATE TABLE ceo_context (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    company TEXT NOT NULL,
    output_style TEXT NOT NULL,
    strategic_priorities TEXT NOT NULL,
    known_constraints TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Table 3: Sessions
-- Tracks conversation sessions with state management
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ceo_id UUID REFERENCES ceo_context(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'NEEDS_CLARIFICATION'
        CHECK (state IN (
            'NEEDS_CLARIFICATION',
            'AWAITING_RESEARCH',
            'RESEARCH_RUNNING',
            'AWAITING_FEEDBACK',
            'AWAITING_APPROVAL',
            'PAUSED',
            'COMPLETED'
        )),
    awaiting_research BOOLEAN DEFAULT false,
    archived_state JSONB,
    chat_id TEXT NOT NULL,
    started_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Table 2: Messages
-- Stores incoming messages
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id TEXT UNIQUE NOT NULL,
    content TEXT NOT NULL,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- BUSINESS PLANNING TABLES
-- ============================================================================

-- Table 4: Business Plan Sections
-- Tracks status and dependencies of business plan sections
CREATE TABLE business_plan_sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id TEXT UNIQUE NOT NULL,
    section_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN (
            'not_started',
            'in_progress',
            'blocked',
            'ready_for_draft',
            'approved'
        )),
    missing_inputs TEXT[],
    depends_on TEXT[],
    next_step TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Table 5: Research Briefs
-- Stores research findings and evidence
CREATE TABLE research_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    research_id TEXT UNIQUE NOT NULL,
    topic TEXT NOT NULL,
    source_type TEXT NOT NULL
        CHECK (source_type IN (
            'alex_provided',
            'manual_entry',
            'system_structured'
        )),
    key_findings TEXT[] NOT NULL,
    evidence_quality TEXT NOT NULL
        CHECK (evidence_quality IN ('low', 'medium', 'high')),
    sources JSONB,
    remaining_uncertainty TEXT,
    decision_relevance TEXT,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES business_plan_sections(section_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Table 6: Assumptions
-- Tracks assumptions with confidence levels and dependencies
CREATE TABLE assumptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assumption_id TEXT UNIQUE NOT NULL,
    statement TEXT NOT NULL,
    confidence TEXT NOT NULL
        CHECK (confidence IN ('low', 'medium', 'high')),
    based_on TEXT[],
    affects TEXT[],
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'superseded')),
    clarification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (clarification_status IN (
            'pending',
            'clarified',
            'assumed_not_clarified'
        )),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Table 7: Decisions
-- Tracks decisions with versioning and approval workflow
CREATE TABLE decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id TEXT UNIQUE NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    assumptions_used TEXT[],
    evidence_used TEXT[],
    sections_affected TEXT[],
    status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK (status IN (
            'pending_approval',
            'approved',
            'rejected',
            'superseded'
        )),
    version INTEGER NOT NULL DEFAULT 1,
    previous_version_id UUID REFERENCES decisions(id) ON DELETE SET NULL,
    changed_reason TEXT,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Table 8: Next Actions
-- Tracks action items and their assignments
CREATE TABLE next_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id TEXT UNIQUE NOT NULL,
    action TEXT NOT NULL,
    linked_section TEXT REFERENCES business_plan_sections(section_id) ON DELETE SET NULL,
    linked_decision TEXT REFERENCES decisions(decision_id) ON DELETE SET NULL,
    assigned_to TEXT NOT NULL
        CHECK (assigned_to IN ('alex', 'intern', 'both')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'done', 'blocked')),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- AGENT ACTIVITY TABLES
-- ============================================================================

-- Table 9: Agent Outputs
-- Stores agent-generated content
CREATE TABLE agent_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id TEXT NOT NULL,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    input_summary TEXT,
    output_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Table 10: Events Logs
-- Audit trail of agent actions and state changes
CREATE TABLE events_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    state_before TEXT,
    state_after TEXT,
    input_ref TEXT,
    output_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- LINKING TABLES (Many-to-Many Relationships)
-- ============================================================================

-- Links sections to their assumptions
CREATE TABLE section_assumptions (
    section_id TEXT REFERENCES business_plan_sections(section_id) ON DELETE CASCADE,
    assumption_id TEXT REFERENCES assumptions(assumption_id) ON DELETE CASCADE,
    PRIMARY KEY (section_id, assumption_id)
);

-- Links sections to their decisions
CREATE TABLE section_decisions (
    section_id TEXT REFERENCES business_plan_sections(section_id) ON DELETE CASCADE,
    decision_id TEXT REFERENCES decisions(decision_id) ON DELETE CASCADE,
    PRIMARY KEY (section_id, decision_id)
);

-- Links sections to their research
CREATE TABLE section_research (
    section_id TEXT REFERENCES business_plan_sections(section_id) ON DELETE CASCADE,
    research_id TEXT REFERENCES research_briefs(research_id) ON DELETE CASCADE,
    PRIMARY KEY (section_id, research_id)
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Indexes for frequently queried fields
CREATE INDEX idx_messages_session_id ON messages(session_id);
CREATE INDEX idx_messages_message_id ON messages(message_id);
CREATE INDEX idx_sessions_ceo_id ON sessions(ceo_id);
CREATE INDEX idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX idx_sessions_state ON sessions(state);
CREATE INDEX idx_research_briefs_session_id ON research_briefs(session_id);
CREATE INDEX idx_research_briefs_section_id ON research_briefs(section_id);
CREATE INDEX idx_assumptions_session_id ON assumptions(session_id);
CREATE INDEX idx_assumptions_status ON assumptions(status);
CREATE INDEX idx_decisions_session_id ON decisions(session_id);
CREATE INDEX idx_decisions_status ON decisions(status);
CREATE INDEX idx_next_actions_status ON next_actions(status);
CREATE INDEX idx_next_actions_assigned_to ON next_actions(assigned_to);
CREATE INDEX idx_agent_outputs_session_id ON agent_outputs(session_id);
CREATE INDEX idx_agent_outputs_agent_id ON agent_outputs(agent_id);
CREATE INDEX idx_events_logs_session_id ON events_logs(session_id);
CREATE INDEX idx_events_logs_agent_id ON events_logs(agent_id);
CREATE INDEX idx_events_logs_created_at ON events_logs(created_at);

-- ============================================================================
-- TRIGGERS FOR AUTOMATIC TIMESTAMP UPDATES
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to tables with updated_at
CREATE TRIGGER update_ceo_context_updated_at
    BEFORE UPDATE ON ceo_context
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sessions_updated_at
    BEFORE UPDATE ON sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_business_plan_sections_updated_at
    BEFORE UPDATE ON business_plan_sections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_assumptions_updated_at
    BEFORE UPDATE ON assumptions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_next_actions_updated_at
    BEFORE UPDATE ON next_actions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
