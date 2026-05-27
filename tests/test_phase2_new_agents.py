"""
End-to-end tests for Phase 2 new agents:
- Intelligence Engine (4-step reasoning)
- Devil's Advocate (adversarial review)
- Learning Engine (feedback loop)
- Document Compiler (JSON → Markdown)
- Full orchestration flow (Mother → child → DA → calibrate → deliver)

Mocks Bedrock, Redis, and Supabase — no external dependencies.
"""
import sys
import json
import asyncio
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Mock infrastructure
# ─────────────────────────────────────────────────────────────────────────────

class MockRedisClient:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        self._store.pop(key, None)

    def keys(self, pattern="*"):
        return list(self._store.keys())

    def exists(self, key):
        return 1 if key in self._store else 0

    def scan(self, cursor=0, match="*", count=50):
        import fnmatch
        matched = [k for k in self._store.keys() if fnmatch.fnmatch(k, match)]
        return (0, matched)


class MockSupabaseTable:
    def __init__(self):
        self._data = []

    def insert(self, data):
        self._data.append(data)
        return self

    def select(self, *args):
        return self

    def eq(self, *args):
        return self

    def neq(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def update(self, *args):
        return self

    def in_(self, *args):
        return self

    def execute(self):
        result = MagicMock()
        result.data = self._data if self._data else [{"id": "mock-id-123"}]
        return result


class MockSupabaseClient:
    def __init__(self):
        self._tables = {}

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MockSupabaseTable()
        return self._tables[name]


# ─────────────────────────────────────────────────────────────────────────────
# Patch modules
# ─────────────────────────────────────────────────────────────────────────────

mock_redis = MockRedisClient()
mock_supabase = MockSupabaseClient()

import os
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://fake.upstash.io")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "fake-token")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake-anon-key")
os.environ.setdefault("AWS_BEDROCK_REGION", "us-east-1")
os.environ.setdefault("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
os.environ.setdefault("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

sys.modules["upstash_redis"] = MagicMock()
sys.modules["upstash_redis"].Redis = lambda **kwargs: mock_redis

mock_supabase_module = MagicMock()
mock_supabase_module.create_client = lambda url, key: mock_supabase
mock_supabase_module.Client = MagicMock
sys.modules["supabase"] = mock_supabase_module

import memory.redis_client as redis_mod
redis_mod.redis_client = mock_redis
redis_mod.RedisClient = lambda: MagicMock(client=mock_redis)

import memory.supabase_client as supa_mod
supa_mod.supabase = mock_supabase

import types

class FakeAgent:
    def __init__(self, jid=None, password=None, *args, **kwargs):
        self.jid = jid
    async def start(self, *args, **kwargs): pass
    async def stop(self): pass
    def add_behaviour(self, b): pass
    def is_alive(self): return False

class FakeBehaviour:
    def __init__(self, *args, **kwargs): pass
    async def run(self): pass
    async def send(self, msg): pass
    async def receive(self, timeout=None): return None
    async def join(self, timeout=None): pass

class FakeMessage:
    def __init__(self, to=None):
        self.to = to
        self.body = ""
        self._metadata = {}
    def set_metadata(self, key, value):
        self._metadata[key] = value
    def get_metadata(self, key):
        return self._metadata.get(key)

spade_mod = types.ModuleType("spade")
spade_agent_mod = types.ModuleType("spade.agent")
spade_behaviour_mod = types.ModuleType("spade.behaviour")
spade_message_mod = types.ModuleType("spade.message")

spade_agent_mod.Agent = FakeAgent
spade_behaviour_mod.CyclicBehaviour = FakeBehaviour
spade_behaviour_mod.OneShotBehaviour = FakeBehaviour
spade_behaviour_mod.PeriodicBehaviour = FakeBehaviour
spade_message_mod.Message = FakeMessage

sys.modules["spade"] = spade_mod
sys.modules["spade.agent"] = spade_agent_mod
sys.modules["spade.behaviour"] = spade_behaviour_mod
sys.modules["spade.message"] = spade_message_mod

mock_boto3 = MagicMock()
sys.modules["boto3"] = mock_boto3


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_bedrock_response(text: str) -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 100, "outputTokens": 200},
    }


def make_bedrock_json_response(data: dict) -> dict:
    return make_bedrock_response(json.dumps(data))


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Intelligence Engine — reason_and_produce
# ─────────────────────────────────────────────────────────────────────────────

VALID_SECTION_OUTPUT = {
    "section_number": "1",
    "opportunity_description": "B2B SaaS equity management platform for seed-stage startups",
    "competitive_strategy": "Simpler, cheaper alternative to Carta for early-stage",
    "objectives": [{"objective": "10 paying customers", "metric": "customers", "target_value": "10", "timeframe": "6 months"}],
    "icp_hypothesis": {"buyer_role": "Founder/CEO", "budget_process": "Founder discretion", "decision_timeline": "1-2 weeks", "pain_points": ["Complex spreadsheets"]},
    "assumptions_used": [{"statement": "Seed startups need simpler tooling", "confidence": "high", "source": "alex_provided", "source_detail": "CEO Q&A"}],
    "uncertainties": ["Willingness to pay at seed stage"],
    "confidence_score": "high",
    "input_tokens": 0,
    "output_tokens": 0,
}


def test_intelligence_engine_reason_and_produce():
    """Intelligence Engine runs 4-step reasoning chain and returns parsed output."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    call_count = [0]

    def mock_converse(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            return make_bedrock_response("Step analysis: key decisions identified...")
        elif call_count[0] == 3:
            return make_bedrock_response("Challenge: some overconfidence noted...")
        else:
            return make_bedrock_json_response(VALID_SECTION_OUTPUT)

    mock_bedrock.converse = mock_converse
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "claude-sonnet-4-20250514")

    result, trace, usage = asyncio.run(engine.reason_and_produce(
        agent_role="Opportunity Analyst",
        input_data={"idea_summary": "B2B SaaS for equity management", "business_type": "saas"},
        output_schema_prompt="Return JSON with section_number, opportunity_description, ...",
        reasoning_budget=3,
    ))

    assert result is not None, "Expected parsed output"
    assert result["section_number"] == "1"
    assert trace["revisions_applied"] is True
    assert trace["reasoning_budget"] == 3
    assert usage["input_tokens"] > 0
    assert call_count[0] == 4  # decompose, produce, challenge, revise
    logger.info("PASS: test_intelligence_engine_reason_and_produce")
    return True


def test_intelligence_engine_budget_2_skips_challenge():
    """With reasoning_budget=2, challenge and revise steps are skipped."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    call_count = [0]

    def mock_converse(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return make_bedrock_response("Decomposition complete")
        else:
            return make_bedrock_json_response(VALID_SECTION_OUTPUT)

    mock_bedrock.converse = mock_converse
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test-model")

    result, trace, usage = asyncio.run(engine.reason_and_produce(
        agent_role="Test Agent",
        input_data={"test": True},
        output_schema_prompt="Return JSON",
        reasoning_budget=2,
    ))

    assert result is not None
    assert trace["revisions_applied"] is False
    assert call_count[0] == 2  # only decompose + produce
    logger.info("PASS: test_intelligence_engine_budget_2_skips_challenge")
    return True


def test_intelligence_engine_constraints_injection():
    """Hard constraints, confidence ceiling, and uncertainties are injected into prompts."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    captured_prompts = []
    mock_bedrock = MagicMock()

    def mock_converse(**kwargs):
        user_text = kwargs["messages"][0]["content"][0]["text"]
        captured_prompts.append(user_text)
        return make_bedrock_json_response(VALID_SECTION_OUTPUT)

    mock_bedrock.converse = mock_converse
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test-model")

    input_data = {
        "test": True,
        "hard_constraints": {
            "price_per_unit": {"value": 99, "source": "Section 8"},
            "headcount_year1": {"value": 5, "source": "Section 4"},
        },
        "confidence_ceiling": "medium",
        "upstream_uncertainties": [
            {"from_section": "1", "uncertainty": "Market size unknown"},
        ],
        "ceo_provided_data": {"revenue_target": "$1M ARR"},
    }

    asyncio.run(engine.reason_and_produce(
        agent_role="Financial Modelling",
        input_data=input_data,
        output_schema_prompt="Return JSON",
        reasoning_budget=2,
    ))

    decompose_prompt = captured_prompts[0]
    assert "BINDING CONSTRAINTS" in decompose_prompt
    assert "price_per_unit = 99" in decompose_prompt
    assert "CONFIDENCE CEILING" in decompose_prompt
    assert "medium" in decompose_prompt
    assert "UPSTREAM UNCERTAINTIES" in decompose_prompt
    assert "Market size unknown" in decompose_prompt
    assert "CEO-PROVIDED DATA" in decompose_prompt
    assert "$1M ARR" in decompose_prompt
    logger.info("PASS: test_intelligence_engine_constraints_injection")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Intelligence Engine — calibrate_confidence
# ─────────────────────────────────────────────────────────────────────────────

def test_calibrate_confidence_high_severity():
    """2+ high-severity challenges → confidence drops to 'low'."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    engine = IntelligenceEngine(MagicMock(), "test")
    section_output = {"confidence_score": "high"}
    da_result = {
        "verdict": "revise",
        "challenges": [
            {"severity": "high", "claim": "x"},
            {"severity": "high", "claim": "y"},
        ],
        "recommended_confidence": "low",
    }

    result = asyncio.run(engine.calibrate_confidence(section_output, da_result))
    assert result == "low"
    logger.info("PASS: test_calibrate_confidence_high_severity")
    return True


def test_calibrate_confidence_one_high():
    """1 high-severity challenge + 'high' confidence → downgrades to 'medium'."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    engine = IntelligenceEngine(MagicMock(), "test")
    section_output = {"confidence_score": "high"}
    da_result = {
        "verdict": "revise",
        "challenges": [{"severity": "high", "claim": "x"}],
        "recommended_confidence": "medium",
    }

    result = asyncio.run(engine.calibrate_confidence(section_output, da_result))
    assert result == "medium"
    logger.info("PASS: test_calibrate_confidence_one_high")
    return True


def test_calibrate_confidence_pass_no_change():
    """DA passes with no challenges → confidence unchanged."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    engine = IntelligenceEngine(MagicMock(), "test")
    section_output = {"confidence_score": "high"}
    da_result = {"verdict": "pass", "challenges": [], "recommended_confidence": "high"}

    result = asyncio.run(engine.calibrate_confidence(section_output, da_result))
    assert result == "high"
    logger.info("PASS: test_calibrate_confidence_pass_no_change")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Intelligence Engine — so_what_filter
# ─────────────────────────────────────────────────────────────────────────────

def test_so_what_filter_pass():
    """Section passes so-what filter when LLM returns 'PASS'."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response("PASS")
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test")
    result = asyncio.run(engine.apply_so_what_filter(VALID_SECTION_OUTPUT, "Opportunity Analyst"))
    assert result is None  # None means passed
    logger.info("PASS: test_so_what_filter_pass")
    return True


def test_so_what_filter_fail():
    """Section fails so-what filter when LLM returns critique."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response(
        "FAIL: This section uses only generic market language with no actionable specifics"
    )
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test")
    result = asyncio.run(engine.apply_so_what_filter(VALID_SECTION_OUTPUT, "Opportunity Analyst"))
    assert result is not None
    assert "FAIL:" in result
    logger.info("PASS: test_so_what_filter_fail")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Intelligence Engine — validate_hypotheses
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_hypotheses_all_pass():
    """No failures returned when LLM says all hypotheses pass."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response("[]")
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test")
    output = {**VALID_SECTION_OUTPUT, "revenue_year1": 60000, "cac": 500, "ltv": 3000}
    result = asyncio.run(engine.validate_hypotheses(output, "Financial Modelling"))
    assert result == []
    logger.info("PASS: test_validate_hypotheses_all_pass")
    return True


def test_validate_hypotheses_failures_returned():
    """Failed hypotheses are returned as list."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    failures = [
        {"hypothesis": "funnel_math", "result": "fail", "explanation": "25k leads needed but only 5k addressable", "numbers_involved": "500 sales / 2% = 25000"},
        {"hypothesis": "unit_economics", "result": "pass", "explanation": "LTV/CAC ratio is 6:1", "numbers_involved": "3000/500"},
    ]
    mock_bedrock.converse.return_value = make_bedrock_response(json.dumps(failures))
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("ModelTimeoutException", (Exception,), {})

    engine = IntelligenceEngine(mock_bedrock, "test")
    output = {**VALID_SECTION_OUTPUT, "revenue_year1": 60000, "volume": 500, "conversion_rate": 0.02}
    result = asyncio.run(engine.validate_hypotheses(output, "Marketing Strategy"))
    assert len(result) == 1
    assert result[0]["hypothesis"] == "funnel_math"
    assert result[0]["result"] == "fail"
    logger.info("PASS: test_validate_hypotheses_failures_returned")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Devil's Advocate
# ─────────────────────────────────────────────────────────────────────────────

VALID_DA_OUTPUT = {
    "verdict": "revise",
    "challenges": [
        {
            "claim": "Market size of 50k seed startups",
            "challenge_type": "overconfidence",
            "severity": "medium",
            "explanation": "No source cited for 50k number — could be significantly lower in addressable market",
            "suggested_fix": "Cite source or downgrade to 'agent_inferred' with low confidence",
            "section_reference": None,
        },
        {
            "claim": "1-2 week decision timeline",
            "challenge_type": "unsupported",
            "severity": "low",
            "explanation": "Decision timeline is assumed without customer research evidence",
            "suggested_fix": "Mark as hypothesis requiring validation",
            "section_reference": None,
        },
    ],
    "confidence_assessment": "inflated",
    "recommended_confidence": "medium",
    "assumptions_grade": "mixed",
    "overall_reasoning_quality": "adequate",
    "summary": "Section has reasonable opportunity analysis but overestimates market confidence without adequate sourcing. Two medium-severity issues found: unsourced market size claim and assumed decision timeline. Recommend revision to downgrade unsupported confidence labels.",
    "input_tokens": 0,
    "output_tokens": 0,
}


def test_devils_advocate_valid_response():
    """DA agent handles valid JSON response and validates output."""
    from agents.phase2.devils_advocate import DevilsAdvocateAgent
    from schemas.outputs.devils_advocate import DevilsAdvocateOutput

    agent = object.__new__(DevilsAdvocateAgent)
    agent.model_id = "claude-sonnet-4-20250514"
    agent.redis = MagicMock(client=mock_redis)

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_json_response(VALID_DA_OUTPUT)
    agent.bedrock = mock_bedrock

    from schemas.inputs.devils_advocate import DevilsAdvocateInput
    inp = DevilsAdvocateInput(
        task_id="da-001",
        session_id="sess-001",
        pipeline_run_id="run-001",
        section_number="1",
        section_output=VALID_SECTION_OUTPUT,
        reasoning_trace={"decomposition": "test"},
        cross_section_context={},
    )

    prompt = agent._build_prompt(inp)
    assert "Challenge this business plan section" in prompt
    assert "Section 1" in prompt or "SECTION NUMBER: 1" in prompt

    raw = mock_bedrock.converse(
        modelId=agent.model_id,
        system=[{"text": "test"}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096},
    )["output"]["message"]["content"][0]["text"]

    parsed = agent._parse_response(raw)
    assert parsed is not None
    parsed["task_id"] = "da-001"
    parsed["section_number"] = "1"
    parsed["model_used"] = agent.model_id

    validated = DevilsAdvocateOutput(**parsed)
    assert validated.verdict == "revise"
    assert len(validated.challenges) == 2
    assert validated.confidence_assessment == "inflated"
    assert validated.recommended_confidence == "medium"
    logger.info("PASS: test_devils_advocate_valid_response")
    return True


def test_devils_advocate_fallback_pass():
    """DA agent returns fallback pass when LLM fails."""
    from agents.phase2.devils_advocate import DevilsAdvocateAgent
    from schemas.outputs.devils_advocate import DevilsAdvocateOutput

    agent = object.__new__(DevilsAdvocateAgent)
    agent.model_id = "claude-sonnet-4-20250514"

    fallback = agent._fallback_pass("da-002")
    validated = DevilsAdvocateOutput(**fallback)
    assert validated.verdict == "pass"
    assert len(validated.challenges) == 0
    assert "review recommended" in validated.summary.lower() or "could not be completed" in validated.summary.lower()
    logger.info("PASS: test_devils_advocate_fallback_pass")
    return True


def test_devils_advocate_cross_section_prompt():
    """DA prompt includes cross-section context for contradiction checking."""
    from agents.phase2.devils_advocate import DevilsAdvocateAgent
    from schemas.inputs.devils_advocate import DevilsAdvocateInput

    agent = object.__new__(DevilsAdvocateAgent)
    agent.model_id = "test"

    inp = DevilsAdvocateInput(
        task_id="da-003",
        session_id="sess-001",
        pipeline_run_id="run-001",
        section_number="8",
        section_output={"marketing_budget": 50000, "confidence_score": "high"},
        cross_section_context={
            "12": {"revenue_assumptions": {"year1_revenue": 30000}, "confidence_score": "low"},
        },
    )

    prompt = agent._build_prompt(inp)
    assert "OTHER COMPLETED SECTIONS" in prompt
    assert "Section 12" in prompt
    assert "30000" in prompt
    logger.info("PASS: test_devils_advocate_cross_section_prompt")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Learning Engine
# ─────────────────────────────────────────────────────────────────────────────

def test_learning_engine_record_acceptance():
    """Learning Engine records acceptance and retrieves it."""
    from agents.phase2.learning_engine import LearningEngine

    redis = MagicMock(client=MockRedisClient())
    engine = LearningEngine(redis)

    engine.record_acceptance(
        session_id="sess-001",
        section_number="1",
        confidence_score="high",
        assumptions_count=3,
        devils_advocate_verdict="pass",
    )

    key = "learning:pattern:sess-001:1"
    raw = redis.client.get(key)
    assert raw is not None
    record = json.loads(raw)
    assert record["event"] == "accepted"
    assert record["section"] == "1"
    assert record["confidence"] == "high"
    logger.info("PASS: test_learning_engine_record_acceptance")
    return True


def test_learning_engine_record_rejection():
    """Learning Engine records rejection with reason and CEO feedback."""
    from agents.phase2.learning_engine import LearningEngine

    redis = MagicMock(client=MockRedisClient())
    engine = LearningEngine(redis)

    engine.record_rejection(
        session_id="sess-002",
        section_number="8",
        reason="Marketing budget unrealistic for stated revenue",
        ceo_feedback="We cannot spend $50k on marketing in year 1 when revenue is only $30k",
    )

    key = "learning:rejection:sess-002:8"
    raw = redis.client.get(key)
    assert raw is not None
    record = json.loads(raw)
    assert record["event"] == "rejected"
    assert "unrealistic" in record["reason"]
    assert "$50k" in record["ceo_feedback"]
    logger.info("PASS: test_learning_engine_record_rejection")
    return True


def test_learning_engine_build_context():
    """build_learning_context produces prompt-injectable text from past failures."""
    from agents.phase2.learning_engine import LearningEngine

    redis_client = MockRedisClient()
    redis = MagicMock(client=redis_client)
    engine = LearningEngine(redis)

    # Seed rejection data (get_section_history scans rejection keys)
    rejection = json.dumps({
        "event": "rejected",
        "section": "8",
        "reason": "Budget too high",
        "ceo_feedback": "Cut it in half",
        "timestamp": "2026-05-25T10:00:00Z",
        "session_id": "old-session",
    })
    redis_client.set("learning:rejection:old-session:8", rejection)

    # Seed a second rejection from another session
    rejection2 = json.dumps({
        "event": "rejected",
        "section": "8",
        "reason": "Target audience too broad",
        "ceo_feedback": "Focus on seed-stage only",
        "timestamp": "2026-05-24T09:00:00Z",
        "session_id": "older-session",
    })
    redis_client.set("learning:rejection:older-session:8", rejection2)

    context = engine.build_learning_context("8")
    assert "LEARNING FROM PAST RUNS" in context
    assert "REJECTED" in context
    assert "Budget too high" in context
    assert "Cut it in half" in context
    logger.info("PASS: test_learning_engine_build_context")
    return True


def test_learning_engine_no_failures_empty_context():
    """build_learning_context returns empty string when no failures exist."""
    from agents.phase2.learning_engine import LearningEngine

    redis = MagicMock(client=MockRedisClient())
    engine = LearningEngine(redis)

    context = engine.build_learning_context("99")
    assert context == ""
    logger.info("PASS: test_learning_engine_no_failures_empty_context")
    return True


def test_learning_engine_da_accuracy():
    """DA accuracy tracking stores and retrieves stats."""
    from agents.phase2.learning_engine import LearningEngine

    redis_client = MockRedisClient()
    redis = MagicMock(client=redis_client)
    engine = LearningEngine(redis)

    # Use different session/section combos so keys don't overwrite each other
    engine.record_da_accuracy("sess-001", "1", "overconfidence", True)
    engine.record_da_accuracy("sess-002", "1", "overconfidence", True)
    engine.record_da_accuracy("sess-003", "3", "overconfidence", False)
    engine.record_da_accuracy("sess-001", "8", "math_error", True)

    stats = engine.get_da_accuracy_stats()
    assert "overconfidence" in stats
    assert stats["overconfidence"]["total"] == 3
    assert stats["overconfidence"]["valid"] == 2
    assert abs(stats["overconfidence"]["accuracy"] - 2 / 3) < 0.01
    assert stats["math_error"]["accuracy"] == 1.0
    logger.info("PASS: test_learning_engine_da_accuracy")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Document Compiler
# ─────────────────────────────────────────────────────────────────────────────

def test_document_compiler_full_compile():
    """Document Compiler produces Markdown from all section outputs."""
    from agents.phase2.document_compiler import DocumentCompiler

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response(
        "The business targets seed-stage startups with a simplified equity management tool, "
        "offering competitive pricing at $99/month versus enterprise solutions."
    )

    compiler = DocumentCompiler(mock_bedrock, "test-model")

    all_outputs = {
        "1": VALID_SECTION_OUTPUT,
        "3": {"pest_analysis": [{"category": "economic", "factor": "VC boom", "impact": "positive", "relevance": "high"}],
               "five_forces": [], "market_context": "Growing market", "confidence_score": "medium",
               "assumptions_used": [{"statement": "Market growing", "confidence": "medium", "source": "agent_inferred"}]},
    }

    result = asyncio.run(compiler.compile(all_outputs, "EquiTrack"))
    assert "# Business Plan: EquiTrack" in result
    assert "## The Opportunity" in result or "seed-stage startups" in result
    assert mock_bedrock.converse.call_count >= 2  # at least 2 sections compiled
    logger.info("PASS: test_document_compiler_full_compile")
    return True


def test_document_compiler_fallback_on_llm_failure():
    """Compiler falls back to key-value rendering when LLM fails."""
    from agents.phase2.document_compiler import DocumentCompiler

    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = Exception("LLM unavailable")

    compiler = DocumentCompiler(mock_bedrock, "test-model")

    all_outputs = {"1": VALID_SECTION_OUTPUT}
    result = asyncio.run(compiler.compile(all_outputs, "TestBiz"))

    assert "# Business Plan: TestBiz" in result
    assert "Opportunity Description" in result or "opportunity_description" in result.lower()
    assert "B2B SaaS" in result
    logger.info("PASS: test_document_compiler_fallback_on_llm_failure")
    return True


def test_document_compiler_assumptions_appendix():
    """Compiler generates assumptions appendix from all sections."""
    from agents.phase2.document_compiler import DocumentCompiler

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response("Section content here.")

    compiler = DocumentCompiler(mock_bedrock, "test-model")

    all_outputs = {
        "1": {**VALID_SECTION_OUTPUT},
        "3": {"assumptions_used": [{"statement": "Market growing fast", "confidence": "low", "source": "assumed"}],
               "confidence_score": "low"},
    }

    result = asyncio.run(compiler.compile(all_outputs, "TestBiz"))
    assert "Assumptions Registry" in result
    assert "Seed startups need simpler tooling" in result
    assert "Market growing fast" in result
    assert "require validation" in result.lower()
    logger.info("PASS: test_document_compiler_assumptions_appendix")
    return True


def test_document_compiler_quality_appendix():
    """Compiler generates quality appendix with confidence distribution."""
    from agents.phase2.document_compiler import DocumentCompiler

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response("Section.")

    compiler = DocumentCompiler(mock_bedrock, "test-model")

    all_outputs = {
        "1": {**VALID_SECTION_OUTPUT, "_da_verdict": "pass"},
        "3": {"confidence_score": "low", "_da_verdict": "revise"},
        "8": {"confidence_score": "medium", "_da_verdict": "pass"},
    }
    coherence_audit = {
        "issues": [{"severity": "medium", "description": "Revenue assumptions differ between S8 and S12"}],
        "overall_plan_confidence": "medium",
    }

    result = asyncio.run(compiler.compile(all_outputs, "TestBiz", coherence_audit))
    assert "Data Quality" in result
    assert "High: 1" in result
    assert "Medium: 1" in result
    assert "Low: 1" in result
    assert "Passed: 2" in result
    assert "Revised: 1" in result
    assert "Coherence Audit" in result
    assert "Revenue assumptions differ" in result
    logger.info("PASS: test_document_compiler_quality_appendix")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST: Full orchestration flow
# ─────────────────────────────────────────────────────────────────────────────

def test_full_orchestration_flow():
    """Full pipeline: child produces → DA reviews → confidence calibrated → learning recorded."""
    from agents.phase2.intelligence_engine import IntelligenceEngine
    from agents.phase2.learning_engine import LearningEngine

    # 1. Child agent produces output via IntelligenceEngine
    mock_bedrock = MagicMock()
    call_idx = [0]

    def mock_converse(**kwargs):
        call_idx[0] += 1
        if call_idx[0] <= 3:
            return make_bedrock_response("Reasoning step output")
        return make_bedrock_json_response(VALID_SECTION_OUTPUT)

    mock_bedrock.converse = mock_converse
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("T", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("M", (Exception,), {})

    intel = IntelligenceEngine(mock_bedrock, "test")
    output, trace, usage = asyncio.run(intel.reason_and_produce(
        agent_role="Opportunity Analyst",
        input_data={"idea_summary": "SaaS equity platform"},
        output_schema_prompt="Return JSON",
        reasoning_budget=3,
    ))
    assert output is not None

    # 2. DA reviews the output
    da_result = VALID_DA_OUTPUT  # "revise" verdict with 2 challenges

    # 3. Confidence calibration
    calibrated = asyncio.run(intel.calibrate_confidence(output, da_result))
    assert calibrated in ("high", "medium", "low")

    # 4. Learning Engine records the result
    redis = MagicMock(client=MockRedisClient())
    learning = LearningEngine(redis)
    learning.record_acceptance(
        session_id="sess-test",
        section_number="1",
        confidence_score=calibrated,
        assumptions_count=len(output.get("assumptions_used", [])),
        devils_advocate_verdict=da_result["verdict"],
    )

    key = "learning:pattern:sess-test:1"
    raw = redis.client.get(key)
    record = json.loads(raw)
    assert record["event"] == "accepted"
    assert record["da_verdict"] == "revise"

    logger.info("PASS: test_full_orchestration_flow")
    return True


def test_revision_loop_flow():
    """DA revise verdict → agent re-dispatched with feedback → produces revised output."""
    from agents.phase2.intelligence_engine import IntelligenceEngine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_json_response({
        **VALID_SECTION_OUTPUT,
        "confidence_score": "medium",  # downgraded after revision
    })
    mock_bedrock.exceptions = MagicMock()
    mock_bedrock.exceptions.ThrottlingException = type("T", (Exception,), {})
    mock_bedrock.exceptions.ModelTimeoutException = type("M", (Exception,), {})

    intel = IntelligenceEngine(mock_bedrock, "test")

    # Simulate re-dispatch with revision feedback
    revision_feedback = (
        "MANDATORY REVISIONS (from quality review):\n"
        "- Downgrade market size confidence to 'low' — no source cited\n"
        "- Mark decision timeline as hypothesis requiring validation\n"
        "Fix these issues. Do NOT weaken your analysis — make it more rigorous."
    )

    result, trace, _ = asyncio.run(intel.reason_and_produce(
        agent_role="Opportunity Analyst",
        input_data={"idea_summary": "SaaS equity platform"},
        output_schema_prompt="Return JSON",
        reasoning_budget=4,  # +1 for revision
        learning_context=revision_feedback,
    ))

    assert result is not None
    assert result["confidence_score"] == "medium"
    # Verify the revision feedback was in the prompt
    call_args = mock_bedrock.converse.call_args_list[0]
    user_text = call_args[1]["messages"][0]["content"][0]["text"]
    assert "MANDATORY REVISIONS" in user_text
    logger.info("PASS: test_revision_loop_flow")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    tests = [
        # Intelligence Engine
        test_intelligence_engine_reason_and_produce,
        test_intelligence_engine_budget_2_skips_challenge,
        test_intelligence_engine_constraints_injection,
        test_calibrate_confidence_high_severity,
        test_calibrate_confidence_one_high,
        test_calibrate_confidence_pass_no_change,
        test_so_what_filter_pass,
        test_so_what_filter_fail,
        test_validate_hypotheses_all_pass,
        test_validate_hypotheses_failures_returned,
        # Devil's Advocate
        test_devils_advocate_valid_response,
        test_devils_advocate_fallback_pass,
        test_devils_advocate_cross_section_prompt,
        # Learning Engine
        test_learning_engine_record_acceptance,
        test_learning_engine_record_rejection,
        test_learning_engine_build_context,
        test_learning_engine_no_failures_empty_context,
        test_learning_engine_da_accuracy,
        # Document Compiler
        test_document_compiler_full_compile,
        test_document_compiler_fallback_on_llm_failure,
        test_document_compiler_assumptions_appendix,
        test_document_compiler_quality_appendix,
        # Full orchestration
        test_full_orchestration_flow,
        test_revision_loop_flow,
    ]

    logger.info("=" * 70)
    logger.info("PHASE 2 NEW AGENTS — END-TO-END TEST SUITE")
    logger.info("=" * 70)
    logger.info("")

    passed = 0
    failed = 0
    failures = []

    for test_fn in tests:
        try:
            result = test_fn()
            if result:
                passed += 1
            else:
                failed += 1
                failures.append(test_fn.__name__)
        except Exception as e:
            failed += 1
            failures.append(f"{test_fn.__name__}: {e}")
            logger.error("FAIL: %s — %s", test_fn.__name__, e)
            import traceback
            traceback.print_exc()

    logger.info("")
    logger.info("=" * 70)
    if failed == 0:
        logger.info("RESULT: ALL %d TESTS PASSED", passed)
    else:
        logger.error("RESULT: %d PASSED, %d FAILED", passed, failed)
        for f in failures:
            logger.error("  - %s", f)
    logger.info("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
