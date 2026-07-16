# Evidence Accumulation — What Was Actually Built

**Date:** 2026-07-16  
**Context:** Alex reviewed an earlier proposal document (EpistemicOS_Evidence_Accumulation_Approach.docx) and raised four concerns. This document answers each one against the system that was actually implemented — the proposal was rejected and a simpler architecture was built instead.

---

## Alex's Concern #1: "It assumes one best node, but many facts are multi-node"

### Status: **Fixed (this commit)**

**What was already in place:**
- `memory_index.py` already creates cross-chunk relationships (`confirms`, `contradicts`, `updates`, `depends_on`, `related`) automatically on every stored fact
- These relationships span nodes — a fact in BP.5.1.2 can have a `confirms` relationship to a chunk in BP.9.3

**What was missing (and is now built):**

After a fact is stored and its relationships are computed, the new `services/multi_node_linker.py` runs automatically and writes:

| Field | What it does |
|-------|--------------|
| `primary_node_id` | Where the fact is stored (already existed as `node_id`) |
| `secondary_node_ids` | Other nodes this fact informs — derived from cross-node relationships |
| `secondary_relationships` | Why each secondary node is linked (relationship_type + confidence) |
| `evidence_use_boundary` | Can agents in other nodes cite this fact? |
| `cross_node_count` | How many other nodes this fact reaches |

### Evidence Use Boundary Rules

| Boundary | When assigned | Meaning |
|----------|--------------|---------|
| `cite_freely` | CONFIRMED facts/metrics/decisions, or INFERRED with 2+ secondary nodes | Any agent working on any related node can use this |
| `cite_with_caveat` | Assumptions, single-node crosslinks, or unvalidated claims | Can reference but must note it's from a different node |
| `primary_only` | Contradictions, MISSING status, isolated facts | Only the primary node's agent should use this |

### How it works in practice

Alex submits: *"We have 3 confirmed paying pilots at €8000 each — IESE, ESADE, and IE Business School."*

1. Stored under `BP.6.2` (Customer Discovery — pilot validation)
2. `link_new_chunk()` finds related chunks in:
   - BP.9 (revenue/pricing) — because it mentions €8000
   - BP.10 (validation/traction) — because it mentions confirmed paying
   - BP.5 (buyer/procurement) — because it names institutional buyers
3. `multi_node_linker` writes: `secondary_node_ids: ["BP.9", "BP.10", "BP.5"]`, `evidence_use_boundary: "cite_freely"`
4. Now when the Financial Modelling agent queries BP.9, it can retrieve this chunk because `secondary_node_ids` includes BP.9

### What about `affected_assumptions` and `created_tasks`?

These are tracked separately:
- **Affected assumptions:** The post-store hooks already check for ASSUMPTION-status chunks that conflict with the new CONFIRMED fact. When found, they're written to `negative_knowledge` (killed) and the assumption_tracker updates the lifecycle.
- **Created tasks:** Task creation is a pipeline function (Mother Agent's domain), not a storage-time function. The Feed workspace stores evidence; the pipeline runs decisions.

---

## Alex's Concern #2: "It does not distinguish evidence from task/action"

### Status: **Already solved — has been working since Phase 2**

The classifier runs **content type detection FIRST, placement SECOND**. This is `classify_content_type()` in `feed_handler.py` (line 75):

| Input is classified as | Epistemic status assigned | Example |
|------------------------|--------------------------|---------|
| `decision` | CONFIRMED | "We decided to go with per-seat pricing" |
| `risk` | INFERRED | "The biggest risk is adoption speed" |
| `metric` | CONFIRMED | "Revenue target: €500k ARR by Q2" |
| `constraint` | CONFIRMED | "Budget is capped at €120k" |
| `task` | INFERRED | "Need to schedule pilot kickoff by Friday" |
| `open_question` | MISSING | "Still unclear whether deans have budget authority" |
| `assumption` | ASSUMPTION | "I assume procurement takes 3 months" |
| `fact` | CONFIRMED | "Research shows 73% of universities use manual processes" |

The content_type is stored in chunk metadata. The epistemic_status is stored as a first-class field on the chunk. Both are set BEFORE node placement runs.

**The classifier does NOT treat everything as a "fact."** The name `split_into_atomic_facts` is misleading — it splits into atomic claims, each of which gets typed independently.

---

## Alex's Concern #3: "One score mixes different things"

### Status: **Does not apply — we didn't build the mixed score**

The original proposal combined four evidence scores into one `final_confidence` number. That proposal was rejected. What was built instead uses **separate, independent signals that are never combined into a single number:**

| Signal | What it answers | Where stored |
|--------|----------------|--------------|
| `classifier_confidence` ("high"/"medium"/"low") | Does it belong in this node? | `metadata.classifier_confidence` |
| `classifier_validated` (true/false) | Did a second LLM pass confirm placement? | `metadata.classifier_validated` |
| `epistemic_status` (CONFIRMED/ASSUMPTION/etc.) | Is it reliable? | `epistemic_status` column |
| `content_type` (decision/risk/metric/etc.) | What kind of thing is it? | `metadata.content_type` |
| `evidence_use_boundary` | Should it be used automatically across nodes? | `metadata.evidence_use_boundary` |
| `tier_decision` (auto_file/flagged/soft_ask/ask) | What governance action to take? | `metadata.tier_decision` |

Alex's requested separation:

> "Does it belong here?" → `classifier_confidence` + `classifier_validated`  
> "Is it true / reliable?" → `epistemic_status`  
> "Can it support a BP claim?" → `evidence_use_boundary`  
> "Should it be used automatically?" → `tier_decision`  

These are four separate fields. They're never averaged, weighted, or combined.

---

## Alex's Concern #4: "Weighted scoring is unsafe for prohibited claims"

### Status: **Already solved — hard blocker, not a penalty**

`_check_prohibition_violation()` (feed_handler.py line 836) is a **hard gate** that runs on every classification. When a fact triggers a node's `prohibited_claims_inference_patterns`:

```python
if violation_detected:
    result["node_id"] = None
    result["confidence"] = "low"
    result["none_fit"] = True
    result["reasoning"] = f"REJECTED: {violation_reason}"
```

The classification is **rejected outright**. The fact is NOT stored under that node. It routes to the full human review flow (`ask` tier).

Additionally, the LLM validation pass (`validate_classification()`) checks both prohibition violations AND required_output mismatch as independent hard gates. A fact that trips both gets `none_fit = True` — cannot be stored anywhere without Alex explicitly choosing a node.

### Specific hard-blocked patterns

| Pattern | Effect |
|---------|--------|
| Unsupported projection | Rejected — cannot store as CONFIRMED |
| Unverified WTP claim | Rejected from BP.5 (buyer node) if no source |
| Procurement claim without buyer source | Rejected from BP.5 nodes with procurement prohibition |
| Contradiction with approved evidence | Post-store hook auto-files to `negative_knowledge` |
| No traceable source | Stored with epistemic_status = INFERRED (never CONFIRMED) |
| Founder interpretation presented as fact | Content type = `assumption`, status = ASSUMPTION |

These are not score penalties. They're structural enforcement.

---

## Summary: Proposal vs. What's Built

| Proposal (rejected) | System (built) |
|---------------------|----------------|
| 4-score weighted formula | Independent signals, never combined |
| Evidence Accumulation service | Confidence tier routing (4 tiers) |
| Penalty-based prohibition | Hard gate prohibition (reject outright) |
| Single-node placement | Multi-node linking with evidence_use_boundary |
| All inputs = "facts" | 8 content types classified independently |
| Mixed confidence score | Mapping confidence + evidence reliability separated |

---

## Architecture Diagram (as built)

```
Alex submits text
    │
    ▼
classify_content_type()          ← "What type of item is this?"
    │ (decision/risk/metric/assumption/task/question/constraint/fact)
    ▼
classify_and_match_node()        ← "Where does it belong?"
    │ (two-stage: domain → leaf node)
    │
    ├─ _check_prohibition_violation()  ← HARD GATE (reject if violated)
    ├─ _required_output_precheck()     ← HARD GATE (reject if no overlap)
    ├─ validate_classification()       ← LLM double-check (demote or reject)
    │
    ▼
_determine_tier()                ← "What governance action?"
    │
    ├─ auto_file:         Store immediately (high + validated)
    ├─ auto_file_flagged: Store with review flag (high unvalidated / medium+alex)
    ├─ soft_ask:          Lean-yes prompt (medium from documents)
    └─ ask:               Full human review (low / none_fit)
    │
    ▼ (after storage)
_run_post_store_hooks()
    │
    ├─ Contradiction detection + resolution
    ├─ Assumption invalidation (CONFIRMED kills matching ASSUMPTION)
    ├─ link_new_chunk() → cross-chunk relationships
    └─ compute_multi_node_metadata() → secondary_node_ids + evidence_use_boundary
```

---

## Querying Cross-Node Evidence

Any agent can now find evidence that informs its node, even if stored elsewhere:

```sql
SELECT content, metadata->>'primary_node_id', metadata->>'evidence_use_boundary'
FROM knowledge_base
WHERE metadata->'secondary_node_ids' ? 'BP.9'
  AND metadata->>'evidence_use_boundary' IN ('cite_freely', 'cite_with_caveat')
ORDER BY created_at DESC;
```

This returns all facts that inform BP.9, regardless of where they're primarily stored.
