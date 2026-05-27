-- Disable Row Level Security for Multi-Agent AI System Tables
-- These tables are for internal system use with the anon key

-- Note: In production, you should use proper RLS policies instead of disabling RLS
-- For development/testing purposes, we disable RLS to allow anon key access

ALTER TABLE ceo_context DISABLE ROW LEVEL SECURITY;
ALTER TABLE sessions DISABLE ROW LEVEL SECURITY;
ALTER TABLE messages DISABLE ROW LEVEL SECURITY;
ALTER TABLE business_plan_sections DISABLE ROW LEVEL SECURITY;
ALTER TABLE research_briefs DISABLE ROW LEVEL SECURITY;
ALTER TABLE assumptions DISABLE ROW LEVEL SECURITY;
ALTER TABLE decisions DISABLE ROW LEVEL SECURITY;
ALTER TABLE next_actions DISABLE ROW LEVEL SECURITY;
ALTER TABLE agent_outputs DISABLE ROW LEVEL SECURITY;
ALTER TABLE events_logs DISABLE ROW LEVEL SECURITY;
ALTER TABLE section_assumptions DISABLE ROW LEVEL SECURITY;
ALTER TABLE section_decisions DISABLE ROW LEVEL SECURITY;
ALTER TABLE section_research DISABLE ROW LEVEL SECURITY;

-- Alternative: Enable RLS with permissive policies (uncomment if you prefer this approach)
-- This allows all operations for authenticated and anon users

/*
-- Enable RLS on all tables
ALTER TABLE ceo_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_plan_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_briefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE assumptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE next_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_outputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE events_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE section_assumptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE section_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE section_research ENABLE ROW LEVEL SECURITY;

-- Create permissive policies for all tables
CREATE POLICY "Allow all operations on ceo_context" ON ceo_context FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on sessions" ON sessions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on messages" ON messages FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on business_plan_sections" ON business_plan_sections FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on research_briefs" ON research_briefs FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on assumptions" ON assumptions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on decisions" ON decisions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on next_actions" ON next_actions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on agent_outputs" ON agent_outputs FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on events_logs" ON events_logs FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on section_assumptions" ON section_assumptions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on section_decisions" ON section_decisions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on section_research" ON section_research FOR ALL USING (true) WITH CHECK (true);
*/
