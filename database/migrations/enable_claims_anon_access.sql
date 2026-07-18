-- Claims table was created with RLS ON (Supabase default) and no policy, so the
-- app's anon key cannot write. This grants the same permissive dev access the
-- other system tables use. APPLY ONCE in the Supabase SQL Editor.
-- (Real per-role restriction is future work — see add_rls_policies.sql.)

ALTER TABLE claims ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all operations on claims" ON claims FOR ALL USING (true) WITH CHECK (true);

-- Alternatively, to match tables that simply disable RLS in dev:
-- ALTER TABLE claims DISABLE ROW LEVEL SECURITY;
