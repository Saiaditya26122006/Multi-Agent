# BP.1.1 Pilot — Evidence Chain Demonstration

## Objective
Demonstrate end-to-end evidence traceability for BP.1.1 (Product Definition) node using the new governance architecture:

**Source Doc → Extracted Fact → Evidence Tier → Candidate Claim → Node Link → Sufficiency → Controller Decision**

---

## BP.1.1 Atomic Claims

| Claim ID | Claim Text | Evidence Tiers | Source Types |
|----------|-----------|-----------------|-------------|
| BP11_C1 | The system validates manuscript authenticity | E5, E6, E7 | first_party_data, controlled_test |
| BP11_C2 | Target market is research-active business schools | E4, E5, E6 | third_party_report, first_party_data |
| BP11_C3 | Pricing model is annual institutional subscription | E2, E3, E5 | ceo_direct, first_party_data |
| BP11_C4 | Primary competitive advantage is detection speed | E5, E6 | first_party_data, controlled_test |
| BP11_C5 | Go-to-market is direct sales + API licensing | E3, E4, E5 | ceo_direct, third_party_report |

---

## Evidence Chain Example: BP11_C1

**Claim:** The system validates manuscript authenticity

### Trace Path

1. **Source Document**
   - CEO source data or ingest document
   - Fact: "We've tested manuscript authentication on 150 samples with 97% accuracy"

2. **Extracted Fact** (knowledge_base record)
   - ID: `chunk-12345678`
   - Text: "Tested on 150 samples with 97% accuracy"
   - Source: `first_party_data` (internal test result)
   - Evidence Tier: `E6` (controlled observation → A/B test / structured test)
   - Epistemic Status: `CONFIRMED` (assertion_certainty=explicit + verification_status=verified)
   - Assertion Certainty: `explicit` ("tested", "97% accuracy")
   - Source Traceability: `present` (specific numbers provided)

3. **Evidence Link** (evidence_links record)
   - Link ID: `link-87654321`
   - Chunk ID: `chunk-12345678`
   - Target Node: `BP.1.1`
   - Candidate Claim: "The system validates manuscript authenticity"
   - Claim Supported: "Tested on 150 samples with 97% accuracy"
   - Sufficiency Status: `sufficient`
   - Requires Corroboration: `false`
   - Boundary Reason: "Controlled test result supports core diagnostic claim"
   - Controller Approved: `false` (awaiting review)

4. **Governance Item** (bp12_register, if gap exists)
   - Example: If another claim contradicts "97% accuracy"
   - Item Type: `contradiction`
   - Affected Chunks: `[chunk-12345678, chunk-87654321]`
   - Severity: `high`
   - Resolution Status: `open`
   - Controller Decision: (awaiting)

5. **Controller Decision**
   - Via API: POST /api/bp12/resolve
   - Decision: `accepted` | `killed` | `accepted_risk` | `escalated`
   - Reasoning: "E6 evidence is sufficient for initial product validation"
   - Final Status: `resolved`

---

## Current System State

### Knowledge Base
- **Total Chunks:** 1,129
- **Backfilled Metadata:**
  - `evidence_tier` (E0-E7)
  - `source_family` (7 types)
  - `assertion_certainty` (explicit/hedged/ambiguous)
  - `verification_status` (verified/unverified)
  - `source_traceability` (present/partial/missing)

### Tables Status
| Table | Records | Status |
|-------|---------|--------|
| business_plan_sections | 746 | ✅ Populated |
| knowledge_base | 1,129 | ✅ Backfilled with metadata |
| evidence_links | ? | ⏳ RLS blocking (needs fix) |
| chunk_relationships | ? | ⏳ RLS blocking (needs fix) |
| bp12_register | ? | ⏳ RLS blocking (needs fix) |

---

## Blockers & Next Steps

### BLOCKER: RLS Policy
Tables `evidence_links`, `bp12_register`, `chunk_relationships` have RLS enabled but no insert policies.

**Fix:** Execute in Supabase SQL Editor:
```sql
ALTER TABLE chunk_relationships DISABLE ROW LEVEL SECURITY;
ALTER TABLE bp12_register DISABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_links DISABLE ROW LEVEL SECURITY;
```

See: `/database/migrations/fix_rls_system_tables.sql`

### After RLS Fixed

1. **Run Background Tasks:**
   - Relationship linking (populate chunk_relationships)
   - Contradiction detection (populate bp12_register)

2. **Ingest CEO Data:**
   ```bash
   python -m services.ingestion_pipeline
   ```

3. **Link BP.1.1 Evidence:**
   ```bash
   python scripts/bp11_pilot_setup.py
   ```

4. **Verify via API:**
   ```bash
   curl http://localhost:8000/api/evidence-links/BP.1.1
   curl http://localhost:8000/api/bp12/register
   ```

---

## Files Created This Session

| File | Purpose |
|------|---------|
| `services/source_reliability.py` | E0-E7 classification, source family, traceability |
| `services/evidence_links.py` | Per-link sufficiency boundaries |
| `services/bp12_register.py` | Governance register CRUD |
| `database/migrations/fix_rls_system_tables.sql` | Fix RLS blocker |
| `database/migrations/add_evidence_links.sql` | Schema for per-link boundaries |
| `database/migrations/add_bp12_register.sql` | Schema for governance register |
| `scripts/bp11_pilot_setup.py` | Pilot evidence chain builder |
| `docs/BP11_PILOT_PLAN.md` | This document |

---

## Success Criteria

Once RLS is fixed and pilot runs:

- [ ] Evidence links created for BP.1.1 claims
- [ ] At least 5 chunks linked to claims
- [ ] Sufficiency statuses assigned (sufficient/partial/insufficient)
- [ ] Controller can view links via GET /api/evidence-links/BP.1.1
- [ ] Controller can resolve governance items via POST /api/bp12/resolve
- [ ] Full trace: Source → Fact → Tier → Link → Claim → Status → Decision

---

**Status:** 🟡 Blocked on RLS fix — ready to proceed once SQL executed
