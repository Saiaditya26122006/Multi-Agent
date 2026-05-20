# Main Pipeline Guide

## Overview

The `main.py` file is the central orchestrator that wires together all agents (L0, L1, L3) with Telegram integration into a complete end-to-end pipeline.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM MESSAGE                          │
│                           ↓                                  │
│                     ┌──────────┐                            │
│                     │    L0    │  Validate & Authenticate   │
│                     │  INPUT   │  Check Duplicates          │
│                     │  GUARD   │  Create/Get Session        │
│                     └──────────┘                            │
│                           ↓                                  │
│                    Valid? ───No──→ [Send Rejection]        │
│                       Yes ↓                                 │
│                     ┌──────────┐                            │
│                     │    L1    │  Generate Clarifying       │
│                     │ CLARITY  │  Question                  │
│                     │  AGENT   │  Create Assumption         │
│                     └──────────┘                            │
│                           ↓                                  │
│                  [Send Question to CEO]                     │
│                           ↓                                  │
│                   [Wait for Response]                       │
│                           ↓                                  │
│         ┌─────────────────┴────────────────────┐           │
│         │                                       │           │
│    Normal Response                    Decision Response    │
│         ↓                                       ↓           │
│   Process with L1                    ┌──────────────┐     │
│                                      │ Yes/Adjust/  │     │
│                                      │    Kill      │     │
│                                      └──────────────┘     │
│                                            ↓               │
│                                      Handle Decision      │
│                                                            │
└─────────────────────────────────────────────────────────────┘
```

## Startup Sequence

### 1. System Initialization
```python
python3 main.py
```

**What Happens**:
1. Loads environment variables from `.env`
2. Prints startup banner
3. Verifies required environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `GEMINI_API_KEY`
4. Tests database connection by calling `get_ceo_context()`
5. Displays CEO information
6. Starts Telegram polling

**Expected Output**:
```
============================================================
    MULTI-AGENT AI SYSTEM
    CEO Business Planning Assistant
============================================================
Started at: 2026-05-14 22:14:11
============================================================

[STARTUP] Verifying environment variables...
  ✓ SUPABASE_URL
  ✓ SUPABASE_ANON_KEY
  ✓ TELEGRAM_BOT_TOKEN
  ✓ GEMINI_API_KEY

[STARTUP] Verifying database connection...
  ✓ Connected to Supabase
  ✓ CEO: Alex Zamurko at EpistemicOS
  ✓ Chat ID: 8866294087

[STARTUP] System status: ✅ READY
============================================================

[SYSTEM] Starting Telegram polling...
[SYSTEM] Waiting for messages...
```

## Message Processing Pipeline

### Flow Diagram

```
Message Received
    ↓
┌───────────────────────────────────────────────┐
│ STEP 1: L0 INPUT GUARD                        │
├───────────────────────────────────────────────┤
│ • Validate sender (CEO check)                 │
│ • Check for duplicates                        │
│ • Get or create session                       │
│ • Log message and event                       │
└───────────────────────────────────────────────┘
    ↓
Valid? ─No→ Send rejection message → STOP
    Yes ↓
┌───────────────────────────────────────────────┐
│ STEP 2: CHECK SESSION STATE                   │
├───────────────────────────────────────────────┤
│ • Is state AWAITING_APPROVAL?                 │
│ • Is message "Yes", "Adjust", or "Kill"?      │
└───────────────────────────────────────────────┘
    ↓
Decision Response? ─Yes→ Handle Decision → DONE
    No ↓
┌───────────────────────────────────────────────┐
│ STEP 3: L1 CLARITY AGENT                      │
├───────────────────────────────────────────────┤
│ • Load CEO context                            │
│ • Load project state                          │
│ • Generate clarifying question                │
│ • Create assumption                           │
│ • Update session state                        │
└───────────────────────────────────────────────┘
    ↓
Send Question to CEO → DONE
```

## Session States

The system uses these session states to track conversation flow:

| State | Meaning | Next Action |
|-------|---------|-------------|
| `NEEDS_CLARIFICATION` | Waiting for CEO to answer questions | L1 generates more questions |
| `AWAITING_RESEARCH` | Ready for L2 to gather data | L2 Research Agent runs |
| `RESEARCH_RUNNING` | L2 is actively researching | Wait for completion |
| `AWAITING_APPROVAL` | L3 generated feedback, needs decision | CEO replies Yes/Adjust/Kill |
| `PAUSED` | Temporarily on hold | Resume later |
| `COMPLETED` | Finished, archived | Start new session |

## Decision Handling

When session state is `AWAITING_APPROVAL`, the CEO can respond with:

### Yes
```
CEO: Yes
```
**Actions**:
1. Update decision status to `approved`
2. Update business plan sections to `in_progress`
3. Set session state to `COMPLETED`
4. Send confirmation message

**Response**:
```
✅ Decision approved! Moving forward with the plan.

Session completed. Send a new message when you're ready.
```

### Adjust
```
CEO: Adjust
```
**Actions**:
1. Reset session state to `NEEDS_CLARIFICATION`
2. Allow CEO to provide more input

**Response**:
```
🔄 Got it. Let's adjust the approach.

What would you like to change?
```

### Kill
```
CEO: Kill
```
**Actions**:
1. Update decision status to `rejected`
2. Set session state to `COMPLETED`
3. Send confirmation message

**Response**:
```
🛑 Initiative stopped as requested.

Session completed. Send a new message when you're ready.
```

## Console Logging

The system provides detailed console output for monitoring:

### Example: Processing a Message

```
============================================================
[INCOMING] Message 12345 from chat 8866294087
[TEXT] I want to expand into Asia
============================================================

[L0] Validating message...
[L0] ✓ Message valid
[L0] Session: a5124a5d-6023-4dc4-951d-5d3cea448fa6
[L0] New session: False
[SESSION] Current state: NEEDS_CLARIFICATION

[L1] Generating clarifying question...
[L1] ✓ Question generated
[L1] Assumption: assumption_20260514_221500
[L1] ✓ Sent to CEO: Which countries in Asia are you targeting first?...

[PIPELINE] ✓ Message processed successfully
============================================================
```

### Example: Handling a Decision

```
============================================================
[INCOMING] Message 12346 from chat 8866294087
[TEXT] Yes
============================================================

[L0] Validating message...
[L0] ✓ Message valid
[SESSION] Current state: AWAITING_APPROVAL

[DECISION] Processing CEO response: Yes
[DECISION] Found decision: decision_20260514_220441
[DECISION] CEO approved - updating decision and BP section...
[DECISION] ✓ Approved and session completed

[PIPELINE] Completed decision handling
============================================================
```

## Error Handling

### Rejected Messages

If L0 rejects a message (unauthorized sender, duplicate, etc.):

```
[L0] ✗ Message rejected: Unauthorized sender
```

CEO receives:
```
❌ Unauthorized sender (chat_id: 9999999). Only CEO can send messages.
```

### Agent Errors

If an agent fails:

```
[L1] ✗ Error: Failed to generate question
```

CEO receives:
```
⚠️ Error processing your message. Please try again.
```

## Testing the Pipeline

### 1. Start the System
```bash
python3 main.py
```

### 2. Send Test Messages from Telegram

**Test 1: Basic Message**
```
Send: "I need help with marketing"
```

Expected:
- L0 validates
- L1 generates clarifying question
- You receive a question back

**Test 2: Decision Response**
First, ensure session is in `AWAITING_APPROVAL` state, then:
```
Send: "Yes"
```

Expected:
- Decision approved
- Session completed
- Confirmation message received

**Test 3: Adjustment Request**
When in `AWAITING_APPROVAL` state:
```
Send: "Adjust"
```

Expected:
- Session reset to `NEEDS_CLARIFICATION`
- Acknowledgment message received

## Environment Variables

Required in `.env`:

```bash
# Supabase (Database)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_TEST_CHAT_ID=your_chat_id

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key

# Redis (optional)
UPSTASH_REDIS_REST_URL=your_redis_url
UPSTASH_REDIS_REST_TOKEN=your_redis_token
```

## Stopping the System

Press `Ctrl+C` to gracefully shut down:

```
^C

[SYSTEM] Shutting down...
============================================================
Goodbye! 👋
============================================================
```

## Extending the Pipeline

### Adding L3 Auto-Trigger

Currently, L3 must be triggered manually. To auto-trigger when research is complete:

```python
# In handle_telegram_message, after L1:

# Check if research is complete
briefs = get_research_briefs_for_session(session_id)
if briefs and current_state == "RESEARCH_RUNNING":
    print("\n[L3] Research complete, generating feedback...")
    
    l3_result = generate_feedback(
        session_id=session_id,
        research_brief=briefs[0]
    )
    
    await send_message(chat_id, l3_result["telegram_message"])
    print("[L3] ✓ Feedback sent to CEO")
```

### Adding L2 Research Agent

When ready to integrate L2:

```python
# After L1, check if research is needed:

if current_state == "AWAITING_RESEARCH":
    print("\n[L2] Triggering research agent...")
    
    # Call L2 research agent here
    # l2_result = start_research(session_id, research_topic)
    
    print("[L2] ✓ Research started")
```

## Troubleshooting

### Bot Not Responding
- Check Telegram bot token is correct
- Verify bot is not blocked by CEO
- Ensure `.env` file is loaded

### Database Errors
- Verify Supabase URL and key
- Check network connectivity
- Ensure CEO context exists in database

### Agent Errors
- Check Gemini API key is valid
- Verify API quota not exceeded
- Review agent logs for specific errors

## Files Structure

```
multi-agent-system/
├── main.py                    ← Main pipeline (THIS FILE)
├── .env                       ← Environment variables
├── requirements.txt           ← Python dependencies
│
├── agents/
│   ├── l0_input_guard.py      ← Validation & auth
│   ├── l1_clarity_agent.py    ← Question generation
│   └── l3_feedback_agent.py   ← Feedback & decisions
│
├── memory/
│   ├── supabase_client.py     ← Database functions
│   ├── redis_client.py        ← Cache (optional)
│   └── session_manager.py     ← Session tracking
│
└── tools/
    ├── telegram_handler.py    ← Telegram integration
    └── logger.py              ← Logging utilities
```

## Quick Reference

### Start System
```bash
python3 main.py
```

### Stop System
```
Ctrl+C
```

### View Logs
System logs to console in real-time.

### Test Message Flow
1. Send message to bot
2. Watch console for pipeline execution
3. Check Telegram for response

---

**Status**: ✅ Production Ready  
**Last Updated**: 2026-05-14  
**Version**: 1.0
