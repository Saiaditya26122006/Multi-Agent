# ISSUE #1: TWO OVERLAPPING SYSTEMS — IMPLEMENTATION COMPLETE

**Status:** Ready for testing  
**Date:** 2026-07-19  
**What:** Separated canonical tables from knowledge_base (KB as indexed view)

---

## WHAT WAS IMPLEMENTED

### ✅ Files Created:

1. **`services/canonical_storage.py`** (NEW)
   - `CanonicalStorage` class with 6 methods
   - Handles bidirectional linking between canonical tables and KB
   - Methods:
     - `store_assumption()` → canonical + KB
     - `store_decision()` → canonical + KB
     - `store_agent_output()` → canonical + KB
     - `create_task()` → tasks table ONLY (not KB)
     - `file_contradiction()` → BP.12 register
     - `verify_canonical_structure()` → health check

2. **`tests/test_canonical_storage.py`** (NEW)
   - 11 pytest test cases
   - Tests all methods and their bidirectional linking
   - Tests separation of tasks from KB

3. **`database/migrations/001_create_canonical_structure.sql`** (NEW)
   - SQL migration to run in Supabase
   - Creates `tasks` table
   - Creates `bp12_register` table
   - Adds linking columns to canonical tables and KB

---

## WHAT YOU NEED TO DO

### STEP 1: Run SQL Migration in Supabase

1. Open Supabase dashboard → SQL Editor
2. Copy the entire content from:
   ```
   database/migrations/001_create_canonical_structure.sql
   ```
3. Paste into SQL Editor
4. Click "Run"
5. Expected: No errors (may see "already exists" if columns already there)

**Verification:**
```sql
-- Run these to verify migration worked

-- Check tables created
SELECT table_name FROM information_schema.tables 
WHERE table_schema='public' AND table_name IN ('tasks', 'bp12_register');
-- Expected: 2 rows

-- Check columns added
SELECT column_name FROM information_schema.columns 
WHERE table_name='knowledge_base' 
AND column_name IN ('assumption_id', 'decision_id', 'output_id', 'task_id', 'bp12_record_id');
-- Expected: 5 rows
```

### STEP 2: Run Tests

```bash
cd /home/saiaditya26122006/multi-agent-system

# Run all Issue #1 tests
pytest tests/test_canonical_storage.py -v

# Expected output: 11 passed
```

### STEP 3: Verify Implementation

```bash
# Check service imports correctly
python -c "from services.canonical_storage import CanonicalStorage; print('✓ Service imported')"

# Check files created
ls -la services/canonical_storage.py
ls -la tests/test_canonical_storage.py
ls -la database/migrations/001_create_canonical_structure.sql
```

---

## HOW THE SOLUTION WORKS

### Canonical Tables (Source of Truth)

```
Entity Type          Table              Why Canonical
──────────────────────────────────────────────────────
Assumption           assumptions        CEO-provided or validated
Decision             decisions          CEO decisions
Agent Output         agent_outputs      Agent-produced results
Task                 tasks              Action items (NOT evidence)
Contradiction/Gap    bp12_register      Governance records
```

### Bidirectional Linking

```
canonical table          knowledge_base
──────────────────────────────────────

assumptions
├─ id: "assumption-123"    knowledge_base
├─ content: "..."          ├─ id: "kb-456"
├─ knowledge_base_id ──────→ assumption_id: "assumption-123"
└─ ...                     └─ ...

decisions
├─ id: "decision-789"      knowledge_base
├─ title: "..."            ├─ id: "kb-012"
├─ knowledge_base_id ──────→ decision_id: "decision-789"
└─ ...                     └─ ...
```

**Benefits:**
- Single source of truth (canonical table)
- Searchable via KB (embedding + retrieval)
- Trackable provenance (can trace KB entry back to source)
- Separated concerns (tasks ≠ evidence)

### Separation: Tasks vs Evidence

```
Tasks (NOT in KB):
├─ "Schedule customer interviews" → tasks table only
├─ "Collect pricing data" → tasks table only
└─ "Review market research" → tasks table only
  └─ These are ACTIONS, not evidence

Evidence (IN KB):
├─ "Market size is €50M" → KB + assumptions table
├─ "Decided on SaaS model" → KB + decisions table
└─ "Completed market analysis" → KB + agent_outputs table
  └─ These are FACTS, searchable via RAG
```

### Governance: BP.12 Register

```
bp12_register (NEW):
├─ Contradictions: "Price says €100 vs €150"
├─ Gaps: "Missing customer interview data"
├─ Escalations: "Agent failed 3 times"
└─ Errors: "Bedrock timeout on 4 attempts"
  └─ Auto-files to BP.12.X.Y nodes
  └─ CEO reviews and resolves
```

---

## ARCHITECTURE DIAGRAM

```
Input Data
├─ Assumption "Market size €50M"
│  ├─ Store in canonical: assumptions table ✓
│  ├─ Store in indexed: knowledge_base ✓
│  ├─ Link canonical → KB ✓
│  └─ Link KB → canonical ✓
│
├─ Decision "Use SaaS pricing"
│  ├─ Store in canonical: decisions table ✓
│  ├─ Store in indexed: knowledge_base ✓
│  ├─ Link canonical → KB ✓
│  └─ Link KB → canonical ✓
│
├─ Task "Interview 5 customers"
│  ├─ Store in canonical: tasks table only ✓
│  └─ NOT in knowledge_base (tasks are actions, not evidence) ✓
│
└─ Contradiction "Two prices mentioned"
   ├─ Store in governance: bp12_register ✓
   ├─ Reference: chunk1_id, chunk2_id ✓
   └─ Auto-assign: BP.12.1, BP.12.2, etc. ✓
```

---

## USAGE EXAMPLES

### Example 1: Store an Assumption

```python
from services.canonical_storage import CanonicalStorage
from database import supabase_client as db

# Initialize
canonical = CanonicalStorage(db)

# Store assumption
assumption_id, kb_id = await canonical.store_assumption(
    session_id="sess-123",
    content="Market size is €50M",
    status="validated",
    source="ceo_input",
    confidence=0.9,
    created_by="Alex",
)

# Result:
# - Assumption stored in canonical table
# - KB entry created (searchable)
# - Both linked bidirectionally
```

### Example 2: Create a Task

```python
# Create task (NOT searchable, only for action tracking)
task_id = await canonical.create_task(
    session_id="sess-123",
    title="Schedule customer interviews",
    task_type="interview",
    relates_to_node="BP.5",
    priority=1,
    created_by="mother_agent",
)

# Result:
# - Task stored in tasks table
# - NOT in KB (tasks are actions, not evidence)
# - Can track progress but not RAG-searchable
```

### Example 3: File a Contradiction

```python
# File contradiction
bp12_id = await canonical.file_contradiction(
    session_id="sess-123",
    bp_node="BP.12",  # Auto-assign BP.12.1, BP.12.2, etc.
    issue_type="contradiction",
    title="Price conflict",
    chunk1_id="kb-chunk-1",  # Says €100
    chunk2_id="kb-chunk-2",  # Says €150
    description="BP.9 and BP.12 have different prices",
)

# Result:
# - Contradiction filed to BP.12 register
# - Auto-assigned to BP.12.1 (or next number)
# - CEO can review and resolve
```

---

## VERIFICATION CHECKLIST

After running tests, verify:

- [ ] SQL migration ran without errors
- [ ] `tasks` table exists
- [ ] `bp12_register` table exists
- [ ] Linking columns added to canonical tables
- [ ] Linking columns added to knowledge_base
- [ ] 11 tests pass
- [ ] Service imports without errors
- [ ] Bidirectional links verified

---

## WHAT'S NEXT

Once this is complete and tests pass, **ISSUE #2** will be implemented:
- **No Canonical Business-Plan Node Registry**
- Will link all KB entries to their BP nodes
- Will populate business_plan_sections with all 746 nodes

---

## TROUBLESHOOTING

### Error: "Table already exists"
This is OK. The migration uses `IF NOT EXISTS` so it won't fail.

### Error: "Foreign key violation"
Ensure `sessions` table exists and has data before creating test data.

### Tests fail with "No module named services"
Run pytest from project root:
```bash
cd /home/saiaditya26122006/multi-agent-system
pytest tests/test_canonical_storage.py -v
```

### Tests fail with "database connection"
Ensure `.env` has valid `DATABASE_URL` for Supabase.

---

## FILES CREATED

```
/home/saiaditya26122006/multi-agent-system/
├── services/canonical_storage.py (442 lines)
├── tests/test_canonical_storage.py (311 lines)
├── database/migrations/001_create_canonical_structure.sql (98 lines)
└── ISSUE_1_README.md (this file)
```

**Total: 851 lines of code + SQL**

---

**Status:** Ready for Supabase migration + tests  
**Ready for ISSUE #2?** Reply when tests pass ✓
