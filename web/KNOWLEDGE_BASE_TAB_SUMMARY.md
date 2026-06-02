# Knowledge Base Tab — Implementation Summary

## What Was Added

### 1. API Routes (web/server.py)

**Import added:**
```python
import json  # Line 5
```

**New Pydantic model (line 87-93):**
```python
class AddFactRequest(BaseModel):
    """Payload for POST /api/knowledge-base/add."""
    topic: str
    fact: str
    status: str
    token: str
```

**GET /api/knowledge-base (lines 243-256):**
- Calls `load_all_ceo_data()` from `ceo_data/loader.py`
- Returns: `{"topics": {...}}`
- Auth: requires `?token=X` query param
- Status: ✓ Import verified, function loads 11 topics successfully

**POST /api/knowledge-base/add (lines 259-309):**
- Accepts: `{"topic": str, "fact": str, "status": str, "token": str}`
- Validates status in: CONFIRMED, ASSUMPTION, INFERRED, CONTRADICTION
- Writes to: `ceo_data/{topic}.json`
- Creates file if missing with `_meta` structure
- Appends fact to `facts` array with timestamp
- Returns: `{"success": true, "topic": str, "fact_added": str}`

---

### 2. Frontend — Tab Button (web/static/index.html)

**Line 1504:**
```html
<button class="tab-btn" data-tab="knowledge" onclick="switchTab('knowledge')">Knowledge Base</button>
```

Added as third tab button, between "Pipeline Trace" and the header-right section.

---

### 3. Frontend — Tab Content Panel (web/static/index.html)

**Lines 1641-1694:** New `<div class="tab-content" id="tab-knowledge">` section with:

**A. Current Knowledge Section:**
- Loading spinner with "Loading knowledge base..." message
- Empty `<div id="knowledge-topics">` populated via JavaScript
- Topic cards show:
  - Topic name (formatted: `buyers_icp` → "Buyers Icp")
  - Fact count badge
  - List of facts with epistemic status badges (colored: green/amber/blue/red)
  - Gap cards (gray, italic) for `no_data` sections

**B. Add Fact Form:**
- Topic dropdown (pre-populated from loaded topics + "+ New topic..." option)
- Fact textarea (3 rows, required)
- Status selector (4 buttons: CONFIRMED / ASSUMPTION / INFERRED / CONTRADICTION)
- Submit button "Add to Knowledge Base" with icon

---

### 4. CSS Styles (web/static/index.html, lines 1131-1393)

**New style blocks added:**
- `.knowledge-container` — scrollable container, 32px padding
- `.knowledge-header` — title and subtitle
- `.knowledge-loading` — spinner + loading text
- `.knowledge-topics` — grid layout for topic cards
- `.topic-card` — white card with shadow, rounded borders
- `.topic-card.gap-card` — gray variant for gaps
- `.fact-item` — fact row with left border color-coded by status
- `.status-badge` — small pills with status labels (CONFIRMED, ASSUMPTION, etc.)
- `.knowledge-add-form` — form layout
- `.status-buttons` — grid of status selector buttons
- `.submit-btn` — blue submit button matching existing UI style

**Color mapping:**
- CONFIRMED → green (#16a34a)
- ASSUMPTION → amber (#f59e0b)
- INFERRED → blue (#3b82f6)
- CONTRADICTION → red (#ef4444)
- no_data → gray (#64748b)

---

### 5. JavaScript Functions (web/static/index.html, lines 1867-2067)

**Modified `switchTab()` function:**
- Added check: if switching to 'knowledge' tab and not loaded, call `loadKnowledgeBase()`

**New functions:**

1. **`loadKnowledgeBase()`** (line 1877)
   - Fetches GET `/api/knowledge-base?token=X`
   - Shows loading spinner
   - Calls `renderKnowledgeBase()` and `populateTopicDropdown()`
   - Sets `window.knowledgeLoaded = true` on success

2. **`renderKnowledgeBase(topics)`** (line 1898)
   - Iterates over topic keys (sorted alphabetically)
   - Handles text topics (deck.txt) separately
   - Calls `extractFacts()` to parse each topic's structure
   - Creates topic cards with fact lists
   - Shows gap cards for `no_data` topics

3. **`extractFacts(topicData)`** (line 1944)
   - Parses JSON structure to find all facts
   - Looks for objects/arrays with `status` field
   - Skips `_meta` fields
   - Returns flat array of facts

4. **`renderFact(fact)`** (line 1965)
   - Returns HTML string for a single fact item
   - Color-codes border and badge by status
   - Extracts content from various fact field names (fact, description, buyer, segment)

5. **`formatTopicName(key)`** (line 1980)
   - Converts `buyers_icp` → "Buyers Icp"

6. **`populateTopicDropdown(topics)`** (line 1984)
   - Populates `<select id="fact-topic">`
   - Adds "+ New topic..." option at end

7. **Status button click handler** (line 1998)
   - Sets active state on clicked button
   - Updates hidden `fact-status` input

8. **Form submit handler** (line 2006)
   - Validates all fields filled
   - Prompts for new topic name if "+ New topic..." selected
   - POSTs to `/api/knowledge-base/add`
   - Reloads knowledge base on success
   - Shows alert confirmation

---

## File Structure After Changes

```
web/
├── server.py                      [MODIFIED: +68 lines]
│   ├── import json added
│   ├── AddFactRequest model added
│   ├── GET /api/knowledge-base added
│   └── POST /api/knowledge-base/add added
└── static/
    └── index.html                 [MODIFIED: +463 lines]
        ├── Tab button added (line 1504)
        ├── CSS styles added (lines 1131-1393)
        ├── Tab content panel added (lines 1641-1694)
        └── JavaScript functions added (lines 1867-2067)
```

---

## Verification Results

1. **Import test:** ✓ `load_all_ceo_data()` imports successfully
2. **Data load test:** ✓ Loads 11 topics from `ceo_data/`
3. **Tab button:** ✓ Added at line 1504
4. **Tab content:** ✓ Panel exists at line 1641
5. **Routes:** ✓ GET and POST routes added at lines 243 and 259

---

## How It Works

### Loading Knowledge Base

1. User clicks "Knowledge Base" tab
2. `switchTab('knowledge')` called
3. If not loaded, `loadKnowledgeBase()` called
4. Fetches GET `/api/knowledge-base?token=X`
5. Backend calls `load_all_ceo_data()` from `ceo_data/loader.py`
6. Returns all JSON files as dict: `{"topics": {"buyers_icp": {...}, "financials": {...}, ...}}`
7. Frontend renders topic cards with facts
8. Each fact shows its epistemic status as a colored badge

### Adding a Fact

1. User fills form: topic, fact text, status
2. Clicks "Add to Knowledge Base"
3. POSTs to `/api/knowledge-base/add` with `{"topic": "buyers_icp", "fact": "...", "status": "CONFIRMED", "token": "..."}`
4. Backend:
   - Loads existing `ceo_data/{topic}.json` or creates new file
   - Appends to `facts` array: `{"fact": "...", "status": "CONFIRMED", "added_at": "2026-06-02T..."}`
   - Writes back to file
5. Frontend reloads knowledge base
6. New fact appears in the topic card

---

## What Was NOT Modified

- Existing Chat tab (untouched)
- Existing Pipeline Trace tab (untouched)
- WebSocket handler (untouched)
- Pipeline integration (untouched)
- `ceo_data/loader.py` (untouched — used as-is)
- Sidebar navigation (untouched)

---

## Testing Checklist

- [ ] Start server: `uvicorn web.server:app --reload --port 8000`
- [ ] Open browser: `http://localhost:8000`
- [ ] Enter auth token when prompted
- [ ] Click "Knowledge Base" tab — should load topics
- [ ] Verify topic cards appear with facts and colored status badges
- [ ] Verify gap topics (financials, team) show gray "No data yet" message
- [ ] Fill add fact form, submit — should reload and show new fact
- [ ] Click Chat tab — should return to chat
- [ ] Click Knowledge Base tab again — should load from cache (fast)

---

## API Response Examples

**GET /api/knowledge-base response:**
```json
{
  "topics": {
    "buyers_icp": {
      "_meta": {...},
      "buyer_personas": [
        {"buyer": "Associate Dean", "status": "ASSUMPTION", ...},
        {"buyer": "CIO / IT / Security", "status": "CONFIRMED", ...}
      ],
      "icp": [
        {"segment": "Business schools", "status": "CONFIRMED", ...}
      ]
    },
    "financials": {
      "status": "no_data",
      "gap_reason": "No financial data provided yet"
    }
  }
}
```

**POST /api/knowledge-base/add request:**
```json
{
  "topic": "buyers_icp",
  "fact": "New buyer persona: Head of Doctoral Programs",
  "status": "ASSUMPTION",
  "token": "changeme"
}
```

**POST /api/knowledge-base/add response:**
```json
{
  "success": true,
  "topic": "buyers_icp",
  "fact_added": "New buyer persona: Head of Doctoral Programs"
}
```

---

## Design Consistency

All new UI elements match existing design system:
- Font families: Inter (body), Geist (headings), JetBrains Mono (labels)
- Colors: blue (#2563eb) primary, gray scale from existing palette
- Card style: white background, subtle shadow, rounded corners (12px)
- Spacing: 32px padding, 16px gaps (consistent with chat/trace tabs)
- Buttons: match existing decision buttons (Yes/Adjust/Kill) in style
- Status badges: match existing session status badges in sidebar

No new design patterns introduced.
