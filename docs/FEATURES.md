# EpistemicOS — Complete Feature Documentation

> **Last Updated:** July 13, 2026  
> **Version:** Phase 2 Complete

---

## Table of Contents

- [Core Workspaces](#core-workspaces)
- [Intelligence Features](#intelligence-features)
- [UI Components](#ui-components)
- [Technical Features](#technical-features)
- [API Reference](#api-reference)

---

## Core Workspaces

### 1. Feed Workspace
**Purpose:** Add new data to the business plan

**Capabilities:**
- **File Upload:** PDF, DOCX, TXT, CSV, JSON
  - PDF: OCR via pdf2image + pytesseract
  - DOCX: python-docx parser
  - CSV: pandas with auto-delimiter detection
- **Paste Raw Text:** Gets auto-structured into facts
- **Batch Fact Review:** Approve/reject/edit all facts from upload
- **Auto-Classification:** Finds which BP node each fact belongs to (4-level hybrid classifier)
- **Duplicate Detection:** Warns if similar content exists (0.85+ similarity threshold)
- **Domain Classifier:** Prompt-based + LLM + strict none_fit validation
- **Node Creation:** Creates new nodes when content doesn't fit existing structure
- **Epistemic Status Tagging:** CONFIRMED, ASSUMPTION, CONTRADICTION, MISSING

**Flow States:**
- `FEED_AWAITING_INPUT` — ready for data entry
- `FEED_AWAITING_APPROVAL` — batch review active
- `FEED_AWAITING_NODE_SELECTION` — manual node picker
- `FEED_AWAITING_PARENT` — selecting parent for new node
- `FEED_AWAITING_DOMAIN_NAME` — naming new top-level domain
- `FEED_AWAITING_NODE_NAME` — naming new child node

**Output:** Facts stored in `knowledge_base` table with metadata, node assignments, and confidence scores

---

### 2. Build Workspace
**Purpose:** Generate business plan sections using agents

**Capabilities:**
- **Build Full Plan:** Generates all sections end-to-end
- **Build Single Section:** Targets specific BP node (e.g., BP.9)
- **Incremental Rebuild:** Only regenerates sections with changed dependencies
- **Build Weak Sections:** Auto-selects sections below quality threshold (default <40%)
- **Dependency Checking:** Blocks build if prerequisites are unmet
- **Status Dashboard:** Shows readiness, blockers, section scores

**Output:** Agent-generated content stored as `source_type=agent_insight` in `knowledge_base`

**Dependencies:**
- Reads `bp_dependencies.json` to determine build order
- Queries `knowledge_base` for input data per section
- Uses Claude Sonnet for content generation

---

### 3. Inspect Workspace
**Purpose:** Analyze what's strong, weak, or missing in the plan

**Capabilities:**
- **Coverage Heatmap:** Fact density visualization per node (red → amber → green)
- **Contradiction Detection:** Finds conflicting facts via semantic similarity + LLM verification
- **Staleness Detection:** Flags assumptions older than 30 days
- **Gap Analysis:** Identifies missing critical data
- **Node-Level Health Scores:** Combines coverage, staleness, contradictions

**Output:** Structured data for visualization (heatmap rendered inline, lists of issues)

**Metrics:**
- Coverage: facts per 1000 expected words
- Staleness: days since last validation
- Contradiction severity: low/medium/high/critical

---

### 4. Challenge Workspace (Devil's Advocate)
**Purpose:** Stress-test assumptions and find vulnerabilities

**Capabilities:**
- **Challenge Weakest Assumptions:** Auto-selects top-k by age (default k=3)
- **Challenge Specific Section:** Targets a BP node or claim
- **Adversarial Prompting:** Uses Claude to try breaking assumptions
- **Risk Scoring:** Critical (>30 days old) / High / Medium / Low
- **Counter-Arguments:** Returns edge cases, contradictory evidence

**Output:** Structured vulnerability reports with:
- `assumption_id`
- `challenge_text` (adversarial prompt)
- `risk_level`
- `age_days`

**Use Case:** Before finalizing a section, run Challenge to surface hidden risks

---

### 5. Validate Workspace
**Purpose:** Confirm or kill assumptions based on evidence

**Capabilities:**
- **Validation Queue:** Shows pending assumptions for review
- **Confirm Assumption:** Promotes `ASSUMPTION` → `CONFIRMED` in `knowledge_base`
- **Kill Assumption:** Writes to `negative_knowledge` (never re-suggest)
- **Assumption Lifecycle Tracking:** Records state transitions (ASSUMPTION → CHALLENGED → VALIDATED/KILLED)
- **Evidence Collection:** Attaches supporting/refuting facts

**Output:** Updates `epistemic_status` in `knowledge_base`, logs events to `assumption_lifecycle` source type

**States:**
- ASSUMPTION (initial)
- CHALLENGED (Devil's Advocate ran)
- VALIDATED (confirmed with evidence)
- KILLED (rejected, stored in negative_knowledge)

---

### 6. Export Workspace
**Purpose:** Generate professional documents

**Capabilities:**
- **Full DOCX Business Plan:** Styled, branded, with TOC
- **Executive Summary Only:** High-level overview for investors
- **Investor Version:** Hides uncertainties and assumptions
- **Internal Version:** Shows all assumptions, risks, gaps
- **Gap Report:** What's missing from the plan
- **Export Readiness Check:** Validates completeness before generation
- **Live Section Preview:** Real-time trace as sections are assembled

**Output:** DOCX files saved to `outputs/` directory

**Styling:**
- Brand colors: Blue (primary), Green (secondary), Amber (warning), Red (critical)
- Fonts: Calibri (body), Arial (headings)
- Confidence color-coding per section
- Professional page layout with headers/footers

---

### 7. Auto Workspace (Just Chat)
**Purpose:** Ask anything — system auto-routes to appropriate handler

**Capabilities:**
- **Question Classification:** Detects system questions vs general queries
- **Decision Routing:** Recognizes Yes/Adjust/Kill responses
- **Intent Detection:** Routes to Feed/Build/Inspect/Challenge/Validate
- **Context-Aware Responses:** Maintains conversation state

**Special Handling:**
- System questions → Answer Engine
- "Yes" / "Adjust" / "Kill" → Decision handler
- Data pasting → Feed workspace
- Build requests → Build workspace

---

## Intelligence Features

### 8. Intelligent Answer Engine
**Purpose:** Answer any question accurately with multi-source retrieval

**How It Works:**
1. **Question Detection** (regex-based, <5ms)
2. **Search Planning** (Haiku generates 2-4 search operations)
3. **Parallel Execution** (semantic + metadata + keyword + architecture searches)
4. **Confidence Assessment** (high/medium/low/insufficient based on relevance)
5. **Sonnet Synthesis** (answers ONLY from retrieved context, cites nodes)

**Search Operation Types:**
- **Semantic:** Vector similarity (pgvector + Amazon Titan Embed v2)
- **Metadata:** Structured DB queries (counts, dates, node lookups)
- **Keyword:** Exact text match (ILIKE)
- **Architecture:** Node hierarchy lookup (bp_architecture.json)

**Output:** Answer card with:
- Formatted answer (markdown with tables, trees, status symbols)
- Confidence bar (green/amber/red/gray)
- Collapsible sources (node IDs clickable → opens side panel)
- Search operations run count

**Rich Formatting:**
- **Tables** for multi-attribute data
- **Status symbols** (✅ ⚠️ ❌ 🔄 📌)
- **Tree diagrams** for hierarchy
- **Bold monospace** for node IDs: **`BP.13`**

**Works In:** ALL workspaces (Feed, Build, Inspect, Challenge, Validate, Export, Auto)

---

### 9. RAG Knowledge System
**Purpose:** 12-layer semantic memory with pgvector

**Architecture:**
```
CEO Data (ceo_data/*.json) → Ingestion Pipeline → Supabase pgvector
Conversations/Decisions → conversation_store.py → knowledge_base
Agent Insights/Metadata → rag_hooks.py → knowledge_base
                                            ↓
All Agents ← rag_service.py ← Semantic Retrieval (cosine similarity)
```

**12 Source Types:**
1. `ceo_doc` — Alex's static data (Source-of-Truth document)
2. `conversation` — CEO messages, Q&A pairs
3. `decision` — Yes/Adjust/Kill decisions + reasoning
4. `negative_knowledge` — Killed ideas (never re-suggest)
5. `correction` — CEO overrides (supersedes old facts)
6. `feedback` — CEO feedback on outputs
7. `agent_insight` — Key findings from pipeline runs
8. `preference_pattern` — Derived CEO preferences
9. `external_research` — Cached web search results (90-day stale_after)
10. `assumption_lifecycle` — Evidence events for/against assumptions
11. `contradiction_resolution` — Resolved contradictions
12. `run_metadata` — Pipeline run summaries

**Key Features:**
- **Vector Similarity:** Amazon Titan Embed v2 (1024-dim normalized vectors)
- **Temporal Decay:** Recent facts rank higher (freshness weight)
- **Supersede Mechanism:** Old facts marked as `superseded_by: new_id`, hidden but preserved
- **Batch Storage:** Up to 100 facts in single transaction
- **Conversation Store:** Logs all CEO messages with timestamps
- **Assumption Tracker:** Records lifecycle events (created → challenged → validated/killed)
- **Preference Extraction:** Detects patterns from repeated decisions
- **Research Cache:** External data with 90-day expiry

**Performance:**
- Retrieval latency: ~400ms (remote Supabase)
- Threshold: 0.4 similarity (better discrimination with 1024-dim)
- Recency boost: +0.1 for facts <7 days old

---

## UI Components

### 10. Stored Data Table
**Purpose:** View and manage all facts in the knowledge base

**Features:**
- **Paginated Table:** 50 rows per page
- **Sort Options:**
  - BP order (depth-first: BP.1 → BP.1.1 → BP.1.1.1 → BP.1.2 → BP.2)
  - Most recent first
- **Filter by Status:** CONFIRMED, ASSUMPTION, CONTRADICTION, MISSING, KILLED
- **Confidence Bar:** Visual bar + percentage per row (green/amber/red)
- **Bulk Actions:**
  - Select multiple rows (checkboxes)
  - Bulk confirm/assume/kill/delete
  - Max 100 rows per batch
- **Inline Edit:**
  - Click pencil icon → edit content + status in-place
  - Save/Cancel buttons
  - PATCH API updates Supabase directly
- **Clickable Node IDs:** Opens side panel with full node detail
- **Export as XLSX:** Downloads full table

**Table Columns:**
- Checkbox (bulk select)
- Node ID (clickable)
- Status (colored pill)
- Confidence (bar + %)
- Content (truncated, expandable via edit)
- Source (ceo_doc, conversation, etc.)
- Stored At (timestamp)
- Actions (pencil icon)

**Location:** Accessible via tray tile "Stored Data" or Cmd+K → "stored data"

---

### 11. Side Panel (Artifact-style)
**Purpose:** Show detailed views without leaving the conversation

**Design:**
- Slides in from right (560px wide)
- Backdrop overlay (click to close)
- Escape key to close
- Stays on top of chat (z-index 1101)

**Node Detail View:**
- **Meta Grid:**
  - Node ID, Name, Parent (clickable), Depth
  - Total Facts, Last Updated
- **Status Breakdown:** Colored chips (e.g., "5 CONFIRMED", "2 ASSUMPTION")
- **Children Section:** Clickable chips for sub-nodes
- **Facts List:** All stored facts under that node, color-coded by status
- **Navigation:** Click any child → loads that node without closing panel

**Triggers:**
- Click node ID in stored data table
- Click source in answer card
- Click child node within panel (navigable)

**Use Cases:**
- "What's in BP.9?" → Click BP.9 → see all marketing facts
- Answer card shows 5 sources → click any → see full context
- Exploring hierarchy → click BP.1 → see children → click BP.1.1 → drill down

---

### 12. Command Palette
**Purpose:** Quick search and navigation (Cmd+K)

**Features:**
- **Keyboard Shortcut:** `Cmd+K` or `Ctrl+K`
- **Workspace Switcher:** Type "feed" → Enter → switches to Feed
- **Search Stored Data:** Type query → shows matching facts
- **Run Commands:** Type "export" → quick actions
- **Keyboard Navigation:** Arrow keys + Enter
- **Fuzzy Search:** Tolerates typos
- **Escape to Close**

**Commands Available:**
- Switch to [workspace]
- Show stored data
- Show activity log
- Show epistemic log
- Export business plan
- Challenge assumptions
- Validate queue

---

### 13. Live Activity Drawer
**Purpose:** Real-time pipeline trace during agent execution

**Features:**
- **Shows Agent Execution:** Thinking, finding, verifying events
- **Phase Grouping:** Clusters related events (e.g., "Find contradictions" phase)
- **Timestamps:** Per event
- **WebSocket Updates:** No polling, instant updates
- **Expandable Traces:** Click to see full context

**Event Types:**
- `thinking` — Agent reasoning
- `finding` — Searching knowledge base
- `validating` — Running verification
- `synthesizing` — Generating output
- `complete` — Final result

**Trigger:** Opens automatically when pipeline starts (Build/Challenge/Validate)

---

### 14. Epistemic Log Drawer
**Purpose:** Audit trail for knowledge evolution

**Features:**
- **Killed Ideas:** Facts that were rejected (negative_knowledge)
- **Review History:** All validation decisions with timestamps
- **Staleness Warnings:** Facts older than 30 days
- **Contradiction Log:** Resolved and unresolved conflicts

**Use Case:** "What did we decide NOT to do and why?"

---

### 15. Process Panel (Batch Fact Review)
**Purpose:** Review and edit multiple facts before storing

**Features:**
- **Editable Cards:** Each fact in a card with:
  - Content (textarea, editable)
  - Node assignment (dropdown)
  - Epistemic status (dropdown)
- **Actions per Card:**
  - ✅ Approve (saves as-is)
  - ❌ Reject (discards)
  - ✏️ Edit (inline)
- **Bulk Actions:**
  - Approve all
  - Reject all
- **Real-Time Validation:** No empty content, valid status enum

**Trigger:** After uploading file or pasting text in Feed workspace

---

## Technical Features

### Authentication & Sessions
- **Token-Based Auth:** `WEB_AUTH_TOKEN` env var
- **Session Management:** Redis (Upstash) with 24-hour TTL
- **Automatic Archival:** Expired sessions written to Supabase `sessions.archived_state`
- **WebSocket per Session:** One connection per browser tab
- **State Tracking:** Current workspace, flow state, conversation history

### File Upload Processing
**Supported Formats:** PDF, DOCX, TXT, CSV, JSON

**PDF Processing:**
- OCR via `pdf2image` + `pytesseract`
- Page-by-page extraction
- Metadata: filename, page numbers

**DOCX Processing:**
- `python-docx` parser
- Preserves formatting (bold, lists)
- Extracts tables

**CSV Processing:**
- `pandas` with auto-delimiter detection
- Row-by-row fact extraction
- Column headers as metadata

**Chunking:**
- Splits large files into manageable facts (max 500 words per fact)
- Preserves context with overlap (50 words)
- Metadata: chunk index, total chunks

### Auto-Classification Pipeline
**4-Level Hybrid Classifier:**

1. **Prompt-Based Filter** (fast regex patterns)
   - Detects obvious out-of-scope (e.g., personal greetings, off-topic)
   - <5ms per fact

2. **LLM Domain Matcher** (Claude Haiku via Bedrock)
   - Generates top-3 candidate nodes
   - Confidence scores per candidate
   - ~500ms per fact

3. **Strict None-Fit Rejection**
   - Prohibits out-of-scope content from auto-assignment
   - Forces manual review if uncertain

4. **Fallback to Manual Selection**
   - If ambiguous or confidence <0.7, shows picker
   - Alex selects correct node

**Accuracy:** 22% on structured docs (as of June 2026) — improvements planned in Phase 3

### Duplicate Detection
**How It Works:**
- Embeds incoming fact via Amazon Titan Embed v2
- pgvector cosine similarity search against existing facts
- Threshold: 0.85+ triggers warning

**User Experience:**
- Shows existing content side-by-side
- Allows override ("This is different")
- Prevents accidental re-entry

### Node Creation Flow
**When content doesn't fit existing structure:**

1. Classifier returns `no_domain_match`
2. System asks: "Create new top-level domain or add under existing?"
3. **If new domain:**
   - Alex names the domain (e.g., "Legal & Compliance")
   - Creates parent node (e.g., `BP.14`)
   - Updates `bp_architecture.json`
4. **If under existing:**
   - Alex selects parent (e.g., `BP.9 Marketing`)
   - Alex names the child (e.g., "Social Media Strategy")
   - Creates child node (e.g., `BP.9.4`)
5. Fact stored under new node

### Batch Fact Review
**Process Panel Details:**
- **Trigger:** After file upload or text paste in Feed
- **Layout:** Scrollable cards, 3 visible at once
- **Per-Card Actions:**
  - Content: inline textarea (editable)
  - Node: dropdown (all BP nodes)
  - Status: dropdown (CONFIRMED/ASSUMPTION/CONTRADICTION/MISSING)
  - Buttons: Approve ✅, Reject ❌
- **Bulk Actions:** Top bar with "Approve All" / "Reject All"
- **Validation:** No empty content, valid status enum
- **Save:** Approved facts → `knowledge_base`, rejected → discarded

### Coverage Heatmap
**Calculation:**
```
coverage_score = (actual_facts / expected_facts) × 100
expected_facts = estimated_words / 1000
```

**Color Scale:**
- 0-20%: Red (empty)
- 21-50%: Amber (sparse)
- 51-100%: Green (complete)

**Rendered:** Inline bar chart in Inspect workspace

### Contradiction Detection
**Algorithm:**
1. Find pairs of facts with high semantic similarity (0.75+)
2. LLM verification: "Do these statements contradict?"
3. If yes, classify contradiction type:
   - **Direct:** A says X, B says not-X
   - **Temporal:** A was true then, B is true now
   - **Conditional:** Both true under different conditions

**Output:**
```json
{
  "fact_a_id": "uuid-1",
  "fact_b_id": "uuid-2",
  "contradiction_type": "direct",
  "severity": "high",
  "resolution_suggestion": "supersede"
}
```

### Staleness Detection
**Logic:**
- Query `knowledge_base` for `epistemic_status=ASSUMPTION`
- Filter by `created_at < (now - 30 days)`
- Join with `assumption_lifecycle` to check if challenged/validated
- Return unvalidated assumptions with age

**Risk Scoring:**
- **Critical:** >30 days old, blocks dependent sections
- **High:** 15-30 days old
- **Medium:** 7-14 days old
- **Low:** <7 days old

### Assumption Lifecycle
**States:**
- `ASSUMPTION` (initial)
- `CHALLENGED` (Devil's Advocate ran)
- `VALIDATED` (confirmed with evidence)
- `KILLED` (rejected, moved to negative_knowledge)

**Events Stored:**
```json
{
  "assumption_id": "uuid",
  "event_type": "challenged",
  "timestamp": "2026-07-13T10:30:00Z",
  "agent": "challenge_handler",
  "evidence": "Market research contradicts assumption"
}
```

**Timeline View:** Shows all events for an assumption in chronological order

### Decision System
**Alex's Response Options:**
- **Yes:** Proceeds, writes decision to `knowledge_base` with `source_type=decision`
- **Adjust:** Increments `version` field, asks for changes, loops back to previous state
- **Kill:** Archives session, logs reasoning in `negative_knowledge`, never re-suggests

**Decision Record:**
```json
{
  "decision_id": "uuid",
  "question": "Should we target enterprise customers?",
  "response": "Yes",
  "reasoning": "Aligns with long-term revenue goals",
  "timestamp": "2026-07-13T10:30:00Z",
  "version": 1
}
```

**Versioning:** Each "Adjust" increments version, preserves history

### Preference Extraction
**Pattern Detection:**
- Repeated "Yes" on similar topics → preference
- Repeated "Adjust" with same reason → avoid pattern
- Example: Alex always says "No" to ads → `preference: organic_growth_only`

**Storage:** Stored as `source_type=preference_pattern` in RAG

**Usage:** Agents query preferences before making suggestions

### Temporal Decay
**Freshness Scoring:**
```python
age_days = (now - created_at).days
if age_days < 7:
    freshness_boost = 0.1
elif age_days < 30:
    freshness_boost = 0.05
else:
    freshness_boost = 0.0

final_score = base_similarity + freshness_boost
```

**Effect:** Recent facts rank higher in retrieval

### Supersede Mechanism
**How It Works:**
1. New fact arrives that corrects old fact
2. Old fact marked as `superseded_by: new_fact_id`
3. `epistemic_status` set to `SUPERSEDED`
4. All queries filter `is_('superseded_by', 'null')`

**History Preserved:** Old facts remain in DB for audit trail

**Cycle Prevention:** Validates that supersede chain doesn't create loops

### Rich Content Rendering
**Message Types:**

| Type | Trigger | Rendered As |
|------|---------|-------------|
| `workspace_intro` | Switch workspace | Icon + description |
| `heatmap` | Inspect coverage | Bar chart visualization |
| `fact_list` | Feed batch review | Editable cards |
| `decision` | Awaiting Alex | Yes/Adjust/Kill buttons |
| `answer_card` | Answer Engine | Confidence + sources + markdown |
| `menu` | Multi-choice | Numbered options |

**Markdown Support:** Answer cards parse markdown with `marked.js`

### WebSocket Real-Time Updates
**Architecture:**
- FastAPI WebSocket endpoint: `/ws/{session_key}`
- One connection per browser tab
- Broadcasts to all clients in same session
- Automatic reconnect on disconnect (exponential backoff)

**Message Format:**
```json
{
  "role": "assistant",
  "text": "Processing your request...",
  "metadata": {
    "rich_type": "trace",
    "event": "thinking",
    "phase": "Find contradictions"
  },
  "timestamp": "2026-07-13T10:30:00Z",
  "channel": "system"
}
```

**Trace Events:**
- `thinking` — Agent reasoning
- `finding` — Searching knowledge base
- `validating` — Running verification
- `synthesizing` — Generating output
- `complete` — Final result

### Export Styling
**DOCX Generation via `python-docx`:**

**Brand Colors:**
- Primary: RGB(37, 99, 235) — Blue
- Secondary: RGB(16, 185, 129) — Green
- Accent: RGB(245, 158, 11) — Amber
- Error: RGB(239, 68, 68) — Red

**Fonts:**
- Body: Calibri 11pt
- Headings: Arial Bold (H1: 18pt, H2: 14pt, H3: 12pt)

**Features:**
- Table of Contents (auto-generated with hyperlinks)
- Page numbers in footer
- Section breaks (each BP section on new page)
- Confidence color-coding (green/amber/red text)
- Professional spacing (1.15 line height)

### Security Features
- **Auth on All Endpoints:** `token` parameter validated against `WEB_AUTH_TOKEN`
- **No SQL Injection:** Supabase SDK only (no raw SQL)
- **File Upload Limits:** 10MB max
- **Sanitized HTML:** `escapeHtml()` on all user content
- **No eval() or exec():** Static code only
- **CORS:** Configured for localhost only
- **Rate Limiting:** (planned for Phase 3)

### Performance Optimizations
- **Parallel Search:** ThreadPoolExecutor with 4 workers
- **Pagination:** 50 rows per page (not loading full table)
- **Cached Architecture:** `bp_architecture.json` loaded once, cached in memory
- **Debounced Search:** Command palette waits 300ms before searching
- **WebSocket Batching:** Groups multiple traces → single broadcast
- **Vector Index:** pgvector ivfflat index on embeddings

### Error Handling
- **LLM Fallbacks:** If Sonnet fails, retry with Haiku
- **Try/Catch on All API Calls:** No uncaught exceptions
- **User-Friendly Messages:** "Something went wrong — try again" (logs detailed error)
- **Logging:** All errors written to `events_logs` table
- **Automatic Retry:** Transient failures (network timeout) retry 3x with backoff

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Open command palette |
| `Escape` | Close active panel/drawer |
| `Enter` | Submit message |
| `Shift+Enter` | New line in input (no submit) |
| `Cmd+/` | Show shortcuts help |

### Mobile Responsive
- **Adaptive Layouts:** `min(560px, 90vw)` for panels
- **Touch-Friendly:** Min 44px tap targets
- **Drawer Slide-Ins:** Work on mobile (swipe to close)
- **Viewport Meta Tag:** `width=device-width, initial-scale=1`
- **Font Scaling:** Readable on small screens (min 12px)

### Data Integrity
- **Epistemic Status Enum:** DB-enforced (CONFIRMED/ASSUMPTION/CONTRADICTION/MISSING/KILLED)
- **Node ID Validation:** Must exist in `bp_architecture.json` before storage
- **Foreign Key Constraints:** Supabase RLS enforces relationships
- **Supersede Chain Validation:** No cycles (A→B→C→A rejected)
- **Batch Operation Limits:** Max 100 rows per bulk action
- **UUID v4:** All IDs generated with `uuid.uuid4()`, globally unique

---

## API Reference

### Authentication
All endpoints require `token` parameter matching `WEB_AUTH_TOKEN` env var.

### Endpoints

#### Knowledge Base

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/knowledge/stored` | Paginated table of all facts |
| `PATCH` | `/api/knowledge/stored/{row_id}` | Inline edit content/status |
| `POST` | `/api/knowledge/stored/bulk-action` | Bulk update or delete |
| `GET` | `/api/knowledge/stored/export` | Download as XLSX |
| `GET` | `/api/knowledge-base` | Search knowledge base |
| `POST` | `/api/knowledge-base/add` | Add single fact |

#### Workspaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/menu` | Main workspace menu |
| `GET` | `/api/menu/{workspace_id}` | Workspace-specific menu |
| `POST` | `/api/workspace/switch` | Switch to workspace |
| `GET` | `/api/workspace/state` | Current workspace + state |

#### Build

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/build/status` | Readiness, blockers, scores |

#### Inspect

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/inspect/coverage` | Coverage heatmap data |
| `GET` | `/api/inspect/contradictions` | Conflicting facts |
| `GET` | `/api/inspect/stale` | Old assumptions |

#### Challenge

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/challenge/vulnerabilities` | Weakest assumptions |

#### Validate

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/validate/queue` | Pending assumptions |

#### Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/export/generate` | Generate DOCX |
| `GET` | `/api/export/download/{filename}` | Download generated file |
| `GET` | `/api/export/readiness` | Check completeness |

#### Feed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/feed/upload` | Upload file |
| `POST` | `/api/feed/bulk-approve` | Approve batch facts |

#### Side Panel

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/panel/node/{node_id}` | Full node detail |

#### Epistemic

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/epistemic/killed-ideas` | Negative knowledge |
| `GET` | `/api/epistemic/council` | Review history |
| `POST` | `/api/epistemic/confirm-chunk` | Validate fact |

#### Session

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/session-key` | Generate new session |
| `POST` | `/api/messages` | Send message (returns response) |
| `GET` | `/api/messages/{session_key}` | Chat history |
| `WS` | `/ws/{session_key}` | WebSocket connection |

#### Misc

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/dashboard` | Summary metrics |
| `GET` | `/api/recommendation` | Suggested next action |
| `GET` | `/api/search` | Global search |
| `GET` | `/api/assumptions/lifecycle` | Assumption events |
| `GET` | `/api/digest` | Daily summary |
| `GET` | `/api/non-scope/queue` | Out-of-scope content |

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11, FastAPI, asyncio |
| LLM | Claude (Bedrock) — Sonnet 4.6 + Haiku 4.5 |
| Database | Supabase / Postgres |
| Vector Search | pgvector (ivfflat index) |
| Embeddings | Amazon Titan Embed v2 (1024-dim) |
| Session Store | Redis (Upstash) |
| WebSocket | FastAPI WebSocket |
| Frontend | HTML5, Vanilla JS, CSS3 |
| Markdown | marked.js |
| Icons | Lucide Icons |
| Document Export | python-docx |
| PDF Processing | pdf2image, pytesseract |
| CSV Processing | pandas |

---

## What's Not Built Yet

**Phase 3 Priorities (P0 — Structural Fixes):**
- Multi-agent pipeline (Mother Agent + 9 child agents) — exists but not wired to web UI
- SPADE messaging between agents
- SimPy Monte Carlo simulation

**Phase 3 (P1 — Performance):**
- Caching layer (Redis for RAG results)
- Rate limiting on API endpoints
- WebSocket compression
- Database query optimization

**Phase 3 (P2 — True MAS Intelligence):**
- Multi-perspective synthesis (3-agent panel for decisions)
- Adversarial validation (2+ agents try to refute findings)
- Self-correction loops (agent detects own mistakes)
- Memory consolidation (periodic summaries)

**Phase 3 (P3 — Optimization):**
- Batch embedding (process 100 facts → 1 API call)
- Lazy loading (defer non-critical UI components)
- Service worker (offline mode)
- Progressive web app (installable)

---

## Getting Started

**Prerequisites:**
- Python 3.11+
- Supabase account (or local Postgres with pgvector)
- Redis instance (Upstash or local)
- AWS Bedrock access (Claude models enabled)

**Installation:**
```bash
pip install -r requirements.txt
```

**Environment Variables:**
```bash
# .env file
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
REDIS_URL=redis://your-redis-instance
WEB_AUTH_TOKEN=your-secret-token
AWS_BEDROCK_REGION=us-east-1
CLAUDE_SONNET_MODEL=us.anthropic.claude-sonnet-4-6-v1:0
CLAUDE_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001:0
```

**Run the Server:**
```bash
python main.py
```

**Open Browser:**
```
http://localhost:8000
```

**First-Time Setup:**
1. Click Feed workspace
2. Upload Alex's Source-of-Truth document
3. Review and approve facts
4. Switch to Build workspace → "Build Full Plan"
5. Check Inspect workspace for gaps
6. Use Export workspace to generate DOCX

---

## Contributing

**Code Style:**
- `snake_case` everywhere (functions, variables, files)
- Type hints on every function
- Docstrings on public functions
- `Black` formatting (line length 88)
- `logging` module only (never `print()`)

**Commit Message Format:**
```
<subject line (50 chars)>

<body: what changed and why (wrap at 72 chars)>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

**Testing:**
```bash
pytest tests/ -v
```

---

## Support

**Issues:** [GitHub Issues](https://github.com/Saiaditya26122006/Multi-Agent/issues)  
**Documentation:** `docs/` directory  
**CLAUDE.md:** Project-specific instructions for AI assistants

---

**Last Updated:** July 13, 2026  
**Version:** Phase 2 Complete + Answer Engine + Side Panel  
**Next Milestone:** Phase 3 — Structural Fixes (Q3 2026)
