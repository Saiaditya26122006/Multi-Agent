# Canonical Tables (Alex audit issue #1)

The system stored assumptions and decisions in **two** places — operational
tables and `knowledge_base` rows — creating divergent truth. Resolution:

| Entity | CANONICAL table | Indexed representation |
|--------|-----------------|------------------------|
| Assumption | `assumptions` | `knowledge_base` rows, `source_type='assumption_lifecycle'` |
| Decision | `decisions` | `knowledge_base` rows, `source_type='decision'` |
| Contradiction / gap | `bp12_register` | (none) |
| Claim | `claims` | (none) |
| CEO facts / docs | `knowledge_base` (itself canonical) | — |

Rules:
1. Writers create/update the **canonical** row first, then store the
   `knowledge_base` representation with `canonical_table` + `canonical_id` set
   (see `add_canonical_links.sql`).
2. Readers that need authoritative state read the canonical table; retrieval/RAG
   reads `knowledge_base`.
3. `bp12_register.affected_assumption_ids` references `assumptions.id`.
4. Never edit an entity in one place only — update the canonical row; the
   representation is regenerated from it.

Status: linkage columns + decision defined here. Full backfill (populate
canonical_table/canonical_id on existing 15 assumption_lifecycle + 3 decision KB
rows, dedup) is a follow-up once every writer sets the link.
