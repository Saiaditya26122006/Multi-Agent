-- Migration: RLS policies + access-role model (Alex audit Priority 4)
-- Created: 2026-07-18
--
-- ⚠️ DO NOT ENABLE BLINDLY. Per Alex: "Enable RLS only after policies are tested."
-- The ENABLE statements at the bottom are COMMENTED OUT on purpose.
--
-- PREREQUISITE (blocking): the app currently talks to Supabase with ONE anon key,
-- so Postgres cannot tell an "agent" write from a "controller" write — RLS keyed
-- to roles is meaningless until each actor has its own credential. Before enabling:
--   1. Create Supabase roles / service accounts: `controller` (Alex), `agent`
--      (pipeline), `readonly` (dashboards).
--   2. Give each actor its own key; stop sharing the anon key.
--   3. Apply + TEST these policies in a staging project, then enable per-table.
--
-- Access-role model (mirrors .claude/rules/agent-patterns.md write matrix):
--   controller  — full write incl. approvals (business_plan_sections.status='approved',
--                 bp12_register.controller_decision, claims.approved_version)
--   agent       — write facts/links/events; MAY NOT approve or set controller fields
--   readonly    — SELECT only
--
-- APPLY ONCE via the Supabase SQL Editor after the prerequisite is met.

-- Example policy set for the governance tables. Extend per table as needed.

-- knowledge_base: agents + controller write; everyone reads.
-- ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY kb_read   ON knowledge_base FOR SELECT USING (true);
-- CREATE POLICY kb_write  ON knowledge_base FOR INSERT WITH CHECK (auth.role() IN ('agent','controller'));
-- CREATE POLICY kb_update ON knowledge_base FOR UPDATE USING (auth.role() IN ('agent','controller'));

-- bp12_register: agents may create items; ONLY controller may resolve/decide.
-- ALTER TABLE bp12_register ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY bp12_read   ON bp12_register FOR SELECT USING (true);
-- CREATE POLICY bp12_create ON bp12_register FOR INSERT WITH CHECK (auth.role() IN ('agent','controller'));
-- CREATE POLICY bp12_resolve ON bp12_register FOR UPDATE USING (auth.role() = 'controller');

-- business_plan_sections: ONLY controller may write (esp. status='approved').
-- ALTER TABLE business_plan_sections ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY bps_read  ON business_plan_sections FOR SELECT USING (true);
-- CREATE POLICY bps_write ON business_plan_sections FOR ALL USING (auth.role() = 'controller') WITH CHECK (auth.role() = 'controller');

-- claims: agents create/assess; ONLY controller sets approved_version.
-- ALTER TABLE claims ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY claims_read   ON claims FOR SELECT USING (true);
-- CREATE POLICY claims_write  ON claims FOR INSERT WITH CHECK (auth.role() IN ('agent','controller'));
-- CREATE POLICY claims_update ON claims FOR UPDATE USING (auth.role() IN ('agent','controller'));

-- evidence_links: agents write; controller approves (controller_approved).
-- ALTER TABLE evidence_links ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY el_read  ON evidence_links FOR SELECT USING (true);
-- CREATE POLICY el_write ON evidence_links FOR ALL USING (auth.role() IN ('agent','controller')) WITH CHECK (auth.role() IN ('agent','controller'));

-- Leave RLS DISABLED until the prerequisite role separation is in place and the
-- policies above are tested in staging. Enabling with only the anon key will
-- block all writes and take the app down.
