# Multi-Agent AI System — CLAUDE.md

## Project State (as of 2026-05-26)

- **Phase:** 2 (active since May 19, 2026)
- **Phase 1:** Complete — L0/L1/L3 pipeline works, Telegram integration done
- **Phase 2:** Mother Agent + 9 child agents built, SPADE messaging, SimPy simulation
- **Current bug (FIXED):** Child agents escalating with `weak_evidence` because Claude via Bedrock returns non-JSON text that fails Pydantic validation. Fix: hardened SYSTEM_PROMPTs + `_parse_llm_response()` with markdown stripping and fallback defaults.

## Technology Stack

| Component         | Technology                    |
|-------------------|-------------------------------|
| Agent framework   | SPADE (spade.agent)           |
| LLM               | Claude (Bedrock) — Sonnet/Haiku |
| Canonical DB      | Supabase / Postgres           |
| Session memory    | Redis (Upstash)               |
| Simulation        | SimPy (Monte Carlo)           |
| MVP UI            | Streamlit                     |
| CEO channel       | Telegram (webhook)            |
| Messaging         | XMPP via SPADE                |

## Run Commands

```bash
pip install -r requirements.txt
streamlit run app.py
pytest tests/
python telegram/webhook.py
python main.py  # starts full Phase 2 pipeline
```

## Phase 2 Architecture

### Agent Files (all in `agents/phase2/`)

| Agent | File | Section | Model |
|-------|------|---------|-------|
| Mother Agent | `mother_agent.py` | orchestrator | Sonnet |
| Opportunity Analyst | `opportunity_analyst.py` | 1 | Sonnet |
| Environment Research | `environment_research.py` | 3 | Haiku |
| Organisation Designer | `organisation_designer.py` | 4 | Haiku |
| SWOT Synthesizer | `swot_synthesizer.py` | 5 | Sonnet |
| Marketing Strategy | `marketing_strategy.py` | 8 | Sonnet |
| Operations | `operations.py` | 10 | Haiku |
| Financial Modelling | `financial_modelling.py` | 12 | Sonnet |
| Launch & Contingency | `launch_contingency.py` | 13 | Haiku |
| Summary Agent | `summary_agent.py` | exec summary | Haiku |

### Schema Files

- Input schemas: `schemas/inputs/<agent_name>.py`
- Output schemas: `schemas/outputs/<agent_name>.py`
- All outputs use Pydantic BaseModel with `Literal` types for enums

### Key Patterns in Child Agents

Each child agent has:
1. `SYSTEM_PROMPT` — includes JSON-only instruction with exact field list
2. `ListenBehaviour` — SPADE CyclicBehaviour listening for messages
3. `handle_request()` — validates input, calls LLM, parses response, validates output
4. `_parse_llm_response()` — strips markdown code blocks, attempts JSON parse, falls back to schema-valid defaults
5. `_call_llm()` — calls Bedrock `invoke_model` API
6. `_escalate()` — sends escalation to Mother Agent

### LLM via AWS Bedrock

- Region: `us-east-1` (env: `AWS_BEDROCK_REGION`)
- Sonnet model: `claude-sonnet-4-20250514` (env: `CLAUDE_SONNET_MODEL`)
- Haiku model: `claude-haiku-4-5-20251001` (env: `CLAUDE_HAIKU_MODEL`)
- API: `bedrock.invoke_model(model=, max_tokens=, system=, messages=)`
- Response format: `response["body"].read()` → JSON with `content[0].text`

### SPADE Messaging Protocol

Performatives: `request`, `inform`, `escalate`, `propose`, `refuse`
Metadata fields: `performative`, `task_id`, `session_id`, `pipeline_run_id`

## Code Rules

- `snake_case` everywhere
- Never hardcode API keys — `.env` + `python-dotenv`
- Type hints on every function
- `logging` module only — never `print()`
- `Black` formatting, line length 88
- Every agent action writes to `events_logs`

## Known Issues / Watch Items

- Financial agent depends on `simulation/financial_sim.py` — ensure `run_simulation()` returns `runs_completed`, `probability_distribution`, `primary_risk_factor`
- Summary agent `executive_summary` field has min_length=200 — fallback text must meet this
- Bedrock `invoke_model` API uses positional kwargs — not the `converse` API
