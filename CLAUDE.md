# Multi-Agent AI System — CLAUDE.md

## Project Overview
An intern-built multi-agent AI system for a startup CEO. The CEO sends ideas via Telegram.
Agents parse input, ask clarifying questions, structure research briefs, and deliver
feedback summaries — all logged and persisted for future sessions.

- **Phase:** 1 (Foundation + Pipeline). Phase 2 starts May 19, 2026.
- **Project path:** `/home/saiaditya26122006/multi-agent-system`
- **Terminal:** WSL Ubuntu

## Technology Stack
| Component         | Technology              |
|-------------------|-------------------------|
| Agent framework   | Python + CrewAI         |
| LLM               | Gemini API (free tier)  |
| Canonical DB      | Supabase / Postgres     |
| Session memory    | Redis (Upstash)         |
| MVP UI            | Streamlit               |
| CEO channel       | Telegram (webhook)      |
| Dashboard         | Notion or Airtable      |

> ⚠️ Switch LLM to Anthropic Claude API when Phase 2 starts.

## Run Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Start Streamlit dashboard
streamlit run app.py

# Run tests
pytest tests/

# Start Telegram webhook listener
python telegram/webhook.py
```

## Folder Structure
```
/agents         → L0, L1, L3 agent logic (Phase 1 only)
/memory         → Redis session helpers + Supabase canonical helpers
/prompts        → Prompt templates for L0, L1, L3 (input + output schemas)
/db             → Supabase table schemas, migrations, enum definitions
/telegram       → Webhook setup, message routing, reply sender
/dashboard      → Airtable or Notion sync logic
/tests          → Unit tests only — one file per agent
/utils          → Shared helpers (logging, error handling, formatters)
```

## Agent Layers (Phase 1: L0–L3 only)
| Layer | Agent          | Responsibility                                                  |
|-------|----------------|-----------------------------------------------------------------|
| L0    | Input Guard    | Validate sender, deduplicate via telegram_message_id, route    |
| L1    | Clarity Agent  | Ask ≤3 clarifying questions, structure idea into brief         |
| L2    | Research       | Manual in Phase 1 — CEO provides or skips                      |
| L3    | Feedback Agent | Send plain-language summary + decision request via Telegram    |
| L4–L6 | —             | Phase 2 only — do not build                                    |

## Memory Architecture
| Tier | Storage  | Written by       | What lives here                          |
|------|----------|------------------|------------------------------------------|
| T1   | Supabase | Team (Phase 1)   | CEO canonical profile (5 fields)         |
| T2   | Redis    | Any agent        | Active session state — expires on end    |
| T3   | Supabase | Team manually    | Research briefs, summaries, outputs      |
| T4   | Supabase | Team (Phase 1)   | Assumptions + Decisions Registry         |

**Write discipline:** In Phase 2, only L4 (Mother Agent) writes to T1 and T4.
In Phase 1, the team writes these manually. No agent should write to canonical
tables without explicit permission rules in place.

## Database Tables (Supabase)
11 tables: `messages`, `sessions`, `profiles`, `business_plan_sections`,
`research_briefs`, `assumptions`, `decisions`, `next_actions`, `agent_outputs`,
`sources`, `events_logs`

- All status fields → Postgres enum types (no free-text status values)
- All linked fields → foreign key constraints
- `messages.telegram_message_id` → unique constraint (deduplication)
- `sessions.state` → enum, required for state machine
- `decisions.version` → integer, starts at 1, increments on each revision

## Workflow States
```
NEEDS_CLARIFICATION → AWAITING_RESEARCH → RESEARCH_RUNNING
→ AWAITING_FEEDBACK → AWAITING_APPROVAL → COMPLETED
                                         ↘ PAUSED (no CEO response)
```
If CEO does not respond: set state to PAUSED, notify via Telegram,
tag affected assumptions as `assumed_not_clarified`. Never auto-proceed.

## Code Rules
- Use `snake_case` for all variables and function names
- Never hardcode API keys — use `.env` + `python-dotenv`
- Always add type hints to every function
- Use `logging` — never `print()` for debugging
- Write docstrings for every agent function and helper
- Use `Black` for formatting (line length: 88)
- Every agent action must write to `events_logs` — no silent operations

## Agent Output Rules
- Every prompt template has a defined input schema and output schema
- L1 asks one question at a time — hard max of 3 per session
- L3 sends exactly: one-paragraph summary + biggest risk + one decision question
- CEO reply options are always: `Yes / Adjust / Kill` — nothing else
- On `Adjust`: L1 re-processes, increments `decisions.version`, loops back to L3
- On `Kill`: archive session, log rejected decision with CEO reasoning

## Error Handling Rules
- API failure → stop cleanly, notify CEO via Telegram, log to `events_logs`
- CEO no response → set state to PAUSED, notify, do not auto-proceed
- Redis TTL fires → write last session state to `sessions.archived_state` in Supabase before expiry
- No silent failures anywhere — every caught exception must be logged

## What NOT to Build in Phase 1
- L4 Mother Agent — Phase 2
- L5 Execution Agents (GTM, Finance, HR, Risk, Market) — Phase 2
- L6 Monitor / Digest — Phase 2
- Escalation or revision-loop limits — Phase 2 (observe first, govern later)
- Vector memory or semantic search — Phase 2/3
- Slack integration — Phase 2 if needed
- Notion/Linear task creation — Phase 2
- Auto-assumption escalation (24hr timer) — Phase 2

## Phase 1 Completion Checklist
- [ ] CEO sends Telegram message → system identifies, loads profile, routes
- [ ] Clarity questions asked (max 3, skips known fields)
- [ ] Research brief structured and stored in Supabase, linked to BP section
- [ ] Feedback summary sent via Telegram with key risks + decision question
- [ ] CEO approves / adjusts / kills — pipeline moves correctly
- [ ] Decision object created with rationale, version, linked assumptions
- [ ] All assumptions logged with confidence level + clarification status
- [ ] Every action in `events_logs` with full timestamp + state transitions
- [ ] Dashboard shows live: BP sections, decisions, assumptions, next actions, alerts
- [ ] System remembers context when CEO messages again in a new session
