# PROJECT_STATE.md

Last updated: 2026-07-30

---

## ⛔ CONTRACT — no Feed path may auto-file, and `degraded_target` must stay visible

**Read this before writing a single line of the classifier rebuild.** This is a build
requirement, not a recommendation.

**As of 2026-07-28 Feed auto-files nothing at all.** Seven retrieval mechanisms were
measured against the labelled key; best leaf recall@10 is 57.4%, so the correct node is
absent from the top ten for 43% of facts — against Alex's 95% bar. Ingestion calls
`feed_classifier_v3.propose()`, which returns a ranked shortlist and commits to nothing.
`classify()` survives only because the eval harness scores it: **do not wire it back
into ingestion.** `knowledge_base.section` is written in exactly one place,
`feed_pipeline.confirm_card()`, and only from a node a human chose.

`match_bp_architecture` returns **ALL** nodes by default (`trusted_only=FALSE`) so that
retrieval is complete. Degraded nodes stay in the shortlist and are **flagged**, never
removed — hiding one does not protect a fact, it pushes the fact toward a
wrong-but-trusted node instead.

**If auto-file is ever reintroduced, the original rule returns with it:** a fact must
never be auto-filed into a node whose `degraded_target = TRUE`, and enforcement lives in
the filing path, NOT in the RPC default. Failing to honour this reintroduces the
confidently-wrong filing that Phase 0 removed.

### Why the RPC does not filter for you

Filtering degraded rows out of the *search* does not protect the fact — it redirects it
to a different node that looks fine. Measured against the live table:

| query | complete retrieval | with degraded hidden |
|---|---|---|
| "what are our gross margin assumptions" | **BP.9.5.10** Gross Margin Assumptions — 0.8700 | BP.9.3.5 *GTM Assumptions* — 0.4519 |
| "how do we sequence hiring" | **BP.9.5.17** Hiring Sequence — 0.8118 | BP.6.6.9 *Adoption Sequencing Acceptance Check* — 0.3574 |

Both substitutes clear the 0.3 threshold, so both would be auto-filed **with apparent
confidence** — a hiring fact landing in customer-adoption sequencing. Hiding the right
answer does not produce a safe answer; it produces a wrong one that looks safe.

### The rule

```
# Review-assist (current): nothing files, so nothing needs guarding.
proposal = propose(fact, arch)          # ranked shortlist, commits to nothing
card.degraded_target = proposal.candidates[0].degraded   # flagged, shown, kept
# ... a human picks, and only then:
confirm_card(card, chosen_node_id, who)  # the sole writer of section

# If auto-file ever returns, this guard returns with it:
#   if top.degraded_target:   # 89 of 912 nodes as of 2026-07-28
#       route_to_human_review(fact, top.node_id, top.degraded_reason)
#   else:
#       auto_file(fact, top.node_id)
```

`trusted_only=TRUE` exists for callers that want the trusted subset for its own sake
(e.g. reporting which nodes are safe targets). **Never use it as the classifier guard** —
it hides the correct node rather than flagging it.

**Status: LIVE, and satisfied by construction (2026-07-28).** `feed_pipeline`
reads `bp_architecture` through `propose()` and files nothing automatically, so the
auto-file clause has no path to violate. The visibility clause IS active and is
enforced in `_apply_proposal()`: degraded candidates stay in the shortlist and set
`card.degraded_target` + `check_node_degraded`.
`services/bp_classification_handler.py` still cannot run (it imports the deleted
`web/handlers/feed_handler`) and is dead code.

---

## ⛔ WIRE-UP REQUIREMENT — `needs_review` and `degraded_target` are two independent signals

**Read before wiring the chunker to the classifier.** These must not be collapsed
into a single "needs attention" bit.

| Signal | Set by | Means | Fix |
|---|---|---|---|
| `needs_review` | `services/semantic_chunker.py` fidelity audit | **The extraction is suspect.** The fact may assert more, less, or other than the source did. | Re-read the source quote; correct or discard the fact. |
| `degraded_target` | `bp_architecture` row | **The node is incomplete.** Purpose or `required_output` is missing, placeholder, or contradicted. | Alex authors the missing field (see the repair inventory). |

They are **orthogonal**. A fact can carry both, either, or neither:

```
needs_review=F  degraded_target=F  ->  no extra check; confirm the node
needs_review=T  degraded_target=F  ->  check the FACT   (extraction problem)
needs_review=F  degraded_target=T  ->  check the NODE   (architecture problem)
needs_review=T  degraded_target=T  ->  check BOTH — two separate problems
```

Since the review-assist pivot these signals no longer ROUTE anything — every fact
goes to a human regardless — so they are pure annotations telling the reviewer
which repair is needed and by whom. `feed_pipeline` surfaces them as independent
entries in `card.checks` (`check_extraction`, `check_node_degraded`), never
merged.

**A fact must carry both signals separately through the pipeline, and the review UI
must show which reason(s) apply.** Collapsing them into one flag makes the queue
unactionable: a reviewer cannot tell whether to fix the extraction or ask Alex for
a purpose, so both get triaged wrong. It also hides the fourth row entirely —
fixing one problem would clear the flag while the other remains.

`needs_review` additionally carries a `verdict`
(`strengthened` / `weakened` / `unsupported` / `distorted`) and a `review_reason`;
`degraded_target` carries `degraded_reason` (`placeholder_purpose` /
`empty_shell` / `null_purpose` / `null_required_output` / `overwritten_purpose`).
Surface both — they tell the reviewer what to actually do.

**Status: WIRED (2026-07-28, review-assist)** — `services/feed_pipeline.py`
carries both signals separately on every card and `confirm_card()` stores both as
separate metadata fields. `_apply_proposal()` is where the fourth row is enforced:
it appends `check_extraction` and `check_node_degraded` independently, so a
distorted extraction pointing at a degraded node produces both. Verified end to end
on `ceo_data/deck.txt` (37/37 rows carried both fields) and on a real 793-word
Source-of-Truth excerpt, which is the run that first exercised
`degraded_target` on real data.

---

## ⛔ MEASUREMENT RULE — one before/after run proves nothing on this pipeline

**Read this before quoting any accuracy number, in this file or out of it.** This is a
measurement requirement, not a caveat.

Both stages of the Feed path are LLM calls with no fixed seed: `chunk_text` decides
where to split, and `propose()` decides where a fact goes. Neither is deterministic.
**Measured 2026-07-30:** the 27-card reference paste was run through the current code
with all three classification fixes DISABLED, and **7 of 27 cards (26%) landed on a
different node than the recorded baseline** — no code change involved, same input, same
day. The chunker also re-split differently, returning 26 facts where the baseline
returned 27, and re-worded claims it had worded another way ("the faculty pricing tier
is twenty thousand" came back as "the faculty subscription price is twenty thousand").

### The rule

A two-arm comparison — recorded run vs. new run — **cannot distinguish a fix from
drift**, because a quarter of the cards move on their own. Every future change to the
chunker or classifier must be measured with three arms on the same input, same session:

```
baseline   the recorded run being improved on
control    current code, fixes under test DISABLED   (process_document(
                                                        collapse_duplicates=False,
                                                        group_facts=False,
                                                        sibling_context=False))
fixed      current code, fixes ENABLED
```

A change is **fix-caused** only when `fixed` differs from `control`. When `control`
already differs from `baseline`, that card drifted and the fix is not implicated either
way. `scripts/compare_feed_27_arms.py` prints this table and the attribution; the three
`process_document` flags exist for the control arm and for nothing else.

### This retroactively weakens earlier numbers

**Every single-run figure recorded in this file before 2026-07-30 was measured two-arm
or one-arm, and carries an unquantified ±drift of this magnitude.** That includes the
accuracy, recall and per-mechanism numbers in "Feed classifier — measured accuracy,
cost, latency", "Retrieval — hybrid measured", "Operational-term enrichment" and the
LOCKED CONCLUSION section. Their *direction* is likely sound — several rest on
differences far larger than 26% of cards — but no individual percentage in them should
be quoted as precise, and none should be used to claim a small improvement. Re-measure
with three arms before relying on any of them.

Reproducibility depends on the input being recoverable: `FeedBatch.source_text` now
persists the extracted text on the batch record. Batches written before that field
existed carry only per-card quotes and spans, from which an input can be reconstructed
but only by inferring the separator characters between spans — see
`evaluation/feed_27_card_paste.txt`.

---

## Completed

### Phase 2 Core (May 2026)
- Mother Agent + 9 child agents built with SPADE messaging
- SimPy Monte Carlo simulation wired into financial modelling agent
- Pydantic schema validation on all agent handoffs
- RAG system: 12 source_types, pgvector retrieval, ingestion pipeline
- Web interface (FastAPI + WebSocket) with trace broadcasting

### Feed Handler Redesign (July 2026)
- Auto-placement with confidence tiers (no manual node picking)
- Undo and adjust commands supported
- Duplicate detection at 0.95 cosine threshold

### RAG Knowledge Base (July 2026)
- 746 BP architecture nodes ingested into RAG knowledge base
- BP node matcher fixed: uses metadata layer filter, correct source_type, threshold lowered to 0.3

### Architectural Audit Fixes (July 2026)
1. `reopen_triggers` wired in `services/dependency_checker.py` and `agents/phase2/mother_agent.py` — downstream sections flip to `awaiting_approval` when upstream is revised
2. `tools/trace_emitter.py` now persists trace events to `events_logs` table (Supabase)
3. `run_simulation()` wrapped in error handling with graceful fallback (`simulation_failed=True` flag, no propagation)
4. Final delivery gate added — explicit Alex approval ("deliver"/"cancel") before plan ships, state stored in Redis
5. Assumption kill and confirm now require explicit confirmation text before executing (two-step Redis gate)

### Storage Write Path — store() contract + persistence (2026-07-27)

Two silent-failure paths removed. Read this before writing any new caller of
`services/rag_service.py`.

**`store()` returns `StoreResult`, not `Optional[str]`.**

```python
result = store(content=..., source_type="ceo_doc", ...)
if result:            # True ONLY for STORED — a new row exists
    use(result.id)
```

| `result.outcome` | Meaning | Populated field |
|---|---|---|
| `StoreOutcome.STORED` | row written | `.id` |
| `StoreOutcome.SKIPPED_EMPTY` | content blank/whitespace — no-op, not a failure | — |
| `StoreOutcome.SKIPPED_DUPLICATE` | identical content already stored | `.duplicate_of` (the existing row's id) |

- `__bool__` is `STORED` only, so `if result:` means "a new row was written" —
  the same meaning the old `if chunk_id:` had.
- **A failed write raises `RagStoreError`.** When Supabase accepts the insert but
  returns no row (RLS rejection, swallowed constraint violation), `store()` raises
  with the content prefix, source_type, section, epistemic_status and node_id in
  the message. It no longer returns `None`. Do not catch this into a `None` — that
  recreates the bug. `agents/phase2/precision_mapper.store_mapping()` re-raises it
  explicitly past its broad `except` for this reason.
- Invalid `source_type`/`epistemic_status` still raise `ValueError`.
- Wrappers typed `-> Optional[str]` (`rag_hooks`, `conversation_store`,
  `assumption_tracker`) return `result.id` and keep their old signature.
- `SKIPPED_DUPLICATE` is actionable, not just informational:
  `conversation_store.store_correction()` supersedes the original using
  `.duplicate_of`, so a re-submitted correction cannot leave the stale chunk
  authoritative.

**`batch_store()` returns `list[StoreResult]`, index-aligned with the input.**

One entry per input chunk — `results[i]` always describes `chunks[i]`. Previously
skipped chunks were dropped entirely, so the returned list was *shorter* than the
input and positional mapping was impossible. Adds two outcomes:
`SKIPPED_INVALID_TYPE` (source_type not in `VALID_SOURCE_TYPES`) and
`FAILED` (with `.error`). Per-item failures do not raise — bulk callers need
per-item outcomes — but each is logged at `error`, and a short result set marks
the whole batch `FAILED` rather than guessing which rows landed.

**Persistence moved off the ephemeral filesystem (Railway wipes it on deploy):**

- **Non-scope queue** — `services/non_scope_router.py` no longer touches
  `ceo_data/non_scope.json`. Items are `knowledge_base` rows:
  `source_type='ceo_doc'`, `epistemic_status='MISSING'`,
  `topic_tags=['non-scope','pending-review']`,
  `metadata.non_scope={status, reason, confidence, resolution}`. Writes and reads
  both moved — no split. Item ids are now UUIDs, not `ns_0001` (that counter reset
  on every redeploy). `resolve_non_scope()` stores the mapped fact **first** and
  marks the item resolved only if that succeeded, so a failure leaves the item
  in the queue instead of dropping it with the mapping stored nowhere.
  `_store_resolved_mapping()` is the only persistence of a resolved mapping and
  therefore raises rather than logging and continuing.
- **`POST /api/knowledge-base/add`** — writes to `knowledge_base` via `store()`
  (`metadata={'topic':…, 'origin':'web_add_fact'}`), not to `ceo_data/<topic>.json`.
  `ceo_data/loader.load_all_ceo_data()` merges those rows back per topic, so the
  add and GET endpoints agree; the checked-in `ceo_data/*.json` files remain
  read-only seed data. The merge is warn-and-continue, since that loader is also
  the RAG-unavailable fallback path. `topic` is validated against
  `^[a-z][a-z0-9_]{0,63}$` (it was interpolated into a file path unvalidated).

Schema unchanged — no new columns, no new `source_type`.

### Test isolation — cleanup fixture (2026-07-27)

The suite runs against **live Supabase** (`tests/conftest.py` builds a client from
`SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS). It had no teardown, so every run
left its fixtures in the production corpus. A single day's runs deposited 86 rows —
not inert strings, but plausible-looking fake CEO facts (`"pricing is EUR 12000 per
year"`, `"CEO said: I think we should focus on Spain first"`) plus 12
`negative_knowledge` rows, the layer whose job is suppressing proposals. Fake kill
records there can suppress real ideas. Those 86 rows were deleted by explicit id.

**Interim fix — `clean_written_rows` in `tests/conftest.py`.** An autouse fixture
records every `knowledge_base` row a test writes and deletes it by id when that test
ends. It covers both write paths:

- Wraps `rag_service.store` / `batch_store`, **and** rebinds the same names in any
  module that did `from services.rag_service import store` at import time — those
  hold their own reference that a module-attribute patch does not reach.
- The `admin_db` fixture now returns a recording client, so direct
  `admin_db.table("knowledge_base").insert(...)` calls are captured too.

Two rules it must keep:

- **Only `StoreOutcome.STORED` ids are recorded.** `SKIPPED_DUPLICATE` carries
  `duplicate_of`, the id of a row that already existed and may be real corpus data —
  deleting it would destroy data the test never created.
- **Only UUID-shaped ids are deleted.** Tests that patch `_get_supabase` with a
  `MagicMock` produce fake ids; those must never reach a `DELETE`.

`CLEANUP_TABLES` is currently `("knowledge_base",)`. Add a table only if its rows
can be deleted by id with no cascade handling.

**Proper fix, not yet done: a dedicated test Supabase project.** The fixture stops
the bleeding but the suite still writes to production between setup and teardown, a
crashed or `-x`-aborted run can still leave rows behind, and a parallel run (`-n`)
can see another test's fixtures mid-flight. Isolation belongs at the connection, not
the teardown — point `SUPABASE_URL` at a test project via `.env.test` and let the
suite write freely there.

## Phase 0 — BP Architecture Load (COMPLETED 2026-07-28)

`bp_architecture` is the canonical structure table Build reads. Created by
`database/migrations/006_create_bp_architecture.sql`, loaded and verified.

**It is a container layer, not business content — never ingest these rows into
`knowledge_base` as facts.**

| Property | State |
|---|---|
| Rows | **912**, `node_id` unique PK |
| Embeddings | **904** × `vector(1024)` (Titan Embed v2), **8 intentionally NULL** |
| Degraded targets | **89** |
| Deferred self-FK on `parent_node` | satisfied at commit |
| NULL `parent_node` | 20 = 12 section roots + 8 empty shells |
| Indexes | pkey, `parent_node`, partial `trusted` (NOT degraded), HNSW on embedding |

Loaded in a **single transaction** (one PostgREST array insert). This is required, not
incidental: the FK is `DEFERRABLE INITIALLY DEFERRED` and validates at commit, so a
batched insert would fire the constraint early and reject children written before
their parents.

### Four mappings applied

| Mapping | Scope | `provenance` |
|---|---|---|
| Dedup | 64 duplicate node_ids renumbered into the `.10+` band | `dedup` |
| Reparent | 7 out of the phantom `BP.9.7` branch + 6 `BP.1.9.*` → `BP.1.3.6.*` | `reparent` |
| Tier 1 authoring | 8 parent nodes created, 24 children attached | `authored` |
| Tier 2 | 10 placeholder purposes (contradiction resolved, still thin) | `placeholder` |

Distribution: `source` 817 · `dedup` 64 · `reparent` 13 · `placeholder` 10 ·
`authored` 8. `renumbered_from` populated on 77 rows — **that column, not the `.10+`
suffix band, is the source of truth for what moved. Do not branch code on the band.**

### Degraded breakdown (89)

| reason | count | what it is |
|---|---|---|
| `null_required_output` | 62 | 61 atomic + `BP.11.7` |
| `placeholder_purpose` | 10 | Tier 2 rewrites |
| `empty_shell` | 8 | id-only rows, the 8 with NULL embeddings |
| `null_purpose` | 8 | the authored parents — structure without content |
| `overwritten_purpose` | 1 | `BP.10.2.3`, still contradicted |

Degraded rows load and embed but are **excluded from the trusted classifier target
set**; a fact routed to one is held for review, never auto-filed.

### ~35 TRUSTED nodes carry non-discriminating boilerplate content

Separate from the 89 degraded, and arguably more dangerous because nothing flags it.

**35 trusted nodes** have `purpose` = *"Governs &lt;title&gt; without validating downstream
commercial success."* and `required_output` = *"&lt;title&gt; specification and
governance."* — the same template with a different noun. Clustered in **BP.9.2 (9),
BP.9.3 (9), BP.9.6 (9), BP.9.4 (5), BP.9.5 (3)**. All in BP.9; the other 788 trusted
nodes have genuinely distinct content.

**They are NOT flagged degraded because they do have content — but the content does
not discriminate.** The embedding is built from title + purpose + required_output, so
for these nodes the title is the only signal. `Pricing Model Definition`,
`Pricing Structure`, `Pricing Assumptions` and `Packaging Strategy` are near-identical
vectors.

**Consequence: facts auto-file into these today with the classifier guessing between
near-identical nodes.** Nothing holds them for review — they pass every trusted check.
This is a known ceiling on BP.9 filing accuracy that no classifier improvement can
lift; it needs real `purpose` / `required_output` from Alex before BP.9 filing is
reliable.

**Scoring rule for classifier evaluation** — report two numbers, never one:

| bucket | what it measures |
|---|---|
| **(a) real-content nodes** | the honest classifier measure |
| **(b) boilerplate nodes** | architecture *content* quality, not the classifier |

A bad score in (b) is Alex's content gap, not a classifier bug. The draft answer key
`evaluation/classifier_answer_key_draft.csv` carries `is_boilerplate_target` and
`scoring_bucket` columns computed from live content so the split is mechanical.

The 8 `empty_shell` rows have no title, no purpose and no `required_output`, so there
is nothing to embed — their `embedding` is NULL **by design**. A forced embedding of a
bare node_id would be a garbage vector that could false-match. An embedding-coverage
check should expect 904, not 912, and the NULL set should equal exactly those 8 ids.

### The old 899 rows are retired, not deleted (2026-07-28)

`bp_architecture` was proven on read before anything was retired — the RPC reproduced
an independent brute-force ranking to four decimals, and `EXPLAIN` showed
`Index Scan using bp_architecture_embedding_idx` chosen by the planner without
`enable_seqscan=OFF`.

The 899 old rows in `knowledge_base` were then **retired by renaming their layer**:

```
metadata.layer  'bp_architecture'  ->  'bp_architecture_superseded'
metadata.superseded_by_table = 'bp_architecture'
metadata.superseded_at       = <utc timestamp>
```

Nothing was deleted — `knowledge_base` is still 1739 rows. Exact-layer lookups for
`bp_architecture` now return 0; `rag_service`'s `startswith('bp_architecture')`
exclusion still keeps them out of generic retrieval. **Reversible with one UPDATE**
restoring the layer value; the original metadata of all 899 rows was backed up before
the change.

**Why not `superseded_by`:** that column is `UUID REFERENCES knowledge_base(id)` — a
FK into the same table. The replacement content lives in `bp_architecture`, keyed by
`TEXT` node_id, so there is no valid value to store. `rag_service.retrieve()` also
excludes on `superseded_by IS NOT NULL`, not on `epistemic_status`, so setting the
status alone would have been cosmetic. The mechanism does not support a cross-table
supersede.

**Not in scope, untouched:** `metadata.layer='bp_architecture_aug'` — a separate
840-row layer read by `services/bp_aug_index.py`. `contains()` matches the layer value
exactly, not as a prefix, so it was never at risk.

### Maintenance scripts still target the superseded 899 — point at `bp_architecture` or retire

Not read paths, so they broke nothing, but they now write to or refresh retired rows
and will drift from the canonical table. Its own task, not done:

- `scripts/reembed_architecture.py`
- `scripts/fill_empty_nodes.py`
- `scripts/ingest_bp_architecture.py` (one-off, interactive `input()` prompt — most
  likely retire outright; `bp_architecture` replaces what it built)
- `scripts/populate_bp12.py`
- `scripts/reclassify_facts.py`

Also dead and pointing at the old path: `evaluation/compare_indexes.py` and
`evaluation/retrieval_recall.py`, both importing `match_bp_node` from the deleted
`web/handlers/feed_handler`.

### The loader must create 8 parents, not 7

`BP.1.3.2`, `BP.1.3.3`, `BP.1.3.4`, `BP.1.5.2`, `BP.1.5.3`, `BP.1.5.4`, `BP.9.7` — and
**`BP.1.3.6` "User Journey Architecture"**. The eighth is easy to miss because it was
approved under the BP.1.9 reparent decision rather than the authoring track. A loader
built to create 7 leaves the six `BP.1.3.6.*` children (the former `BP.1.9.1–.6`)
parentless and re-breaks the dependency walk — the exact failure this work exists to
fix.

### After Step 2, 8 nodes are parentless BY DESIGN

An orphan check run against the loaded table **will** report these. They are not a
Tier 1 miss:

```
BP.1.1.10   BP.1.1.10.1   BP.1.1.10.2
BP.3.4.10   BP.3.4.10.1   BP.3.4.10.2
BP.4.3.10.1 BP.4.3.10.2
```

These are the Tier 3a **empty shells** — rows carrying a node_id and nothing else, no
title, no purpose, no `parent_node`. They came from the same `add_tasks` paste and
await Alex's withdraw-or-author ruling. Six of the eight duplicate content that
already exists elsewhere under a different parent (e.g. `BP.1.1.10.1 Canonical Issue
Taxonomy` is live as `BP.1.7.10`), so the likely resolution is withdrawal rather than
authoring — but withdrawing ratifies the parent the paste chose, which is itself a
decision.

Blocked on: Alex's Tier 2 purpose rewrites (11 nodes whose `purpose` was overwritten
with another node's text). Everything structural is approved.

## Feed classifier — measured accuracy, cost, latency (2026-07-28)

⚠️ **Every number below is a sample measurement on a 78-fact draft key, not a
warranty.** All of them must be re-confirmed on Alex's real uploads once Feed is
live. Do not quote any of them as a settled property of the system.

Three classifier designs have been built and measured against
`evaluation/classifier_answer_key_draft.csv` plus 10 hand-written oblique
paraphrases. Sample sizes are small and are stated inline for that reason.

### Accuracy — NOT at Alex's 95% bar

| design | leaf accuracy | notes |
|---|---|---|
| v1 hierarchical (`feed_classifier.py`) | 3.7% | domain hop discards 80% of the tree on a 0.03 margin |
| v2 flat retrieval (`feed_classifier_v2.py`) | 14.8% | 4x better than v1; still capped by recall@10 = 44.4% |
| v3 section-first + sibling judge (`feed_classifier_v3.py`) | 18.5% / 31.5% | top-1 / top-2 sections, textbook facts |

**Auto-file precision (v3)** — the number Alex's 95% bar applies to:

| fact set | top-1 | top-2 |
|---|---|---|
| textbook (n=54) | 41.7% (24 auto-files) | 53.3% (30 auto-files) |
| **paraphrase (n=10)** | **66.7% (3 auto-files)** | **50.0% (4 auto-files)** |
| boundary (n=9) | 40.0% | 66.7% |

The paraphrase cells rest on 3 and 4 auto-files respectively. They are
directionally bad but **not statistically meaningful on their own** — the
textbook cells are the ones with a usable denominator, and they say the same
thing. No margin threshold in the swept range reaches better than 53.8%.

**The judge is not the bottleneck; retrieval is.** Given the correct node is in
the candidate set the judge picks it 81% (top-1) / 78% (top-2) of the time. It is
in the set only 27% / 41% of the time. The precision loss comes from the "none
fit" escape leaking: when the correct node is absent the judge still auto-files
confidently to a wrong node 25% (top-1) / 33% (top-2) of the time. Widening to
top-2 sections makes that leak worse, not better.

### The degraded gate has a blind spot

Degraded-target facts routed to review 5/5 (top-1) and 4/5 (top-2). The top-2
escape was **not** a contract breach: the judge picked a non-degraded node, so
the gate had nothing to fire on. The gate stops filing *into* a degraded node; it
does nothing to stop a fact whose true home is degraded from being auto-filed
into a trusted wrong node. Same wrong-but-trusted redirect that set the
`trusted_only = FALSE` default.

### Cost and latency — viable, and independent of the accuracy question

Measured `scripts/measure_classifier_v3_cost.py`, Sonnet on Bedrock, us-east-1.

| stage | measurement |
|---|---|
| architecture load | 7.5s, **once per process** — not per fact |
| embedding (Titan v2) | p50 0.45s per fact |
| judge, top-1 (~10 candidates) | p50 6.2s, ~2500 in / 181 out tokens, ~$0.010/fact |
| judge, top-2 (~20 candidates) | p50 7.6s, ~3900 in / 188 out tokens, ~$0.015/fact |

**Facts are independent and parallelise cleanly.** A 20-fact document:

| workers | wall clock | per fact | speedup | failures |
|---|---|---|---|---|
| 1 | 140.5s | 7.02s | 1.0x | 0 |
| 4 | 41.2s | 2.06s | 3.4x | 0 |
| 8 | **19.4s** | 0.97s | 7.2x | 0 |

Near-linear to 8 workers with **zero Bedrock throttling failures**, so the old
concurrency-3 cap does not apply to this path. A 20-fact upload is ~20s and
~$0.20-0.30. The top-2 fallback doubles the candidate set, **not** the call
count — it is still one judge call per fact.

Latency is not what blocks Feed. Accuracy is.

### Two proposed optimisations — both measured, both REVERTED

Re-run of the full eval with each in isolation and together (234 live judge
calls). Both remain in `feed_classifier_v3.classify()` as opt-in parameters,
both default OFF.

| config | judge calls | auto-files | precision (all) | paraphrase precision |
|---|---|---|---|---|
| top-1 baseline | 78 | 29 | 41.4% | 66.7% (n=3) |
| top-2 baseline | 78 | 43 | **53.5%** | **60.0%** (n=5) |
| OPT-1 only (skip judge on single-sibling sections) | 78 | 29 | 41.4% | 66.7% |
| OPT-2 only (margin gate at 0.037) | 78 | 40 | 50.0% | 50.0% |
| OPT-1 + OPT-2 | 77 | 39 | 48.7% | 50.0% |

**OPT-1 (`skip_judge_single_sibling`) does nothing.** Zero facts out of 78 landed
in a single-sibling section under top-1, so it saved zero calls and changed zero
outcomes; combined with OPT-2 it saved exactly **one call out of 78**. It is not
a speed optimisation, and it removes the judge's "none fit" refusal — the only
thing converting a wrong section into a review instead of a confident wrong
auto-file. No upside, real downside.

**OPT-2 (`margin_gate`) costs precision.** Against the top-2 baseline it drops
overall precision 53.5% → 50.0% and paraphrase precision 60.0% → 50.0%, while
saving no judge calls at all (it is one call either way — it only shortens the
prompt). Note the margin threshold 0.037 was fit on the same 78 facts it is
scored against, so 50.0% is the optimistic reading.

⚠️ **Run-to-run variance is real at this sample size.** The two baseline runs of
the identical config differ by 1-10 points per cell (top-1 textbook 41.7% vs
38.1%; top-2 paraphrase 50.0% vs 60.0%) purely from LLM nondeterminism. Treat
differences under ~10 points on these denominators as noise, not signal.

## Feed pipeline — BUILT (2026-07-28)

`services/feed_pipeline.py` — chunker → section-first classifier → sibling judge
→ route → `rag_service.store`. Facts classified in parallel (8 workers).
Async upload wired at `POST /api/feed/upload` (returns immediately with a
`run_id`, processes in a background task, broadcasts the batch over WebSocket);
`GET /api/feed/batch/{run_id}` fetches a finished batch. The architecture is
loaded once per process and cached — it needs the **service-role key**, since
the anon role sees zero rows of `bp_architecture` through RLS.

**`auto_file_enabled` defaults to FALSE.** Measured precision is 41-53%, so
every would-be auto-file is held for review with its proposed node attached and
`review_reasons=['auto_file_disabled']`. Turn it on deliberately.

`epistemic_status` is deliberately left NULL on stored facts. `needs_review` is
a claim about extraction fidelity, not about the truth status of the content — a
faithfully extracted fact can still be an assumption, and several of Alex's say
so in their own text. Epistemic classification is a separate job nothing in this
pipeline performs.

Verified end-to-end on `ceo_data/deck.txt`: 37 facts, 37 rows in
`knowledge_base`, provenance complete on all of them. Every batch is reversible
by `delete from knowledge_base where run_id = '<run_id>'`.

## Feed — real-document run and RAG round-trip (2026-07-28)

### RAG round-trip: PASS, with one gap

`scripts/verify_feed_rag_roundtrip.py`. Feed-stored facts come back through the
exact calls Build makes (`rag_mixin.rag_enrich()` — `source_types` filter,
threshold 0.4), and through a `section`-filtered query. Node assignment
(`chunk.section`), provenance (`source_document`, `source_quote`, `start_char`,
`end_char`) and both review signals all survive intact on the `Chunk`.

⚠️ **`format_chunks_for_injection()` drops all of it.** The string an agent
actually receives carries only content, epistemic status and source_type — no
node id, no provenance. Any Build code that needs the node assignment must read
`chunk.section` / `chunk.metadata` directly rather than the injected text. Since
Feed leaves `epistemic_status` NULL, Feed facts also render with an empty status
tag, indistinguishable from a CONFIRMED fact at a glance.

### Real messy document: two defects found

Input: a 793-word excerpt of `ceo_data/EpistemicOS — Structured Source-of-Truth`
(the flattened-table persona/ICP region) — real CEO data, not the test key.

**Fact density is much higher than the test set suggested.** 146-168 facts from
793 words, roughly one fact per 5 words. The earlier "20-fact document"
projection does not describe a real upload.

**DEFECT 1 — the chunker's verification pass did not batch. FIXED.** It sent all
168 facts plus the full source in a single request, which exceeded Bedrock's
default 60s read timeout; three retries failed and the whole pass errored, which
correctly (but uselessly) marked all 168 facts `needs_review` as unaudited. Now
batched at `VERIFY_BATCH_SIZE = 25` with per-batch failure isolation, so one bad
batch costs its own facts a verdict, not the document's. After the fix: **146
facts, 145 faithful, 1 genuine `distorted` flag, 0 missing spans, 129.7s.**

**DEFECT 2 — the dedup lookup is unindexed. Migration written, NOT yet run.**
`rag_service.store()` filters on `metadata->>content_hash`, which no index
covers, so every write sequentially scans the JSONB. Measured 2.84s per lookup
at 1,944 rows, and it degrades linearly as the knowledge base grows. Combined
with a serial store loop this was ~1,400s of a 1,714s run.
Run `database/migrations/008_knowledge_base_content_hash_idx.sql`.
The store loop in `feed_pipeline.process_document` is still serial and should be
parallelised the way classification already is.

### The two-signal contract was exercised for real

The first run (`deck.txt`) proved nothing about it — it produced
`needs_review=0` and `degraded_target=0`. The real-document run produced
`needs_review=168` and **`degraded_target=5` — the first time the degraded gate
has fired on real data** — and the two stayed independent through routing and
storage.

Runs stored: `feed-65dff4b93528` (deck.txt, 37 facts),
`feed-21772166b734` (real excerpt, 168 facts, all flagged unaudited by DEFECT 1
— worth re-running now that the chunker is fixed). Both reversible by `run_id`.

## ⚠️ "auto-file precision 41-53%" and "0 auto-filed" measure different layers

They are not in conflict and neither is 96.9%, which has never appeared in any
measurement.

**41.4% / 53.5%** comes from `scripts/eval_classifier_v3.py`, which calls
`classify()` directly. There is no `auto_file_enabled` at that layer. Of the 78
answer-key facts the classifier committed to 29 (top-1) / 43 (top-2), and that
percentage of those commitments matched the hand-labelled node. It is precision
over commitments, measured against a key.

**"0 auto-filed"** comes from `feed_pipeline.process_document`, which runs with
`auto_file_enabled=False` and rewrites every `auto_file` to `review` afterwards.
The classifier still committed; the pipeline overrode it. On the real 793-word
document it committed to **75 of 128 stored facts (58.6%)** — a *higher* commit
rate than on the test key, not a lower one.

There is no measurement of whether those 75 real-document commitments are
correct. That requires hand labels; see `evaluation/real_answer_key_sample.csv`.

## Feed — clean re-run on the real document (2026-07-28)

`feed-225ea932cc7d`, same 793-word Source-of-Truth excerpt, after both defect
fixes and with the store loop parallelised.

| | first run | clean re-run |
|---|---|---|
| wall clock | 1714s | **534s** (3.2x) |
| facts | 168 | 153 (25 dedup skips → 128 stored) |
| `needs_review` | 168 (verifier timed out) | **0** |
| `degraded_target` | 5 | 4 |
| facts with no locatable span | 0 | 2 |

Routing, with `auto_file_disabled` now visible as its own reason:

| review reason | n |
|---|---|
| `auto_file_disabled` (classifier committed, pipeline held it) | **75** |
| `no_fitting_node` (judge refused) | 39 |
| `judge_unsure` (parked at parent) | 10 |
| `node_degraded` | 4 |

Two further fixes made during this run:

- `_route()` no longer overwrites `target_node_id` with `BP.13` when the
  extraction signal demotes an auto-file. The reviewer's job in that case is to
  check the extraction, which requires seeing where it would have gone. The two
  demotion paths previously disagreed and this one silently discarded the
  classifier's answer.
- The demo's table printer crashed on facts whose quote could not be located
  (`start_char is None`). Those are real — 2 of 153 — and mean provenance is
  genuinely absent, not zero.

## Retrieval — hybrid measured, node enrichment inspected and NOT run (2026-07-28)

### Hybrid (BM25 + vector) — real but small

`services/hybrid_retrieval.py`, measured by `scripts/measure_hybrid_recall.py`
against the 78-fact labelled key. BM25 runs in-process over the 912 loaded rows,
so no tsvector column and no migration.

| config | bucket-a sec@1 | sec@2 | sec@5 | para sec@2 | leaf@10 |
|---|---|---|---|---|---|
| vector only | 25.9% | 40.7% | **63.0%** | 30.0% | 44.4% |
| BM25 only | 24.1% | 33.3% | 46.3% | 10.0% | 33.3% |
| hybrid RRF | **33.3%** | **50.0%** | 59.3% | **40.0%** | 42.6% |
| hybrid weighted α=0.7 | 31.5% | 44.4% | 63.0% | 30.0% | **53.7%** |

RRF gains +7.4 @1 and +9.3 @2 on bucket-a and +10 on paraphrase — and the
paraphrase side moves *with* bucket-a, so this is not test-set fitting. But it
**reverses above k=3** (worse than baseline at sec@5) and nothing anywhere
clears 75%. BM25 alone is worse than vector alone everywhere, because the nodes
are governance-worded and have no operational vocabulary to match.

### Node enrichment with operational terms — inspected, NOT run

`scripts/generate_operational_terms.py` generated terms for the 10 paraphrase
target nodes. It embeds and writes nothing by design.

**Not circular:** only 4 of 206 generated terms (1.9%) appear verbatim in their
own node's text. The generator did produce genuinely new vocabulary.

**But roughly half the sample is wrong-domain.** Given only governance text and
no product context, the generator invented vocabulary for other industries:
K-12 education (`teacher`, `student`, `grading`, `lesson planning`, `k-12`,
`ferpa`), healthcare (`clinician vs cfo`, `hipaa`), and infosec incident
response (`downtime hours`, `data records exposed`, `breach`, `outage`) for a
node about business-plan risk scoring. Adding those to a BM25 index creates
false lexical matches — it would degrade precision, not improve it.

Content hits against the actual paraphrase facts: strong for BP.7.2.3
(`no training`, `no fine-tuning`), BP.5.1.4 (`it security`), BP.5.3.3
(`committee approval`); **absent** for BP.5.1.2 (no `dean`/`researcher`),
BP.9.1.1 (no `supervisor`/`claims`), BP.4.1.3 (no `universities`/`corporate
labs`), BP.10.1.3, BP.8.1.5.

This is the same wrong-industry failure as the earlier scope-enrichment
experiment — caught this time *before* embedding, which is what the inspection
step was for. **Seven mechanisms have now been measured and none reaches the
95% bar.**

## Operational-term enrichment — the first mechanism that generalises (2026-07-28)

All 801 leaves enriched (enriching only the answer nodes would lift recall by
construction). 15,704 terms generated, 14,240 kept after curation: 1,358
circular (appear verbatim in the node's own text) and 106 foreign-domain
dropped. 17.8 terms per node. The generator saw node text plus product context
drawn from BP.1.1.x/BP.4.4.1/BP.5.1.1 — **never any test fact**, so the lift is
not leakage.

Giving the generator product context fixed the wrong-domain defect found in the
10-node sample: terms are now `corresponding author`, `desk rejection`,
`provost sign-off`, `postdoc` rather than `teacher`, `k-12`, `hipaa`, `breach`.

| config | A sec@1 | A sec@5 | A leaf@10 | **P sec@2** | P sec@5 | P leaf@10 |
|---|---|---|---|---|---|---|
| vector only | 25.9% | 63.0% | 44.4% | 30.0% | 50.0% | 40.0% |
| hybrid RRF (raw nodes) | 33.3% | 59.3% | 42.6% | 40.0% | 40.0% | 40.0% |
| hybrid RRF + terms | 33.3% | **70.4%** | 51.9% | 40.0% | 50.0% | **50.0%** |
| hybrid weighted α=0.5 + terms | **40.7%** | **70.4%** | **57.4%** | **50.0%** | 50.0% | 40.0% |
| lexical only + terms | 25.9% | 55.6% | 44.4% | 40.0% | **60.0%** | 30.0% |

**A = bucket-a (n=54), P = paraphrase (n=10).**

On bucket-a the gains are large and outside noise: sec@1 +14.8 pts (~8 facts),
sec@5 +7.4, leaf@10 +13.0 (~7 facts). **The paraphrase set moved in the same
direction rather than collapsing** — the anti-memorisation check the earlier
enrichment attempt failed.

⚠️ **The paraphrase table is n=10: one fact is 10 percentage points.** 40% vs
50% there is a one-fact difference and cannot be read as a real gap. Bucket-a
carries the statistical weight; paraphrase only establishes direction.

Notable: `lexical only + terms` doubles paraphrase sec@1 (20% → 40%) and gives
the best paraphrase sec@5 in the table (60%), while having the *worst* leaf
recall. Operational vocabulary finds the right neighbourhood on messy phrasing;
it does not discriminate within it.

**This does not reach the 95% auto-file bar.** Best leaf@10 is 57.4% — the
correct node is absent from the top ten for 43% of facts. It is a real
improvement to shortlist quality, which is what review-assist needs.

## 🔒 LOCKED CONCLUSION — auto-file is a data problem, not a retrieval problem (2026-07-29)

**Read this before proposing another retrieval idea.** Eight mechanisms have been
measured against the labelled key. None reaches Alex's 95% auto-file bar, and the
failure mode is the same every time.

### What was tried, and what it gave

Leaf recall@10 is the cleanest comparator: it depends only on retrieval, not on a
judge or a threshold. "Correct node absent from the top ten" is a hard ceiling no
downstream component can lift.

| # | mechanism | best leaf@10 | verdict |
|---|---|---|---|
| 1 | v1 hierarchical (domain → section → leaf) | — (3.7% leaf acc) | lost 40/54 facts at hop 1 |
| 2 | v2 flat retrieval over all leaves | 44.4% | 4x better than v1; the baseline |
| 3 | candidate-pool cleaning (drop boilerplate/degraded) | 44.4% | **zero effect**, identical to the decimal |
| 4 | LLM scope-line enrichment | 46.3% | +1.9; collapsed on paraphrase → memorised |
| 5 | section-first + sibling judge | 44.4% | relocated the problem, did not shrink it |
| 6 | hybrid BM25 + vector (RRF / weighted) | 53.7% | +9.3; reverses above k=3 |
| 7 | operational-term enrichment (14,240 generated terms) | 57.4% | +13.0; generalised to paraphrase |
| 8 | **rule columns in the embedding** (evidence_requirement + prohibited_claims) | **55.6%** | **+11.2; best per unit of effort — uses only text Alex wrote** |

(A ninth, margin-gated top-2 recovery, is a variant of 5 and moved precision *down*.)

**Re-derived on 2026-07-31 and added here so it is not re-derived a third time.**
Mechanism 6 was measured again on passages rather than facts, and mechanism 7 was
proposed again before this section was read. Both were already settled above. The one
new datum: **the aggregate hybrid gain hides a per-section regression on the sections
that motivated it.** On the Claim-1 passage, BP.8.1's induced section rank goes 21 → 45
under RRF and BP.8.4 goes 58 → 63; on the competitor roster BP.8.1 goes 26 → 32. Lexical
fusion moves those sections *away* while lifting the average, because the terms it needs
are absent from the corpus entirely — `manusights`, `dossier`, `scholarsreview`,
`refineink`, `typo` and `archetype` appear in **0 of 801** node texts, so BM25 scores them
zero, while `benchmark` (7 nodes), `reviewer` (23) and `competitor` (7) sit mostly on
other sections and pull toward them. Do not read the table above as "hybrid helps
everywhere".

Mechanisms 7 and 8 are the only two that generalised — both lifted the oblique
paraphrase set alongside bucket-a rather than only the written test phrasing.
`proof_burden` was tested and **rejected**: 543 of 912 nodes share the value
`descriptive`, and adding it dropped leaf@10 from 55.6% to 51.9%.

### The diagnosis

Every miss is the same shape. Facts are **instances** written in operational
register ("the dean is who actually buys, not the researcher"); nodes are
**abstract categories** written in governance register ("Define the buyer in one
bounded statement, distinguishing buyer from user and from economic
decision-maker"). Retrieval is being asked to bridge a level-of-abstraction gap,
and no similarity function closes it.

The decisive evidence is that the two mechanisms that worked both worked by
**adding node-side content**, not by changing the search. That is the lever.

### What is NOT yet measured — do not quote a number for these

- **Real-document auto-file precision.** `evaluation/real_answer_key_sample.csv`
  (40 rows) is still unlabelled, so there is no ground truth for the real facts.
  Section recall and precision on real data are **unmeasured**, not low.
- Combining mechanisms 7 and 8. They act on different channels (lexical vs
  vector) and both caches exist, so this is cheap and untried.

### Path forward — locked

1. **Review-assist ships.** Done: `propose()` + `ReviewCard` + `confirm_card()`,
   with Redis batch persistence (`services/feed_batch_store.py`) verified across a
   hard process restart.
2. **Re-embed confirmed facts into their nodes.** Each confirmation writes
   `(fact, confirmed_node_id, rank_of_confirmed)`. Once a node carries real
   fact-level text, matching becomes fact-to-fact instead of fact-to-governance,
   which removes the register gap rather than fighting it. Build it inert; it does
   nothing until confirmations accumulate.
3. **Per-node auto-file, measured.** Turn auto-file on only for nodes whose
   re-embedded precision clears 95% on held-out confirmations. Never globally, and
   never on an unmeasured node.

Auto-file is therefore a **data-accumulation** problem. The review tool is what
generates that data, which makes shipping it the prerequisite rather than a
consolation.

## VERBATIM EXTRACTION COSTS RETRIEVAL — isolated A/B (2026-07-31)

**The same three claims retrieve a different neighbourhood depending only on whether
they are verbatim or rewritten.** `propose_group` on the pricing tiers:

    rewritten (old chunker)   sections BP.9.2, BP.9.5, BP.3.2  -> files at BP.9.2.2
    verbatim  (now)           sections BP.3.2, BP.1.7, BP.2.6  -> NO MATCH

Nothing differs but the wording. `"The single-department pricing tier is eight
thousand."` carries *pricing*, *tier*, *subscription* — the vocabulary that reaches
BP.9.2. `"Eight thousand for a single department"` is what the author actually wrote and
carries none of it. The self-contained rewrites the old chunker produced were, among
other things, **query expansion**, and removing them removed that.

This is the same root cause as the passage-mode coverage regression (1 of 4 blocks
attached versus fact mode's 5 of 8) — now isolated to a controlled A/B on identical
claims rather than inferred from a document.

**It does not argue for going back.** Storing words Alex never wrote is the thing
verbatim storage exists to stop, and it is not negotiable against a retrieval score. It
does mean the retrieval gap is now wider than the locked conclusion measured, because
every number in that table was taken on *rewritten* facts. Any future retrieval work
must be measured on verbatim text or it is measuring a pipeline that no longer exists.

⚠️ **The labelled sets are rewritten-era.** `evaluation/gold_shortlists.json`,
`gold_standard.json` and the bucket-a/paraphrase facts were all written against the old
extraction. Recall numbers taken on them overstate what the current pipeline achieves.

### E3 — group membership fixed, list still unfiled

`fact_dedupe` collapsed a list member into a later ungrouped restatement of the same
claim — the restatement is the longer phrasing, so it won — and the survivor carried no
`group_id`. The list silently lost a member, which then filed alone: that is what
scattered the pricing tiers across BP.9.5.1 and BP.9.2.2. Fixed: **the survivor inherits
`group_id`/`group_label` from the fact it replaced when it has none.** The group is a
property of the claim, not of the wording that survived.

Verified: all three tiers are back in one group, classified in one call, with one shared
outcome. **E3's stated goal — all three on one node — still does not hold**, because that
one shared outcome is now "no match" for the reason above. Consistent and unfiled beats
scattered, but it is not the fix landing.

## MEASURED NEGATIVES (2026-07-31)

Two things measured and deliberately NOT built. Recorded at the same weight as the
positives: a negative result nobody wrote down gets rebuilt.

### Prohibition gate — 0 of 51 violations, not built

`prohibited_claims_inference_patterns` is advisory: it is rendered into the judge prompt
(`_describe`) and nothing checks the judge's answer against it. 834 of 912 nodes carry
one, 799 distinct, median 77 chars. The old `web/handlers/feed_handler.py` had
`_check_prohibition_violation` as a real gate; it was lost with that file.

`scripts/measure_prohibition_violations.py` replays already-recorded proposals — no
pipeline change, so it measures what shipped — and asks once per (fact, proposed node)
whether the fact asserts what that node forbids. One pair per call, reason before
verdict, biased to "false" when in doubt.

    pairs checked        : 51
    no prohibition rule  : 8
    errors               : 0
    VIOLATIONS           : 0        rate 0.0%

**Not built.** The rule-of-three upper bound at n=51 is **~5.9%** at 95% confidence, so
the rate is low but not provably zero. 0% is also consistent with the advisory text
already doing the job — the judge is shown the rule and told to respect it. A gate would
cost one Bedrock call per filing to insure against a failure that is not occurring.

⚠️ n=51, from one document. **Re-run it if Alex's real paste is prohibition-dense**;
the script takes `--limit` and needs no setup. Restoring the old regex gate is not the
option to reach for — it was four hardcoded patterns against 799 distinct rules.

### Candidate ordering — a real bug, no detectable effect on drift

`load_architecture` paginated with `.range()` over an **unordered** query. That is a
correctness bug on its own: PostgREST has no stable row order without `order by`, so
successive pages could skip or repeat rows. It also left `arch.siblings` in whatever
order the API returned — **42 of 91 sections were not in node_id order** — which
scrambles the repeating slot sequence every section shares and which the judge reads as
its candidate list.

Fixed with `.order("node_id")` plus `node_sort_key`, which sorts numerically so
`BP.8.1.2` precedes `BP.8.1.10`. **18 sections have ≥10 leaves and would still have been
scrambled by a plain lexicographic sort.** All 91 sections are now in slot order.

**The drift hypothesis did not survive contact.** Three-arm attribution after the fix:
`fix-caused=8, drift=10, unchanged=9`, against 7 drift before. Per the MEASUREMENT RULE
that split is not a card-for-card measurement, so this is not evidence that drift rose
either. **Read it as: candidate order is not a meaningful contributor to run-to-run
drift.** The fix is kept for the pagination bug, not for the drift.

## Feed classification — three fixes, measured three-arm (2026-07-30)

Three fixes to the classification path, addressing measured errors on the 27-card
reference run `feed-73eb3e7baa5b`. All numbers below are three-arm (see the MEASUREMENT
RULE above); E1–E4 reproduced identically across three `fixed` runs.

| Fix | What it does | Where |
|---|---|---|
| C — dedupe | collapses near-identical facts before classification | `services/fact_dedupe.py` |
| A — group classification | one call, one node for a list the chunker grouped | `propose_group()`, chunker `group` field |
| B — sibling context | SUBSUMED outcome + primary-claim selection | `propose(siblings=...)` |

### Fixed — verified fix-caused

| # | Error | Baseline | Control | Fixed |
|---|---|---|---|---|
| E1 | card 09 filed as its own fact though subsumed by card 10 | BP.1.1.2 | BP.1.1.3 | **SUBSUMED, not filed** |
| E2 | cards 03/13 are one claim on two nodes | 9.2.2 / 9.2.10 | 9.2.3 / 9.2.10 | **one card, BP.9.2.2** |
| E3 | pricing list 02/03/04 scattered | 9.2.10 / 9.2.2 / 9.2.10 | 9.2.2 / 9.2.3 / 9.2.3 | **all BP.9.2.2** |
| E4 | cost list 23/24/25 scattered | 9.5.1 / 9.5.12 / 9.5.1 | 9.5.2 / 9.5.12 / 9.5.3 | **all BP.9.5.2** |

The control column is why these count: each error persists with the fixes off, so the
node convergence is attributable to the fix and not to the run.

### E5 — FIXED, guard-caused (2026-07-31)

Card 20 ("the core problem is whether claims withstand epistemic scrutiny, not writing
quality") was filed at BP.1.2.1 Writing Assistance Exclusion — the subordinate clause
rather than the primary claim. It now files at **BP.1.1.3**, and the rejection guard
(`_rank_from_parsed`, see "Judge rejection guard" below) is what moved it: the judge
marked `BP.1.8.3` `fit: poor` in its own note — *"Claim extraction architecture
references the claim unit definition, so this fact could inform that specification, but
that node is about extraction method, not …"* — and the gate dropped it, leaving
BP.1.1.3. The drop is in the arm log, not inferred.

**The primary-claim rule is still unproven and is not what fixed this.** `control` had
already moved the card off BP.1.2.1 with all three earlier fixes off, and on re-chunk the
sentence splits into two facts, so the merged-contrast condition the rule exists to
handle never arises. The next merged contrast that survives chunking as one fact is the
first real test of that rule. E5's symptom is resolved by a different mechanism than the
one written for it.

### KNOWN REGRESSION — card 26

`"The company's Spanish customers pay in euros"` moves **off BP.9.5.5**, fix-caused —
the control keeps it at BP.9.5.5 in every arm run. Unjustified: the fact is a
currency-denomination rule and it lands on price/cost nodes. **The destination varies by
run** (BP.9.2.2 in one fixed arm, BP.9.5.1 in another), which is itself the
non-determinism the measurement rule is about. Sibling context is the only fix touching
this card. Every node involved is in the BP.9 content-debt set, so the "before" was not
right either — this is a wrong answer replacing a wrong answer, not a correct filing
lost. Unresolved.

Card 07 ("the renewal figure is not validated") landed on three different nodes across
baseline/control/fixed (BP.10.1.5 / BP.9.2.3 / BP.2.4.4). It is unstable independent of
these fixes and is not counted either way.

### Totals

- attribution across 27 baseline cards: **fix-caused 9, drift 7, unchanged 11**.
  Of the 9 fix-caused: 6 are the intended E1–E4 targets, 1 (card 17) moved back to its
  baseline node, 2 are the collateral above.
- **These counts are one arm pairing, not a constant.** Re-running control and fixed
  reshuffles the unstable cards between "fix-caused" and "drift" — card 17 lands in
  either bucket depending on the run. **E1–E4 are the stable core**: they came out
  fix-caused in all three fixed runs. Treat the 9/7/11 split as the shape of the result,
  not as a measurement to compare future runs against card-for-card. The committed
  `evaluation/feed_27_{rerun,control}.json` are the third fixed arm and its control.
- **classification calls 27 → 21** — grouping is a net reduction, as designed.
- **cards flagged degraded 12 → 4** (control 8, so **8 → 4 is fix-attributable**).
  Remaining 4: BP.9.5.1 ×2 `placeholder_purpose`, BP.9.5.16 and BP.9.2.12
  `null_required_output`. That is content debt needing Alex, not a classifier defect.
- **"Correctness" is not measured.** There is no answer key for this paste, so
  "N cards stayed correct" cannot be claimed. The 15/12 split quoted in earlier
  discussion is the unflagged/flagged split, not a correctness label. Building a keyed
  fixture is the prerequisite for any accuracy claim on this path.

## Judge rejection guard + comprehension-first extraction (2026-07-31)

### The bug

`propose()` returned shortlists whose top candidate carried a note arguing against its
own node, and the card surfaced as `ready`. Reproduced on HEAD twice: the depth-engine
roster paste produced **5 cards, all `ready`, all BP.1.1.9**, every note reading

> "Filing here treats the depth-engine archetype and its four tools as part of the
> diagnostic capability map, **but this node covers internal EpistemicOS capabilities,
> not external tool archetypes — a weak fit.**"

Cause is ordering, not reasoning. The candidate object was `{"node_id", "note"}` — the id
was emitted first, so the note could only ever rationalise a node already chosen. Same
class as the chunker verifier's reason-before-verdict bug.

### The fix — structural, in two places

**Prompt.** Each candidate now emits `note` → `fit` → `node_id`, in that order, so the
annotation is written before the id it justifies. `fit` is a verdict on the note just
written (`fits` / `poor`), a candidate whose note argues against its node must be marked
`poor`, and a `poor` candidate must be **deleted, not ranked lower**. All-poor returns an
empty list. `JUDGE_PROMPT` gets the single-choice equivalent: a reason that says poor,
weak or wrong forces `choice: "none"`.

**Code.** `_rank_from_parsed()` drops every `fit: poor` candidate, plus a lexical
backstop (`_note_rejects`) for when the model writes the rejection in prose and labels it
`fits` anyway. An *unrecognised* `fit` falls back to the note rather than counting as a
rejection — one stray word must not empty a good shortlist. An emptied shortlist is
`NO_PROPOSAL` → `CHECK_NO_MATCH` → bucket `nofit`; `ready` is unreachable on a rejected
node because `bucket_of` tests `not card.shortlist` first.

Prompt guidance alone would not have held. The gate is what makes the guarantee.

### Attribution evidence — four guard drops on the 27-card paste

All four are the judge's own `fit: poor` verdict, enforced by the gate. HEAD arm: zero.

| Node dropped | The note that rejected it | Effect |
|---|---|---|
| `BP.9.6.1` | "this node is about the product's own financial governance" | card 15 → BP.5.3.3 |
| `BP.1.8.3` | "that node is about extraction method, not …" | card 20 → BP.1.1.3 (**E5**) |
| `BP.1.1.6` | timing read as a decision-boundary statement | — |
| `BP.1.1.4` | "defines what the system processes, not the epistemic problem" | — |

Depth-engine roster, 3/3 fixed runs: **1 card, `nofit`**, with a reason that supports its
own verdict — *"a competitive/alternatives taxonomy claim that belongs in BP.6.2 …, which
is absent from this candidate list"*. That is a retrieval miss surfaced honestly, not a
filing at a rejected node. It is also the first statement of the BP.8.1 reachability
problem.

**The 27-card arm comparison is weak evidence for this fix and must not be quoted as
strong.** A fourth arm (HEAD, all flags on) differs from the fixed arm on 10 of 27 cards,
but measured run-to-run drift on this input is ~26% (~7 cards), and the paste contains no
rejecting-note case at all. The four logged drops are the attribution; the 27-card set is
a regression check only. E1–E5 all hold under the new code.

### Comprehension-first extraction

The chunker reads the whole passage in one call and already knows its argument structure;
it was discarding it. Every fact now carries `role`
(`claim|evidence|recommendation|assessment|definition`) and `relationships`
(`supports|supported_by|contrasts_with|about`, referencing other facts of the same
passage by `Fact.index`). Persisted on the card, in `_card_event`, and in `confirm_card`
metadata.

**Links are metadata for retrieval, never a merge.** Each linked fact is still split,
classified and filed independently at its own node.

Set-membership rule, folded in: a list of members under one predicate is ONE fact with
`role=definition`; a list of distinct values splits per value.

| Case | HEAD | Fixed |
|---|---|---|
| worked example (claim/evidence/recommendation/assessment) | 6 facts, no structure | **4 facts, roles and links exact, 3/3 runs identical** |
| depth-engine roster | **5 facts** | **1 fact, `definition`** |
| pricing tiers (Test A) | 3 facts | **3 facts, 1 group, 1 call, all BP.9.2.2** — no regression |

Degradation is one-directional: an unresolvable link is dropped, not guessed; an unknown
role becomes `None`; dedupe collapses are followed via `_redirect_collapsed_links()`. A
passage the model cannot structure yields exactly today's behaviour.

Tests: `tests/test_judge_rejection_guard.py` (17), `tests/test_chunker_structure.py`
(19). Both are deterministic and make no Bedrock call — the guarantee has to hold on
whatever the model returns, including the shapes it returns rarely.

## PASSAGE PIPELINE — store whole, attach to many (2026-07-31)

A second ingest unit. The fact pipeline is unchanged and remains the DEFAULT; both are
live so the two can be compared on the same input.

    FEED_UNIT=fact    python main.py   # default — feed_pipeline, the atomic-claim path
    FEED_UNIT=passage python main.py   # passage_pipeline

`web/server.feed_unit()` reads the flag; an unrecognised value logs and falls back to
fact.

**The default stays `fact`, and the reason changed on 2026-07-31.** The original reason —
an unproven write path — is resolved (see "The write path", below). The current reason is
**coverage**: on a four-block claim document, passage mode attached **1 of 4 blocks**
where fact mode found homes for **5 of its 8 fragments**. The passage judge's span test is
stricter, and against a candidate pool that lacks the right section it returns nothing
rather than forcing a fit. That is correct behaviour and it is still a regression in what
reaches a reviewer.

⚠️ That measurement is on a **reconstructed** document, not Alex's paste — only the two
sentences it was built around are verbatim. Flip the default only after both modes have
been run on the real document.

### Why

Asking "which single node does this claim belong to" has no answer for a real paragraph.
Alex's Claim-1 block states a differentiation claim, decides a positioning stance, and
reports a benchmark result. Filing it at one node loses two of the three, and splitting
it into three claims to avoid that is what forced the rewriting removed in VERBATIM
STORAGE below. Storing the paragraph whole and attaching it to several nodes gets both.

| | Fact pipeline | Passage pipeline |
|---|---|---|
| unit | one atomic claim | one **unit of meaning** — claim block, definition, procedure, table |
| splitter | LLM returns claim text | LLM returns **boundaries**; the code slices |
| nodes per unit | 1 | up to `MAX_ATTACHMENTS` (2) |
| justification | a note | **a span of the passage, checked as a substring** |

### The splitter — an LLM chooses boundaries, never text (2026-07-31)

Replaces the blank-line/labelled-block splitter, which could not see meaning: it cut a
nine-step protocol into nine passages because the author bulleted them, and could not
tell a table's rows from nine unrelated lines. A unit of meaning is not always marked
with a blank line, and that is the thing being divided on.

**The model returns each passage's opening 6-10 words, not its text.** The code locates
them and every passage is `text[cut_i:cut_i+1]` — a slice. Two properties follow by
construction rather than by trusting a copy:

- `source[start_char:end_char] == text` — nothing generated can reach storage.
- Boundaries are **contiguous**: passage *i* ends where *i+1* begins, so no text can be
  dropped, duplicated or reordered whatever the model returns.

Asking for openings rather than whole passages is what makes that work. A model asked to
copy a nine-bullet protocol back will eventually normalise a space; asked for its first
eight words, it will not. An opening that cannot be located, or that resolves at or
before the previous cut, is dropped with a warning — its text merges into the previous
passage. A boundary is lost; text never is.

**Measured on the three reference documents, 3 runs each:**

| document | expected | LLM splitter | structural (old) | boundaries identical ×3 | violations |
|---|---|---|---|---|---|
| four Claim blocks | 4 | 4, 4, 4 | 4 | yes | 0 |
| chain + nine bullets | 2 | 2, 2, 2 | **9** ✗ | yes | 0 |
| 2×2 table + comment | 2 | 2, 2, 2 | 2 | yes | 0 |

`span_verified` true on every passage of every run; no text lost in any run.

**`_snap_to_line_start` exists because of a measured two-character instability.** On the
first pass, doc 2 came out 2-of-3 identical: one run returned the protocol's opening as
`"Parse the manuscript…"` rather than `"- Parse the manuscript…"`, putting the boundary
on the wrong side of the bullet. A marker belongs to the item it marks, so a cut now
moves back to its line start when only marker characters (≤4, whitespace/bullets/`1.`
punctuation) precede it. Backwards only, within one line, over non-content only. All
three documents are 3/3 identical after it.

⚠️ **Labels vary run to run** — `'volume vs coverage claim'` /
`'volume vs coverage market failure claim'`. They are free-text metadata and never touch
the passage text; boundaries are what had to be stable.

⚠️ **Three runs on three short documents is evidence, not proof.** A long document has
more boundaries and more chances to disagree.

`split_passages_structural()` is kept as the fallback when the model call fails — losing
Alex's document to a Bedrock timeout is worse than splitting it bluntly — and as the
deterministic reference the table above compares against. It logs at `error` and says
the boundaries will follow blank lines rather than meaning.
`split_passages(text, use_llm=False)` forces it for comparison runs.

### An attachment must be earned by a span

The judge returns, per node, the substring of the passage that populates that node's
`required_output`. `_attachments_from` checks the span really is in the passage and drops
the attachment when it is not. That check is the whole difference between multi-node
attachment and topic spraying: a merely *related* node has nothing to quote, so it cannot
produce a span, so it cannot attach. Four refusal paths — unknown node id, `fit: poor` or
a reason that rejects its own node (the guard from `feed_classifier_v3`, imported rather
than reimplemented), a span not present, and a duplicate node.

Over the cap, qualifying attachments are dropped and counted in `overflow`, which raises
`CHECK_OVERFLOW` so the reviewer knows the passage was crowded.

### Storage

One row per attachment, each holding the WHOLE passage; the span travels in metadata and
is what `highlight()` / `retrieve_at_node()` mark. Retrieval filters on `section`, so a
row per node is what makes a passage visible at each of them.

⚠️ `rag_service.store` dedupes on a **global content hash that ignores `section`**, so the
second attachment of one passage would be swallowed as a duplicate of the first. The
natural key here is (passage, node), so `passage_pipeline._already_attached` does that
check and `store` is called with `deduplicate=False`. Any future multi-node writer must
do the same.

### Measured on the Claim-1 block, two runs

- **1 passage, 0 verbatim violations, character-identical to the input**, span verified.
- **Ranks 1-2 are reproducible; ranks 3-4 were not.** Across four runs BP.8.3.1 and
  BP.8.3.3 came out first and second every time with identical spans, while ranks 3-4
  differed in *every* run: BP.8.3.5+BP.1.2.1, then BP.1.1.3+BP.1.2.1, then
  BP.1.8.6+BP.1.1.9. Each quoted a real span, so the span gate cannot filter them — they
  are judgement calls the judge does not repeat. **BP.1.1.3, the node this passage was
  explicitly not meant to reach, leaked in through that tail.**
- **`MAX_ATTACHMENTS` is therefore 2**, not 4 — the reproducible part of the ranking.
  Everything past it is counted in `overflow` and raises `CHECK_OVERFLOW`, so a passage
  that genuinely populates more nodes is visible rather than silently truncated. Three
  runs at the cap produced identical nodes, spans and character offsets.

**The expected nodes were mostly never candidates.** Expected roughly BP.8.4.1 / BP.8.3.3
/ BP.8.1.4; got BP.8.3.1, BP.8.3.3, BP.8.3.5, BP.1.2.1 — one exact hit.

    section rank for the Claim-1 passage, of 91:
      BP.8.3    5   retrieved
      BP.8.1   21   NOT retrieved  -> BP.8.1.4 never shown to the judge
      BP.8.4   58   NOT retrieved  -> BP.8.4.1 never shown to the judge

This is the BP.8.1 vocabulary gap (see the diagnosis section), now also hitting BP.8.4:
`required_output: "Differentiation Claim Inventory specification and audit trail."` is the
title nominalised three times and matches no natural language. `DEFAULT_SECTIONS` is
already 6 here against the fact path's 3; BP.8.4 at rank 58 would need ~58.

**The attachment mechanism is not the bottleneck — the candidate pool is.** Given BP.8.3
the judge found three distinct earning spans in one paragraph and quoted each correctly.

⚠️ **Multi-node attachment raises the cost of the retrieval gap.** With one node per fact
a retrieval miss lost one filing. A passage that should populate several nodes but only
sees two sections loses attachments silently. **Hybrid retrieval is NOT the answer** —
see the re-derivation note under the LOCKED CONCLUSION: it moves BP.8.1 and BP.8.4
further away, not closer.

### The write path — verified end to end (2026-07-31)

Two supervised writes to canonical Supabase, both confirmed by querying the table rather
than by trusting the return value.

**Direct (`confirm_passage`, 4 nodes, run `passage-supervised-verify-01`):**

    row count            : 4  (expected 4)
    distinct sections    : ['BP.1.1.3', 'BP.1.2.1', 'BP.8.3.1', 'BP.8.3.3']
    distinct passage text: 1     all text == source : True     NO COLLAPSE : True

All four rows carry identical content (`sha1 17037f166941`, 319 chars) and different
`attachment_span` metadata. The `deduplicate=False` + `_already_attached` scoping is what
makes that work; `store()`'s global content hash would have written one row and dropped
three. `retrieve_at_node` returns the whole passage with the span marked and
`also_attached_to` naming the siblings.

**Through the endpoint (`POST /api/feed/confirm`, `node_ids` of 2, run
`passage-endpoint-verify-02`):**

    200  unit=passage  stored=2  status=confirmed
    BP.5.4.2  len=302  identical_to_source=True
    BP.5.4.3  len=302  identical_to_source=True
    row count 2 · distinct sections 2 · distinct text bodies 1 · PASS

`node_ids` with more than one entry against a **fact** batch returns 400.

**Re-confirming a passage is idempotent.** The first endpoint attempt ran against a
passage the direct write had already stored: it returned `stored: 2` having written
**zero** new rows, because `_already_attached` found the existing pair and returned their
ids. Correct behaviour, with one wrinkle worth knowing — `stored_ids` mixes newly written
and pre-existing ids and the response cannot distinguish them.

### The confirm path is passage-aware (2026-07-31)

- `ConfirmCardRequest` takes `node_ids: list[str]`; `node_id` still works and
  `targets()` normalises both. A single `node_id` could not express multi-node
  attachment, which is the entire point of the pipeline.
- The endpoint **dispatches on the batch's own `unit`, not on `feed_unit()`**. A batch
  ingested as passages must confirm as passages even if the process was since restarted
  under the other flag — the cards in Redis outlive the environment variable. Older
  batches without the field are sniffed by the presence of `attachments`.
- `feed_results.html` handles `split`/`passage`/`attached` alongside
  `extracted`/`chunked`/`classified`, and `passageView()` maps a passage card into the
  fact card's field names so filters and counts stay one code path. Each attachment
  renders with its reason and the passage with the earning span `<mark>`ed.

### PARENT FALLBACK — the routing rule that was never on the live path (2026-07-31)

Routing has been stated as **leaf → section → review** since v3. It was not implemented
where it mattered. `propose()` built its candidate pool as

    for section in chosen:
        candidate_ids += arch.siblings.get(section.section_id, [])

and `arch.siblings` maps a section to its **leaves**. The section node itself was never
in the list, so a fact belonging to a section but to no single leaf under it had no legal
target and fell straight through to "no candidate matched". The rule existed in
`classify()` as `PARENT_PARKED` — a path explicitly not used for filing — and nowhere on
the path that files.

`candidate_pool()` and `is_section()` now live in `feed_classifier_v3` and are shared by
`propose()`, `propose_group()` and `passage_classifier.attach()`; the passage-local
duplicate is deleted. Parents are labelled in the rendered candidate list, both rank
prompts say to rank a parent only when no leaf covers the content, and `ProposedNode`
carries `level: "leaf" | "section"` so a section-level filing is distinguishable
downstream from a leaf filing.

Verified firing in fact mode — 3 of 8 cards on the test document now carry a section
fallback that could not previously be offered:

    [02] BP.2.1.1 (leaf)   shortlist: [BP.2.1.1 leaf, BP.2.1.5 leaf, BP.2.1 section]
    [05] BP.1.1.9 (leaf)   shortlist: [BP.1.1.9 leaf, BP.1.8.4 leaf, BP.1.8 section]
    [08] BP.1.2.1 (leaf)   shortlist: [BP.1.2.1 leaf, BP.1.2 section, BP.1.5.3.1 leaf]

⚠️ **It does not rescue a fact whose section was never retrieved, and that is the
common case.** On the passage run all six retrieved sections were in the pool (68
candidates shown, `dropped: []`, so nothing was filtered by the gates) and the judge
declined every one — because BP.8.1 and BP.8.4 were not among them. The parent fallback
fixes "the right section was shown but only its leaves were selectable". It cannot reach
a section sitting at induced rank 21 or 58. The BP.8 retrieval gap now blocks both
pipelines rather than one.

### Still not verified

Ingest, confirm and retrieval are covered. The review queue (`/feed/review`) and the
batch-list view were not exercised in passage mode. Neither mode has been run on Alex's
actual multi-block document — every passage measurement to date is on the Claim-1 block
or on a reconstruction.

## VERBATIM STORAGE — the chunker no longer writes prose (2026-07-31)

**CONTRACT: a stored fact is an exact substring of its source document.**

    source[fact.start_char:fact.end_char] == fact.fact

Alex wrote *"We assess the argument, not the apparatus."* The pipeline stored
*"EpistemicOS assesses the argument, not the apparatus"* — a sentence he never wrote, in
a system whose premise is claim fidelity. The chunker had been resolving pronouns,
supplying subjects and rephrasing to make each fact self-contained. That is now
forbidden outright.

### Split, don't rewrite

The model chooses where the cuts fall and nothing else. It returns the substring it
picked; `chunk_text` locates it and takes `text[start:end]` as the fact, so the invariant
is a property of the code rather than a promise the prompt makes. A model that
paraphrases produces an unlocatable segment, which is flagged, not stored.

Three gates, in order:

| Gate | Where | Does |
|---|---|---|
| `audit_spans()` | `semantic_chunker` | verdict VERBATIM or UNLOCATED; never rewrites or silently drops |
| `verify_card_spans()` | `feed_pipeline` | asserts the invariant on the objects that reach storage; sets `span_verified` |
| `confirm_card()` | `feed_pipeline` | **raises** on `span_verified == False` — the write path cannot emit non-verbatim text |

### Comprehension is separated from storage

Verbatim segments are not self-contained, by design. `propose(passage=...)` and
`propose_group(passage=...)` carry ±`PASSAGE_WINDOW` (600) chars of source; the judge
resolves "it"/"we"/"this" internally, files on that understanding, and leaves the text
alone. Retrieval still embeds the segment alone.

    stored   'It replaced the per-seat model.'      (verbatim, unresolved)
    node     BP.9.2.1
    note     "a change from per-seat to a new model is precisely the kind of
              content that belongs in pricing..."

On the 27-card paste, **8 of 25 cards begin with an unresolved reference and route
normally** (16 ready / 7 look / 2 nofit). `role` and `relationships` are what make those
cards usable later: the span, the neighbours and the links are all stored with the text.

### Measured

Zero verbatim violations across every run — 38 cards over five inputs (Claim-1 passage,
depth-engine roster, pricing paragraph, pronoun case, and the 27-card reference paste of
real prose). `span_verified` True on all of them.

**Segment text was byte-identical across two runs** on all three test inputs. Verbatim
cutting is markedly more stable than rewriting — there is less for the model to vary.
Node assignment still drifts; that is the classifier, untouched here.

Regression: the pricing paragraph still cuts to three tier segments, one group, one
classification call, one node. The roster is still 1 fact, `role=definition`.

### What this removed, and one thing to watch

**The LLM fidelity audit is gone.** Its verdicts — `strengthened`, `weakened`,
`unsupported`, `distorted`, `faithful` — all judge whether a *rewrite* drifted from the
source. With nothing rewritten, four are unreachable by construction and the fifth is a
string comparison. Replaced by `audit_spans()`: deterministic, free, and it cannot itself
be wrong. Verdicts are now `verbatim` / `unlocated`, and `VERIFY_PROMPT`,
`VERIFY_BATCH_SIZE` and `_verify()` are deleted. This drops one Bedrock call per 25
facts. There is no LLM check on split *quality*; that is a different question and is not
built.

`chunk_text(verify=...)` is accepted and ignored, documented as such rather than silently
dropped.

⚠️ **`subsumed` measured 0 on the 27-card paste, down from 2.** Segments are
non-overlapping by construction, so E1's failure mode — one fact being a strict partial
of another — can no longer be created, which is why the mechanism that caught it stops
firing. That reads as prevention rather than regression, but the SUBSUMED path is now
largely unexercised by this fixture. Recorded, not assumed.

### Chunker suite

⚠️ Written against the rewriting chunker. Cases 1–9 test preservation of epistemic
strength and refusal to infer speaker identity — both now guaranteed by copying rather
than by instruction, so those cases can no longer fail for the reason they were written.
`scripts/test_semantic_chunker.py`'s "unresolved reference" marker was relabelled as
informational: it is now the correct output. The suite needs rewriting around split
quality and span fidelity.

Extended from 9 to 11 cases: **10 — no over-merging** (a grouped pricing list must stay
one fact per tier) and **11 — no currency bleed** (each amount keeps its own currency).
Both guard the new group tagging, which must never license fusing a list or leaking one
member's units onto another. All 11 pass: strength markers fully retained on cases 4–11,
6/6 identity checks pass, no unresolved references.

## Known Gaps

- **The financial and GTM core carries PLACEHOLDER purposes — known weakness in the
  BP.9 branch.** The `add_tasks` paste overwrote the `purpose` on 11 nodes while
  leaving their titles, so each described a different node than it claimed to be and
  embedded as a blend of two concepts — matching facts confidently and wrongly.

  **10 of the 11 are resolved to placeholders** (`provenance='placeholder'`, not
  `'authored'`): `BP.9.4.1`–`.4` and `BP.9.5.1`, `.3`, `.4`, `.5`, `.6`, `.7`, each
  rewritten as *"Governs [title] without validating downstream commercial success."*
  This fixes the **contradiction** — the purpose is now about the right concept — but
  the purpose adds nothing the title did not already say.

  **They remain `DEGRADED_TARGET` and stay out of the trusted classifier set**, same
  treatment as the null-`required_output` nodes. A fact destined for one is held for
  review, never auto-filed. Clearing the flag requires a real authored purpose.

  Consequence to expect: **Build produces thin output for the pricing, sales-cycle,
  unit-economics and financial-model nodes** until real purposes are written. This is
  the revenue core of the plan, so it is the branch where thin output matters most.

  **`BP.10.2.3 Behavioural Observation Framework` has no rewrite and is still
  contradicted.** Its purpose describes pre/post comparison methodology — the node now
  at `BP.10.2.12 Pre/Post Comparison Design`. The BP.9 placeholder formula does not fit
  it (it turns on *commercial success*, while BP.10.2.3 governs behavioural and PMF
  evidence), and no text was invented for it. It stays contradicted **and** degraded
  until Alex authors a purpose.

  Not affected: `BP.9.5.2 Cost Structure`, whose purpose was already correct and was
  never in the Tier 2 set.
- Phase 5 workspace handlers still stubs (Build, Challenge, Validate, Export not wired to real agents)
- Mother Agent end-to-end orchestration not yet run live
- Memory index (`chunk_relationships`) not built
- Exec summary coherence check missing
- Context size on late sections (12, 13) not monitored
- **BROKEN: `tests/test_classifier_accuracy.py`, `tests/test_classifier_validation.py`.**
  Both fail at *collection* with
  `ModuleNotFoundError: No module named 'web.handlers.feed_handler'` — that module
  was deleted in commit `5ec4502` ("Remove Feed handler") and the imports were never
  updated. Two consequences: (a) the classifier accuracy and validation coverage does
  not run at all, and it is the coverage we need when the classifier is rebuilt in
  Phase 2 — do not assume the classifier is tested; (b) a bare `pytest tests/` aborts
  during collection and runs *nothing*, so the suite currently needs
  `--ignore=tests/test_classifier_accuracy.py --ignore=tests/test_classifier_validation.py`.
  Not fixed — the tests target a handler that no longer exists, so repointing them is
  part of the classifier rebuild, not a rename.
  **When you rebuild the classifier, read the `degraded_target` CONTRACT at the top of
  this file first — it is a build requirement.**
- **BROKEN: `boto3` is hijacked session-wide by three test modules.**
  `tests/test_full_pipeline_e2e.py:233`, `tests/test_phase2_e2e.py:170` and
  `tests/test_phase2_new_agents.py:174` each run, **at module level**, the equivalent of

  ```python
  mock_boto3 = MagicMock()
  sys.modules["boto3"] = mock_boto3   # never restored
  ```

  Module level means it fires during *collection*, before any test runs, and it is
  never undone — so `boto3` is a `MagicMock` for the rest of the session.
  `services/embedding_service._get_client()` then caches that mock as its module
  singleton, and every later `embed()` returns a mock body →
  `TypeError: the JSON object must be str, bytes or bytearray, not MagicMock`.

  **Consequence: suite results depend on collection order.**
  `tests/test_rag_service.py` passes 22/22 on its own and fails 15 times in a full
  run, purely because `test_full_pipeline_e2e` sorts earlier and poisons `boto3`
  first. Nothing that embeds can reach Supabase in a full run, so a full-suite pass
  proves much less than it appears to — **do not use a full-suite result as evidence
  that a live write path works.** Verify live paths with the targeted RAG command in
  CLAUDE.md, which collects none of these three modules.

  Fixing it means moving each hijack into a fixture that restores `sys.modules` on
  teardown (or `mock.patch.dict(sys.modules, ...)`), not deleting it — those tests do
  need boto3 mocked.
- **Build v2 roster expects Team & Organisation sections the architecture does not
  contain.** `config/phase2/bp_sections.yaml` declares section `"2"` **Founding Team**
  (agent `entrepreneur_team`) and section `"4"` **Organisation Design** (agent
  `organisation_designer`). The BP architecture has **no owner for team or
  organisation topics at all**: across all 904 nodes only three titles match
  team/founder/hiring — `BP.9.6.4 Hiring Investment Plan`, `BP.9.5.17 Hiring Sequence`
  (both framed as *financial* inputs), and `BP.9.7.1 Founding Team Gap Analysis`,
  which is the only founding-team node in the entire architecture.

  Note the yaml keys are **not** BP node ids — `bp_sections.yaml` `"2"`/`"4"` are Build
  workspace sections, while `BP.2` is Core Problem and `BP.4` is Market Boundaries.
  This is the namespace mismatch flagged in the original Phase 0 survey, now concrete:
  it is not just two id schemes, it is two schemes that disagree about what sections
  exist.

  Candidate fix is a **top-level Team & Organisation section**, which is a new L1 and
  therefore Alex's call — and it interacts with the locked decision that BP.13 is a
  system-only review bucket outside the architecture, so the new section could not
  simply take the next number. **Deferred — not part of Phase 0.** Does not block
  Step 2. Interim position: `BP.9.7 Team Execution Readiness` (see the Tier 1
  authoring mapping) is the pragmatic placement, team-as-cost-input under the
  financial domain, explicitly not the structurally correct one.

## Test Suite Health — needs its own pass (recorded 2026-07-27)

Not scheduled. Recorded so it is a known quantity rather than a surprise later.

A full run (`pytest tests/ --timeout=120`, with the two classifier modules ignored and
`test_full_pipeline_e2e.py::test_coherence_audit_with_issues` deselected) gives:

```
441 passed, 78 failed, 27 errors, 1 skipped, 1 deselected — ~19% red
```

Three root causes account for most of it:

| Failures | Cause |
|---|---|
| 32 | the `boto3` `sys.modules` hijack above — order-dependent |
| 11 | `web.handlers.feed_handler` deleted in `5ec4502`, imports never updated |
| 6 | SPADE remnants (`NameError: CyclicBehaviour`) and schema drift (e.g. `sessions.chat_id` missing, `PGRST204`) |

Two further facts worth carrying:

- **`tests/test_full_pipeline_e2e.py::test_coherence_audit_with_issues` hangs forever**
  on a live HTTPS call — 50 minutes at 5 seconds of CPU, blocked in `epoll_wait`. It is
  deselected in the command above. `pytest-timeout` (now in requirements.txt) converts
  this class of stall into a reported failure; without it a single hung call silently
  consumes the entire run.
- **The suite cannot gate anything until this is addressed.** ~19% red with
  order-dependent results means a red run carries no signal — you cannot tell a real
  regression from collection-order noise.
