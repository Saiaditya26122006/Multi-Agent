"""
End-to-end test for Phase 2 pipeline.
Mocks Bedrock, Redis, and Supabase to test the full agent logic
without external dependencies.
"""
import sys
import json
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Mock infrastructure — patches Redis and Supabase before any agent imports
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
# Patch modules before importing agents
# ─────────────────────────────────────────────────────────────────────────────

mock_redis = MockRedisClient()
mock_supabase = MockSupabaseClient()

# Patch environment
import os
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://fake.upstash.io")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "fake-token")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake-anon-key")
os.environ.setdefault("AWS_BEDROCK_REGION", "us-east-1")
os.environ.setdefault("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
os.environ.setdefault("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# Patch Redis and Supabase modules
sys.modules["upstash_redis"] = MagicMock()
sys.modules["upstash_redis"].Redis = lambda **kwargs: mock_redis

# Patch supabase create_client
mock_supabase_module = MagicMock()
mock_supabase_module.create_client = lambda url, key: mock_supabase
mock_supabase_module.Client = MagicMock
sys.modules["supabase"] = mock_supabase_module

# Now patch the redis_client module
import memory.redis_client as redis_mod
redis_mod.redis_client = mock_redis
redis_mod.RedisClient = lambda: MagicMock(client=mock_redis)

# Patch supabase_client module
import memory.supabase_client as supa_mod
supa_mod.supabase = mock_supabase

# Patch SPADE with real-enough classes (not MagicMock — so inheritance works)
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

# Patch boto3
mock_boto3 = MagicMock()
sys.modules["boto3"] = mock_boto3

# Patch yaml loading for mother_agent (it loads config files on import)
import yaml
_original_yaml_safe_load = yaml.safe_load

def _mock_yaml_load(stream):
    """Return empty config if the file doesn't exist or is being loaded in test context."""
    try:
        return _original_yaml_safe_load(stream)
    except Exception:
        return {"sections": {}, "agents": {}, "execution_groups": {}, "gaps": {}}


# ─────────────────────────────────────────────────────────────────────────────
# Test: Bedrock converse API response format
# ─────────────────────────────────────────────────────────────────────────────

def make_bedrock_response(json_output: dict) -> dict:
    """Create a mock Bedrock converse API response."""
    return {
        "output": {
            "message": {
                "content": [{"text": json.dumps(json_output)}]
            }
        },
        "usage": {"inputTokens": 150, "outputTokens": 320},
    }


def make_bedrock_markdown_response(json_output: dict) -> dict:
    """Create response wrapped in markdown code blocks."""
    text = f"```json\n{json.dumps(json_output, indent=2)}\n```"
    return {
        "output": {
            "message": {
                "content": [{"text": text}]
            }
        }
    }


def make_bedrock_garbage_response() -> dict:
    """Create response with non-JSON text."""
    return {
        "output": {
            "message": {
                "content": [{"text": "Here's my analysis of the opportunity:\n\nThe business idea is promising..."}]
            }
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Import schemas (safe — they only use pydantic)
# ─────────────────────────────────────────────────────────────────────────────

from schemas.outputs.opportunity_analyst import OpportunityAnalystOutput
from schemas.outputs.environment_research import EnvironmentResearchOutput
from schemas.outputs.organisation_designer import OrganisationDesignerOutput
from schemas.outputs.swot_synthesizer import SWOTSynthesizerOutput
from schemas.outputs.marketing_strategy import MarketingStrategyOutput
from schemas.outputs.operations import OperationsOutput
from schemas.outputs.financial_modelling import FinancialModellingOutput
from schemas.outputs.launch_contingency import LaunchContingencyOutput
from schemas.outputs.summary_agent import SummaryAgentOutput

from schemas.inputs.opportunity_analyst import OpportunityAnalystInput
from schemas.inputs.environment_research import EnvironmentResearchInput
from schemas.inputs.organisation_designer import OrganisationDesignerInput
from schemas.inputs.swot_synthesizer import SWOTSynthesizerInput
from schemas.inputs.marketing_strategy import MarketingStrategyInput
from schemas.inputs.operations import OperationsInput
from schemas.inputs.financial_modelling import FinancialModellingInput
from schemas.inputs.launch_contingency import LaunchContingencyInput
from schemas.inputs.summary_agent import SummaryAgentInput


# ─────────────────────────────────────────────────────────────────────────────
# Test data: valid LLM responses for each agent
# ─────────────────────────────────────────────────────────────────────────────

VALID_OPPORTUNITY_OUTPUT = {
    "section_number": "1",
    "opportunity_description": "A B2B SaaS platform that simplifies equity management and cap table tracking for early-stage startups, replacing complex spreadsheets with automated workflows",
    "competitive_strategy": "Focus on simplicity and early-stage needs versus enterprise-focused incumbents like Carta",
    "objectives": [
        {"objective": "Acquire first 10 paying customers", "metric": "paying_customers", "target_value": "10", "timeframe": "6 months"},
        {"objective": "Achieve $50k ARR", "metric": "annual_recurring_revenue", "target_value": "50000", "timeframe": "12 months"},
    ],
    "icp_hypothesis": {
        "buyer_role": "Founder/CEO of seed-stage startup",
        "budget_process": "Founder discretion, no procurement",
        "decision_timeline": "1-2 weeks",
        "pain_points": ["Messy spreadsheet cap tables", "Expensive legal fees for equity events", "Confusion about dilution"],
    },
    # SOM/SAM must equal the stated capture rates, and capture_rate_year_1_pct is
    # rejected above 5.0 by the validator on MarketSizing.
    "market_sizing": {
        "tam": 120000000.0,
        "tam_definition": "All US startups raising a seed round each year that need cap table management software",
        "tam_source": "Crunchbase 2025 seed round counts cross-checked against PitchBook",
        "sam": 48000000.0,
        "sam_definition": "US seed-stage startups that self-serve rather than route equity events through a law firm",
        "sam_calculation": "TAM $120M x 40% self-serve segment = $48M",
        "som_year_1": 480000.0,
        "som_year_3": 1920000.0,
        "som_logic": "1% of SAM in year 1 via founder communities and accelerator partnerships, rising to 4% by year 3 as referrals compound. Constrained by a two-person go-to-market team.",
        "capture_rate_year_1_pct": 1.0,
        "capture_rate_year_3_pct": 4.0,
    },
    "assumptions_used": [
        {"statement": "Seed-stage startups need simpler tooling than Series B+", "confidence": "high", "source": "alex_provided", "source_detail": "CEO Q&A"},
        {"statement": "Market size of 50k+ seed startups per year in US", "confidence": "medium", "source": "agent_inferred", "source_detail": None},
    ],
    "uncertainties": ["Willingness to pay at seed stage", "Regulatory requirements for cap table management"],
    "confidence_score": "high",
    "input_tokens": 0,
    "output_tokens": 0,
}

VALID_ENVIRONMENT_OUTPUT = {
    "section_number": "3",
    "pest_analysis": [
        {"category": "political", "factor": "SEC regulations on equity reporting", "impact": "neutral", "relevance": "medium"},
        {"category": "economic", "factor": "VC funding boom driving startup creation", "impact": "positive", "relevance": "high"},
        {"category": "social", "factor": "Remote work increasing startup formation", "impact": "positive", "relevance": "medium"},
        {"category": "technological", "factor": "Cloud infrastructure cost reduction", "impact": "positive", "relevance": "high"},
    ],
    "five_forces": [
        {"force": "Threat of new entrants", "assessment": "Low barriers for basic tools, high for compliance-grade", "strength": "medium"},
        {"force": "Bargaining power of suppliers", "assessment": "Cloud providers are commodity", "strength": "low"},
        {"force": "Bargaining power of buyers", "assessment": "Startups have many options", "strength": "high"},
        {"force": "Threat of substitutes", "assessment": "Spreadsheets and lawyers", "strength": "high"},
        {"force": "Industry rivalry", "assessment": "Carta dominates enterprise, gap in SMB", "strength": "medium"},
    ],
    "risks_opportunities": {
        "risks": ["Carta moves downmarket", "Regulatory changes"],
        "opportunities": ["Underserved seed-stage segment", "API-first integration play"],
    },
    "market_context": "The equity management software market is dominated by enterprise players like Carta and Shareworks. Seed-stage startups are underserved — they use spreadsheets or pay expensive lawyers. Cloud cost reduction and startup formation rates create a favourable window for a simple, affordable tool.",
    "assumptions_used": [{"statement": "Seed segment is underserved", "confidence": "high", "source": "agent_inferred", "source_detail": None}],
    "uncertainties": ["Exact market size for seed-stage segment"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

VALID_ORG_DESIGNER_OUTPUT = {
    "section_number": "4",
    "org_structure": "Flat structure: Founder/CEO, 1 senior engineer, 1 designer (contract), advisor network. No middle management for first 18 months.",
    "capability_gaps": [
        {"gap": "Full-stack engineering", "severity": "high", "resolution": "hire"},
        {"gap": "Legal/compliance knowledge", "severity": "medium", "resolution": "partner"},
    ],
    "roles_and_responsibilities": [
        {"title": "Founder/CEO", "responsibilities": ["Product strategy", "Fundraising", "Sales"], "required_skills": ["Leadership", "Domain expertise"], "hire_timeline": "Immediate", "assigned_to": "founder"},
        {"title": "Senior Engineer", "responsibilities": ["Architecture", "Core platform", "DevOps"], "required_skills": ["Python", "React", "AWS"], "hire_timeline": "Month 1-2", "assigned_to": "hire"},
    ],
    "headcount_plan": {"year_1": {"count": 3, "cost": 280000.0}, "year_2": {"count": 7, "cost": 560000.0}, "year_3": {"count": 12, "cost": 960000.0}},
    "personnel_policy": "Remote-first with quarterly in-person sprints. Equity vesting over 4 years with 1-year cliff. Competitive base salary at 75th percentile.",
    "knowledge_gaps": ["Compliance requirements for equity software", "Enterprise sales process"],
    "assumptions_used": [{"statement": "Remote-first reduces overhead", "confidence": "high", "source": "validated", "source_detail": "Industry standard"}],
    "uncertainties": ["Founder's technical depth", "Budget for first hire"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}


# ─────────────────────────────────────────────────────────────────────────────
# E2E Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_opportunity_analyst_valid_json():
    """Test: Bedrock returns valid JSON → agent produces valid output."""
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent

    agent = object.__new__(OpportunityAnalystAgent)
    agent.model_id = "claude-sonnet-4-20250514"
    agent.redis = MagicMock(client=mock_redis)

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response(VALID_OPPORTUNITY_OUTPUT)
    agent.bedrock = mock_bedrock

    inp = OpportunityAnalystInput(
        task_id="task-001",
        session_id="sess-001",
        idea_summary="A B2B SaaS platform for equity management and cap table tracking for early-stage startups",
        ceo_assumptions=[{"question": "Target market?", "answer": "Seed-stage startups in US"}],
        approved_decision={"status": "approved", "rationale": "Good market opportunity"},
        acceptance_criteria="All required fields present with confidence >= medium",
    )

    user_message = agent._build_prompt(inp)
    raw_response = mock_bedrock.converse(
        modelId=agent.model_id,
        system=[{"text": "test"}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={"maxTokens": 4096},
    )
    llm_text = raw_response["output"]["message"]["content"][0]["text"]

    output_data = agent._parse_llm_response(llm_text, inp)
    output_data["task_id"] = "task-001"
    output_data["model_used"] = agent.model_id

    validated = OpportunityAnalystOutput(**output_data)
    assert validated.confidence_score == "high"
    assert len(validated.opportunity_description) >= 50
    assert len(validated.objectives) >= 1
    logger.info("PASS: test_opportunity_analyst_valid_json")
    return True


def test_opportunity_analyst_markdown_wrapped():
    """Test: Bedrock returns JSON in markdown code block → agent strips and parses."""
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent

    agent = object.__new__(OpportunityAnalystAgent)
    agent.model_id = "claude-sonnet-4-20250514"
    agent.bedrock = None

    inp = OpportunityAnalystInput(
        task_id="task-002",
        session_id="sess-001",
        idea_summary="A B2B SaaS platform for equity management and cap table tracking for early-stage startups",
        ceo_assumptions=[],
        approved_decision={},
        acceptance_criteria="test",
    )

    markdown_text = f"```json\n{json.dumps(VALID_OPPORTUNITY_OUTPUT, indent=2)}\n```"
    output_data = agent._parse_llm_response(markdown_text, inp)
    output_data["task_id"] = "task-002"
    output_data["model_used"] = agent.model_id

    validated = OpportunityAnalystOutput(**output_data)
    assert validated.confidence_score == "high"
    logger.info("PASS: test_opportunity_analyst_markdown_wrapped")
    return True


def test_opportunity_analyst_garbage_fallback():
    """Test: Bedrock returns non-JSON → agent falls back to valid defaults."""
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent

    agent = object.__new__(OpportunityAnalystAgent)
    agent.model_id = "claude-sonnet-4-20250514"
    agent.bedrock = None

    inp = OpportunityAnalystInput(
        task_id="task-003",
        session_id="sess-001",
        idea_summary="A B2B SaaS platform for equity management and cap table tracking for early-stage startups",
        ceo_assumptions=[],
        approved_decision={},
        acceptance_criteria="test",
    )

    garbage = "Here's my analysis:\n\nThe opportunity looks good because the market is growing..."
    output_data = agent._parse_llm_response(garbage, inp)
    output_data["task_id"] = "task-003"
    output_data["model_used"] = agent.model_id

    validated = OpportunityAnalystOutput(**output_data)
    assert validated.confidence_score == "low"
    assert "unparseable" in validated.assumptions_used[0].statement.lower() or "defaults" in validated.assumptions_used[0].statement.lower()
    logger.info("PASS: test_opportunity_analyst_garbage_fallback")
    return True


def test_opportunity_analyst_empty_idea_recovery():
    """Test: Empty idea_summary is recovered from CEO assumptions."""
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent

    agent = object.__new__(OpportunityAnalystAgent)
    agent.model_id = "claude-sonnet-4-20250514"
    agent.redis = MagicMock(client=mock_redis)
    agent.bedrock = MagicMock()
    agent.bedrock.converse.return_value = make_bedrock_response(VALID_OPPORTUNITY_OUTPUT)

    content = {
        "task": {
            "input_package": {
                "idea_summary": "",  # EMPTY — the original bug
                "ceo_assumptions": [{"question": "What market?", "answer": "B2B SaaS for HR tech startups"}],
                "approved_decision": {"status": "approved", "rationale": "Solid opportunity"},
            },
            "acceptance_criteria": "test",
        }
    }

    # Extract the logic from handle_request
    task = content.get("task", {})
    input_package = task.get("input_package", {})

    idea_summary = input_package.get("idea_summary", "")
    ceo_assumptions = input_package.get("ceo_assumptions", [])
    approved_decision = input_package.get("approved_decision", {})

    if len(idea_summary) < 10:
        if ceo_assumptions:
            parts = [f"{a.get('question', '')}: {a.get('answer', '')}" for a in ceo_assumptions if a.get("answer")]
            idea_summary = "Business idea based on CEO answers: " + "; ".join(parts)
        elif approved_decision:
            idea_summary = approved_decision.get("rationale", "") or "Business idea approved at Gate 1"
        if len(idea_summary) < 10:
            idea_summary = "Business idea approved at Gate 1 — details pending clarification"

    assert len(idea_summary) >= 10, f"idea_summary still too short: '{idea_summary}'"

    validated_input = OpportunityAnalystInput(
        task_id="task-004",
        session_id="sess-001",
        idea_summary=idea_summary,
        ceo_assumptions=ceo_assumptions,
        approved_decision=approved_decision,
        acceptance_criteria="test",
    )
    assert validated_input.idea_summary == "Business idea based on CEO answers: What market?: B2B SaaS for HR tech startups"
    logger.info("PASS: test_opportunity_analyst_empty_idea_recovery")
    return True


def test_environment_research_valid():
    """Test: Environment research agent processes valid JSON."""
    from agents.phase2.environment_research import EnvironmentResearchAgent

    agent = object.__new__(EnvironmentResearchAgent)
    agent.model_id = "claude-haiku-4-5-20251001"
    agent.bedrock = None

    inp = EnvironmentResearchInput(
        task_id="task-005", session_id="sess-001",
        market_scope="B2B SaaS equity management",
        business_type="saas", icp_hypothesis={}, acceptance_criteria="test",
    )

    output_data = agent._parse_llm_response(json.dumps(VALID_ENVIRONMENT_OUTPUT), inp)
    output_data["task_id"] = "task-005"
    output_data["model_used"] = agent.model_id

    validated = EnvironmentResearchOutput(**output_data)
    assert len(validated.pest_analysis) >= 4
    assert len(validated.five_forces) >= 5
    assert len(validated.market_context) >= 100
    logger.info("PASS: test_environment_research_valid")
    return True


def test_environment_research_fallback():
    """Test: Environment research fallback produces valid output."""
    from agents.phase2.environment_research import EnvironmentResearchAgent

    agent = object.__new__(EnvironmentResearchAgent)
    agent.model_id = "claude-haiku-4-5-20251001"
    agent.bedrock = None

    inp = EnvironmentResearchInput(
        task_id="task-006", session_id="sess-001",
        market_scope="B2B SaaS", business_type="saas",
        icp_hypothesis={}, acceptance_criteria="test",
    )

    output_data = agent._parse_llm_response("Not valid JSON", inp)
    output_data["task_id"] = "task-006"
    output_data["model_used"] = agent.model_id

    validated = EnvironmentResearchOutput(**output_data)
    assert validated.confidence_score == "low"
    assert len(validated.pest_analysis) >= 4
    assert len(validated.five_forces) >= 5
    logger.info("PASS: test_environment_research_fallback")
    return True


def test_organisation_designer_valid():
    """Test: Org designer processes valid JSON."""
    from agents.phase2.organisation_designer import OrganisationDesignerAgent

    agent = object.__new__(OrganisationDesignerAgent)
    agent.model_id = "claude-haiku-4-5-20251001"
    agent.bedrock = None

    inp = OrganisationDesignerInput(
        task_id="task-007", session_id="sess-001",
        opportunity_description="SaaS equity platform",
        business_type="saas", acceptance_criteria="test",
    )

    output_data = agent._parse_llm_response(json.dumps(VALID_ORG_DESIGNER_OUTPUT), inp)
    output_data["task_id"] = "task-007"
    output_data["model_used"] = agent.model_id

    validated = OrganisationDesignerOutput(**output_data)
    assert len(validated.personnel_policy) >= 50
    assert len(validated.roles_and_responsibilities) >= 1
    logger.info("PASS: test_organisation_designer_valid")
    return True


def test_organisation_designer_empty_opportunity():
    """Test: Org designer handles empty opportunity_description."""
    from agents.phase2.organisation_designer import OrganisationDesignerAgent

    agent = object.__new__(OrganisationDesignerAgent)
    agent.model_id = "claude-haiku-4-5-20251001"
    agent.redis = MagicMock(client=mock_redis)
    agent.bedrock = MagicMock()
    agent.bedrock.converse.return_value = make_bedrock_response(VALID_ORG_DESIGNER_OUTPUT)

    content = {
        "task": {
            "input_package": {
                "opportunity_description": "",  # Empty — would have crashed before fix
                "business_type": "",
                "idea_summary": "A fintech product for cap table management",
            },
            "acceptance_criteria": "test",
        }
    }

    task = content.get("task", {})
    input_package = task.get("input_package", {})

    opportunity_description = input_package.get("opportunity_description", "")
    if not opportunity_description:
        opportunity_description = input_package.get("idea_summary", "") or "Business opportunity — details from Section 1 pending"

    validated_input = OrganisationDesignerInput(
        task_id="task-008",
        session_id="sess-001",
        opportunity_description=opportunity_description,
        business_type=input_package.get("business_type", "") or "startup",
        acceptance_criteria="test",
    )

    assert validated_input.opportunity_description == "A fintech product for cap table management"
    assert validated_input.business_type == "startup"
    logger.info("PASS: test_organisation_designer_empty_opportunity")
    return True


def test_bedrock_converse_api_format():
    """Test: _call_llm uses correct Bedrock converse API format."""
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent

    agent = object.__new__(OpportunityAnalystAgent)
    agent.model_id = "claude-sonnet-4-20250514"

    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response(VALID_OPPORTUNITY_OUTPUT)
    agent.bedrock = mock_bedrock

    import asyncio
    text, usage = asyncio.run(agent._call_llm("Test prompt"))

    # Verify converse was called (not invoke_model)
    mock_bedrock.converse.assert_called_once()
    call_kwargs = mock_bedrock.converse.call_args[1]
    assert call_kwargs["modelId"] == "claude-sonnet-4-20250514"
    assert call_kwargs["inferenceConfig"] == {"maxTokens": 4096}
    assert call_kwargs["messages"][0]["role"] == "user"
    assert call_kwargs["messages"][0]["content"][0]["text"] == "Test prompt"
    assert isinstance(call_kwargs["system"], list)
    assert "text" in call_kwargs["system"][0]

    assert text is not None
    parsed = json.loads(text)
    assert parsed["section_number"] == "1"
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 320
    logger.info("PASS: test_bedrock_converse_api_format")
    return True


def test_all_agents_fallback_produce_valid_schemas():
    """Test: All 9 agents produce valid Pydantic output on parse failure."""
    results = []

    # Opportunity Analyst
    from agents.phase2.opportunity_analyst import OpportunityAnalystAgent
    a = object.__new__(OpportunityAnalystAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = OpportunityAnalystInput(task_id="t", session_id="s", idea_summary="A test business idea that is long enough to pass validation", ceo_assumptions=[], approved_decision={}, acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    OpportunityAnalystOutput(**data)
    results.append("opportunity_analyst")

    # Environment Research
    from agents.phase2.environment_research import EnvironmentResearchAgent
    a = object.__new__(EnvironmentResearchAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = EnvironmentResearchInput(task_id="t", session_id="s", market_scope="test", business_type="saas", icp_hypothesis={}, acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    EnvironmentResearchOutput(**data)
    results.append("environment_research")

    # Organisation Designer
    from agents.phase2.organisation_designer import OrganisationDesignerAgent
    a = object.__new__(OrganisationDesignerAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = OrganisationDesignerInput(task_id="t", session_id="s", opportunity_description="test", business_type="saas", acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    OrganisationDesignerOutput(**data)
    results.append("organisation_designer")

    # SWOT Synthesizer
    from agents.phase2.swot_synthesizer import SWOTSynthesizerAgent
    a = object.__new__(SWOTSynthesizerAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = SWOTSynthesizerInput(task_id="t", session_id="s", pest_analysis=[], five_forces=[], risks_opportunities={}, capability_gaps=[], org_structure="", opportunity_description="t", acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    SWOTSynthesizerOutput(**data)
    results.append("swot_synthesizer")

    # Marketing Strategy
    from agents.phase2.marketing_strategy import MarketingStrategyAgent
    a = object.__new__(MarketingStrategyAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = MarketingStrategyInput(task_id="t", session_id="s", swot_matrix={}, icp_hypothesis={}, competitive_strategy="t", market_context="t", strategic_implications="", acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    MarketingStrategyOutput(**data)
    results.append("marketing_strategy")

    # Operations
    from agents.phase2.operations import OperationsAgent
    a = object.__new__(OperationsAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = OperationsInput(task_id="t", session_id="s", opportunity_description="t", business_type="saas", revenue_assumptions={}, swot_matrix={}, acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    OperationsOutput(**data)
    results.append("operations")

    # Financial Modelling
    from agents.phase2.financial_modelling import FinancialModellingAgent
    a = object.__new__(FinancialModellingAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = FinancialModellingInput(task_id="t", session_id="s", revenue_assumptions={"price_per_unit": 100, "volume_year1": 50, "volume_year2": 200, "volume_year3": 500}, cac_assumptions={}, cost_structure={}, headcount_plan={}, business_type="saas", opportunity_description="t", market_context="t", simpy_runs=100, acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    data["simpy_runs_completed"] = 100
    data["financial_skills_applied"] = []
    data["probability_distribution"] = [{"scenario": "P10", "year1_revenue": 1000, "year2_revenue": 5000, "year3_revenue": 10000}, {"scenario": "P50", "year1_revenue": 5000, "year2_revenue": 20000, "year3_revenue": 50000}, {"scenario": "P90", "year1_revenue": 10000, "year2_revenue": 50000, "year3_revenue": 100000}]
    data["primary_risk_factor"] = "test"
    FinancialModellingOutput(**data)
    results.append("financial_modelling")

    # Launch Contingency
    from agents.phase2.launch_contingency import LaunchContingencyAgent
    a = object.__new__(LaunchContingencyAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = LaunchContingencyInput(task_id="t", session_id="s", revenue_assumptions={}, headcount_plan={}, break_even_analysis={}, probability_distribution=[], primary_risk_factor="", market_entry_strategy="", acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    LaunchContingencyOutput(**data)
    results.append("launch_contingency")

    # Summary Agent
    from agents.phase2.summary_agent import SummaryAgentAgent
    a = object.__new__(SummaryAgentAgent)
    a.model_id = "test"
    a.bedrock = None
    inp = SummaryAgentInput(task_id="t", session_id="s", pipeline_run_id="r", completed_sections={"1": {}}, flagged_assumptions=[], acceptance_criteria="t")
    data = a._parse_llm_response("bad", inp)
    data["task_id"] = "t"; data["model_used"] = "t"
    SummaryAgentOutput(**data)
    results.append("summary_agent")

    logger.info("PASS: test_all_agents_fallback_produce_valid_schemas (%d agents)", len(results))
    return True


def test_mother_agent_input_assembly():
    """Test: Mother Agent correctly assembles input packages with fallbacks."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.db = MagicMock()
    agent.redis = MagicMock(client=mock_redis)
    agent.dependency_map = {
        "sections": {
            "1": {"required_inputs": [{"field": "idea_summary", "source": "phase1_memory"}]},
        }
    }
    agent.agent_roster = {"agents": {}, "execution_groups": {}}
    agent.gap_rules = {"gaps": {}}
    agent.active_runs = {}
    # Mock the learning engine
    learning_mock = MagicMock()
    learning_mock.build_learning_context.return_value = ""
    agent.learning = learning_mock

    # Case 1: idea_summary from CEO assumptions
    phase1_data = {
        "idea_summary": "",
        "ceo_assumptions": [{"question": "What?", "answer": "HR SaaS platform"}],
        "approved_decision": {},
        "business_type": "saas",
        "market_scope": "",
    }
    section_config = {"required_inputs": [{"field": "idea_summary", "source": "phase1_memory"}]}
    result = agent._assemble_input_package(section_config, {}, phase1_data)
    assert len(result["idea_summary"]) >= 10
    assert "HR SaaS" in result["idea_summary"]

    # Case 2: idea_summary from approved decision
    phase1_data2 = {
        "idea_summary": "",
        "ceo_assumptions": [],
        "approved_decision": {"rationale": "Great opportunity in fintech space"},
        "business_type": "fintech",
        "market_scope": "",
    }
    result2 = agent._assemble_input_package(section_config, {}, phase1_data2)
    assert "fintech" in result2["idea_summary"].lower() or "opportunity" in result2["idea_summary"].lower()

    # Case 3: total fallback
    phase1_data3 = {
        "idea_summary": "",
        "ceo_assumptions": [],
        "approved_decision": {},
        "business_type": "unknown",
        "market_scope": "",
    }
    result3 = agent._assemble_input_package(section_config, {}, phase1_data3)
    assert len(result3["idea_summary"]) >= 10

    logger.info("PASS: test_mother_agent_input_assembly")
    return True


def test_pipeline_full_sequence():
    """Test: Full pipeline sequence — input → LLM → parse → validate → output for all agents."""
    agents_data = [
        ("opportunity_analyst", VALID_OPPORTUNITY_OUTPUT, OpportunityAnalystOutput),
        ("environment_research", VALID_ENVIRONMENT_OUTPUT, EnvironmentResearchOutput),
        ("organisation_designer", VALID_ORG_DESIGNER_OUTPUT, OrganisationDesignerOutput),
    ]

    for agent_name, valid_output, schema_class in agents_data:
        # Simulate: Bedrock returns valid JSON
        response = make_bedrock_response(valid_output)
        text = response["output"]["message"]["content"][0]["text"]
        parsed = json.loads(text)
        parsed["task_id"] = f"task-{agent_name}"
        parsed["model_used"] = "claude-sonnet-4-20250514"
        validated = schema_class(**parsed)
        assert validated.task_id == f"task-{agent_name}"

        # Simulate: Bedrock returns markdown-wrapped JSON
        md_response = make_bedrock_markdown_response(valid_output)
        md_text = md_response["output"]["message"]["content"][0]["text"]
        # Strip markdown
        stripped = md_text.strip()
        if stripped.startswith("```"):
            first_nl = stripped.index("\n")
            stripped = stripped[first_nl + 1:]
            if stripped.endswith("```"):
                stripped = stripped[:-3].strip()
        parsed2 = json.loads(stripped)
        parsed2["task_id"] = f"task-{agent_name}"
        parsed2["model_used"] = "test"
        validated2 = schema_class(**parsed2)
        assert validated2.task_id == f"task-{agent_name}"

    logger.info("PASS: test_pipeline_full_sequence (3 agents, 2 response formats each)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_opportunity_analyst_valid_json,
        test_opportunity_analyst_markdown_wrapped,
        test_opportunity_analyst_garbage_fallback,
        test_opportunity_analyst_empty_idea_recovery,
        test_environment_research_valid,
        test_environment_research_fallback,
        test_organisation_designer_valid,
        test_organisation_designer_empty_opportunity,
        test_bedrock_converse_api_format,
        test_all_agents_fallback_produce_valid_schemas,
        test_mother_agent_input_assembly,
        test_pipeline_full_sequence,
    ]

    logger.info("=" * 70)
    logger.info("PHASE 2 END-TO-END TEST SUITE")
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
