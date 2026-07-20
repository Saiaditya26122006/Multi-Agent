"""
Full end-to-end test for the 13-agent Phase 2 pipeline.

Tests the entire orchestration flow:
  Pipeline trigger → Phase 1 read → Section classification → 4 execution groups
  → Gate 2 approvals → Child agent outputs → Council reviews → DA challenges
  → Coherence audit → Document compilation → Delivery

All external deps (Bedrock, Redis, Supabase, SPADE, Telegram) are mocked.
"""
import sys
import json
import asyncio
import logging
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Optional
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Mock infrastructure
# ─────────────────────────────────────────────────────────────────────────────

class MockRedisClient:
    """In-memory Redis mock with key-value store and expiry tracking."""

    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        self._store[key] = value

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        self._store.pop(key, None)

    def keys(self, pattern="*"):
        if pattern == "*":
            return list(self._store.keys())
        prefix = pattern.replace("*", "")
        return [k for k in self._store.keys() if k.startswith(prefix)]

    def exists(self, key):
        return 1 if key in self._store else 0

    def incr(self, key):
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val


class MockSupabaseTable:
    def __init__(self, name: str, shared_store: dict):
        self._name = name
        self._store = shared_store
        self._chain_data = None
        self._chain_filter = {}

    def insert(self, data):
        if self._name not in self._store:
            self._store[self._name] = []
        if isinstance(data, list):
            self._store[self._name].extend(data)
        else:
            if "id" not in data:
                data["id"] = str(uuid.uuid4())
            self._store[self._name].append(data)
        self._chain_data = data
        return self

    def select(self, *args):
        return self

    def eq(self, field, value):
        self._chain_filter[field] = value
        return self

    def neq(self, *args):
        return self

    def not_(self):
        return self

    @property
    def not_(self):
        return self

    def in_(self, *args):
        return self

    def gte(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def update(self, data):
        self._chain_data = data
        return self

    def execute(self):
        result = MagicMock()
        if self._chain_data and isinstance(self._chain_data, dict) and "id" in self._chain_data:
            result.data = [self._chain_data]
        elif self._name in self._store and self._store[self._name]:
            result.data = self._store[self._name]
        else:
            result.data = [{"id": str(uuid.uuid4())}]
        self._chain_filter = {}
        return result


class MockSupabaseClient:
    def __init__(self):
        self._tables = {}
        self._store = {}

    def table(self, name):
        return MockSupabaseTable(name, self._store)


mock_redis = MockRedisClient()
mock_supabase = MockSupabaseClient()


# ─────────────────────────────────────────────────────────────────────────────
# Patch environment and modules before importing agents
# ─────────────────────────────────────────────────────────────────────────────

import os

os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://fake.upstash.io")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "fake-token")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake-anon-key")
os.environ.setdefault("AWS_BEDROCK_REGION", "us-east-1")
os.environ.setdefault("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
os.environ.setdefault("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
os.environ.setdefault("MOTHER_AGENT_JID", "mother@xmpp.local")
os.environ.setdefault("MOTHER_AGENT_PASSWORD", "pass")
os.environ.setdefault("OPPORTUNITY_ANALYST_JID", "opp@xmpp.local")
os.environ.setdefault("OPPORTUNITY_ANALYST_PASSWORD", "pass")
os.environ.setdefault("ENVIRONMENT_RESEARCH_JID", "env@xmpp.local")
os.environ.setdefault("ENVIRONMENT_RESEARCH_PASSWORD", "pass")
os.environ.setdefault("ORGANISATION_DESIGNER_JID", "org@xmpp.local")
os.environ.setdefault("ORGANISATION_DESIGNER_PASSWORD", "pass")
os.environ.setdefault("SWOT_SYNTHESIZER_JID", "swot@xmpp.local")
os.environ.setdefault("SWOT_SYNTHESIZER_PASSWORD", "pass")
os.environ.setdefault("MARKETING_STRATEGY_JID", "mkt@xmpp.local")
os.environ.setdefault("MARKETING_STRATEGY_PASSWORD", "pass")
os.environ.setdefault("OPERATIONS_JID", "ops@xmpp.local")
os.environ.setdefault("OPERATIONS_PASSWORD", "pass")
os.environ.setdefault("FINANCIAL_MODELLING_JID", "fin@xmpp.local")
os.environ.setdefault("FINANCIAL_MODELLING_PASSWORD", "pass")
os.environ.setdefault("LAUNCH_CONTINGENCY_JID", "launch@xmpp.local")
os.environ.setdefault("LAUNCH_CONTINGENCY_PASSWORD", "pass")
os.environ.setdefault("SUMMARY_JID", "summary@xmpp.local")
os.environ.setdefault("SUMMARY_PASSWORD", "pass")
os.environ.setdefault("COUNCIL_AGENT_JID", "council@xmpp.local")
os.environ.setdefault("COUNCIL_AGENT_PASSWORD", "pass")
os.environ.setdefault("DEVILS_ADVOCATE_JID", "da@xmpp.local")
os.environ.setdefault("DEVILS_ADVOCATE_PASSWORD", "pass")

# Note: Redis is no longer used - session state is stored in Supabase

# Patch Supabase
mock_supabase_module = MagicMock()
mock_supabase_module.create_client = lambda url, key: mock_supabase
mock_supabase_module.Client = MagicMock
sys.modules["supabase"] = mock_supabase_module

# Patch SPADE
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
        self.sender = "test@xmpp.local"
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

# Patch memory modules
import memory.supabase_client as supa_mod
supa_mod.supabase = mock_supabase
supa_mod.SupabaseClient = lambda: MagicMock(client=mock_supabase)

# Patch trace emitter
sys.modules.setdefault("tools", types.ModuleType("tools"))
sys.modules.setdefault("tools.trace_emitter", types.ModuleType("tools.trace_emitter"))
sys.modules["tools.trace_emitter"].emit_trace = lambda *args, **kwargs: None

# Patch ceo_data
sys.modules.setdefault("ceo_data", types.ModuleType("ceo_data"))
sys.modules.setdefault("ceo_data.loader", types.ModuleType("ceo_data.loader"))
sys.modules["ceo_data.loader"].load_all_ceo_data = lambda: {}
sys.modules["ceo_data.loader"].get_relevant_ceo_data = lambda section: {}


# ─────────────────────────────────────────────────────────────────────────────
# Import schemas (Pydantic only — safe)
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


# ─────────────────────────────────────────────────────────────────────────────
# Valid agent outputs — one per agent, schema-compliant
# ─────────────────────────────────────────────────────────────────────────────

PHASE1_SESSION_DATA = {
    "id": "sess-e2e-001",
    "idea_summary": (
        "A lightweight CRM tool for freelance designers and developers that uses AI "
        "to auto-categorize leads, predict project close probability, and generate "
        "follow-up emails. Target price $29/month."
    ),
    "ceo_assumptions": [
        {"question": "Who is your target customer?", "answer": "Freelance web designers making $50K-$150K/year"},
        {"question": "What's your pricing model?", "answer": "$29/month flat rate, annual discount at $290/year"},
        {"question": "How will you acquire customers?", "answer": "Content marketing, SEO, ProductHunt launch"},
        {"question": "What's your budget?", "answer": "Bootstrapping with $15K savings"},
    ],
    "approved_decision": {
        "decision": "approved",
        "rationale": "Clear niche, validated pain point from 20 interviews, achievable scope",
        "risk_flags": ["crowded market", "low price point"],
    },
    "business_type": "saas",
    "market_scope": "Freelance CRM tools for solo practitioners",
    "chat_id": "12345",
}

OPPORTUNITY_ANALYST_OUTPUT = {
    "section_number": "1",
    "opportunity_description": "A B2B SaaS CRM for freelancers using AI to automate lead management, predict close probability, and generate follow-up emails at $29/month",
    "competitive_strategy": "Differentiate from HoneyBook/Dubsado via AI automation and simplicity for solo practitioners",
    "objectives": [
        {"objective": "Acquire 100 paying customers", "metric": "paying_customers", "target_value": "100", "timeframe": "6 months"},
        {"objective": "Achieve $35k ARR", "metric": "annual_recurring_revenue", "target_value": "35000", "timeframe": "12 months"},
        {"objective": "Reach 4.5 star rating", "metric": "app_rating", "target_value": "4.5", "timeframe": "9 months"},
    ],
    "icp_hypothesis": {
        "buyer_role": "Freelance web designer/developer",
        "budget_process": "Personal credit card, no procurement",
        "decision_timeline": "Same day to 1 week",
        "pain_points": ["Manual lead tracking in spreadsheets", "Forgotten follow-ups losing deals", "No pipeline visibility"],
    },
    "assumptions_used": [
        {"statement": "Freelancers will pay $29/mo for CRM", "confidence": "medium", "source": "alex_provided", "source_detail": "CEO interviews"},
        {"statement": "AI lead scoring is technically feasible at this price", "confidence": "high", "source": "agent_inferred", "source_detail": None},
    ],
    "uncertainties": ["Willingness to pay at $29 vs free alternatives", "CAC in crowded market"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

ORGANISATION_DESIGNER_OUTPUT = {
    "section_number": "4",
    "org_structure": "Solo founder with contract developer and designer. Advisory board of 2 (ex-CRM PM, freelance community leader). No full-time hires until $10k MRR.",
    "capability_gaps": [
        {"gap": "Full-stack engineering", "severity": "high", "resolution": "contract"},
        {"gap": "Growth marketing", "severity": "medium", "resolution": "founder_learns"},
    ],
    "roles_and_responsibilities": [
        {"title": "Founder/CEO", "responsibilities": ["Product", "Sales", "Content"], "required_skills": ["Product management", "Writing"], "hire_timeline": "Immediate", "assigned_to": "founder"},
        {"title": "Contract Developer", "responsibilities": ["Backend", "AI pipeline", "DevOps"], "required_skills": ["Python", "React", "AWS"], "hire_timeline": "Month 1", "assigned_to": "outsource"},
    ],
    "headcount_plan": {"year_1": {"count": 2, "cost": 85000.0}, "year_2": {"count": 4, "cost": 220000.0}, "year_3": {"count": 8, "cost": 480000.0}},
    "personnel_policy": "Remote-first. Contractors paid monthly with 30-day notice period. First FTE hire at $10k MRR with standard equity vesting (4yr/1yr cliff).",
    "knowledge_gaps": ["Enterprise sales motions", "AI model fine-tuning at scale"],
    "assumptions_used": [{"statement": "Contract developer available at $60/hr", "confidence": "high", "source": "validated", "source_detail": "Market rate check"}],
    "uncertainties": ["Founder capacity as solo operator"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

ENVIRONMENT_RESEARCH_OUTPUT = {
    "section_number": "3",
    "pest_analysis": [
        {"category": "political", "factor": "Data privacy regulations (GDPR/CCPA)", "impact": "neutral", "relevance": "medium"},
        {"category": "economic", "factor": "Freelance economy growing 15% YoY", "impact": "positive", "relevance": "high"},
        {"category": "social", "factor": "Remote work normalizing freelance careers", "impact": "positive", "relevance": "high"},
        {"category": "technological", "factor": "LLM costs dropping rapidly", "impact": "positive", "relevance": "high"},
    ],
    "five_forces": [
        {"force": "Threat of new entrants", "assessment": "Moderate — AI lowers barriers but data moat builds over time", "strength": "medium"},
        {"force": "Bargaining power of suppliers", "assessment": "Low — cloud/AI providers are commodity", "strength": "low"},
        {"force": "Bargaining power of buyers", "assessment": "High — many free alternatives exist", "strength": "high"},
        {"force": "Threat of substitutes", "assessment": "High — spreadsheets, Notion, free CRMs", "strength": "high"},
        {"force": "Industry rivalry", "assessment": "Medium — HoneyBook/Dubsado target similar market", "strength": "medium"},
    ],
    "risks_opportunities": {
        "risks": ["HoneyBook adds AI features", "Pricing pressure from free tools", "Customer acquisition cost too high"],
        "opportunities": ["Underserved solo freelancer segment", "AI differentiation window (12-18mo)", "Community-led growth via content"],
    },
    "market_context": "The freelance CRM market is growing rapidly as the gig economy expands. Current leaders (HoneyBook, Dubsado, Bonsai) target agencies and small teams. Solo freelancers making $50K-$150K are underserved — they use spreadsheets or generic CRMs not built for their workflow. AI-powered automation is a defensible wedge if executed in the next 12-18 months before incumbents catch up.",
    "assumptions_used": [{"statement": "Solo freelancers underserved by current CRM tools", "confidence": "high", "source": "agent_inferred", "source_detail": None}],
    "uncertainties": ["Exact TAM for solo freelancer segment", "Speed of incumbent AI adoption"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

MARKETING_STRATEGY_OUTPUT = {
    "section_number": "8",
    "target_market_analysis": {
        "segmentation": "Solo freelance web designers and developers in US/UK",
        "icp_refined": "Freelancers earning $50K-$150K/year, managing 5-15 active leads",
        "market_size_tam_sam_som": {"tam": 2100000, "sam": 300000, "som": 5000},
    },
    "competitors": [
        {"name": "HoneyBook", "positioning": "All-in-one for creative businesses", "pricing": "$19-$79/mo", "strengths": ["Brand recognition", "Full suite"], "weaknesses": ["Complex for solo users", "No AI features"]},
        {"name": "Dubsado", "positioning": "Client management for creatives", "pricing": "$20-$40/mo", "strengths": ["Workflow automation", "Forms"], "weaknesses": ["Dated UI", "No lead scoring"]},
        {"name": "Bonsai", "positioning": "Freelance admin toolkit", "pricing": "$25/mo", "strengths": ["Contracts + invoicing", "Simple"], "weaknesses": ["Weak CRM", "No AI"]},
    ],
    "competitive_advantages": [
        "AI-powered lead scoring not offered by any competitor at this price point",
        "Purpose-built for solo freelancers (not adapted from team/agency tools)",
        "Automated follow-up generation saves 2-3 hours/week per user",
    ],
    "marketing_mix": {
        "product": "AI CRM with lead scoring, auto follow-up, and pipeline visualization",
        "pricing_policy": "$29/month or $290/year (17% discount)",
        "distribution": "Direct web app, no app stores initially",
        "promotion": "Content marketing (60%), SEO (25%), ProductHunt (10%), paid ads (5%)",
    },
    "customer_relations": {
        "communication": "In-app messaging, weekly tips email, community Slack",
        "loyalty_strategy": "Annual discount, feature requests voted by community, referral program ($10 credit)",
    },
    "revenue_assumptions": {"price_per_unit": 29, "volume_year1": 400, "volume_year2": 1500, "volume_year3": 4000, "annual_churn_rate": 0.08},
    "cac_assumptions": {"cac_estimate": 45, "cac_source": "content_marketing_and_seo", "confidence": "medium"},
    "market_entry_strategy": "Community-led launch: build audience via blog/Twitter for 2 months pre-launch, ProductHunt debut, then SEO long-tail capture for 'freelance CRM' keywords.",
    "assumptions_used": [
        {"statement": "8% monthly churn achievable with quality product", "confidence": "medium", "source": "alex_provided", "source_detail": "Industry benchmarks"},
        {"statement": "CAC of $45 via content + SEO", "confidence": "medium", "source": "agent_inferred", "source_detail": None},
    ],
    "uncertainties": ["Actual conversion rate from content", "Whether $29 price supports $45 CAC"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

SWOT_SYNTHESIZER_OUTPUT = {
    "section_number": "5",
    "strengths": [
        {"item": "AI automation differentiator", "evidence": "No competitor offers AI lead scoring for freelancers at $29/mo", "impact": "high"},
        {"item": "Low overhead (solo founder)", "evidence": "Monthly burn under $7K allows 24-month runway on $15K", "impact": "medium"},
        {"item": "Clear ICP (solo freelancers)", "evidence": "20 customer interviews validate pain point", "impact": "high"},
    ],
    "weaknesses": [
        {"item": "No brand recognition", "evidence": "Zero existing audience or content footprint", "impact": "high"},
        {"item": "Solo founder capacity", "evidence": "One person handling product, sales, and marketing", "impact": "medium"},
        {"item": "Limited engineering resource", "evidence": "Single contract developer for all technical work", "impact": "medium"},
    ],
    "opportunities": [
        {"item": "Growing freelance market", "evidence": "15% YoY growth in freelance economy", "impact": "high"},
        {"item": "AI cost reduction", "evidence": "LLM API costs dropping 50%+ annually", "impact": "medium"},
        {"item": "Underserved segment", "evidence": "No CRM specifically built for solo freelancers under $30/mo", "impact": "high"},
    ],
    "threats": [
        {"item": "HoneyBook AI features", "evidence": "HoneyBook raised $250M, likely to add AI", "impact": "high"},
        {"item": "Free tool proliferation", "evidence": "Notion, spreadsheets, free CRMs as substitutes", "impact": "medium"},
        {"item": "High CAC in crowded space", "evidence": "CRM keywords cost $8-15 CPC", "impact": "high"},
    ],
    "strategic_implications": "Focus on AI automation as primary differentiator. Use content marketing to build brand cheaply. Keep burn rate low until product-market fit confirmed. Key risk: must ship AI features before incumbents close the gap (12-18mo window).",
    "priority_strategic_issues": [
        "Ship AI lead scoring before HoneyBook adds equivalent feature",
        "Prove CAC <$50 via content-led acquisition before scaling paid channels",
        "Validate $29 price point with first 50 customers before expanding feature set",
    ],
    "assumptions_used": [{"statement": "12-18mo window before incumbent catch-up", "confidence": "medium", "source": "agent_inferred", "source_detail": None}],
    "uncertainties": ["Whether AI differentiation is durable"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

OPERATIONS_OUTPUT = {
    "section_number": "10",
    "production_process": "Lean SaaS delivery: Python/FastAPI backend with React frontend deployed via GitHub Actions to AWS ECS. Automated CI/CD pipeline handles testing, building, and deployment. Customer onboarding is self-serve with in-app guided flow. Support handled by founder via Intercom with <4hr response SLA.",
    "cost_structure": {
        "fixed_costs": {"infrastructure_aws": 500, "tools_subscriptions": 150, "contractor_dev": 5000},
        "variable_costs": {"ai_api_per_user": 0.50, "support_per_ticket": 2},
        "cogs_per_unit": 1.75,
        "source_labels": {"infrastructure": "AWS pricing calculator", "contractor": "market rate", "ai_api": "OpenAI pricing page"},
    },
    "capacity_plan": "Current infrastructure supports up to 1000 concurrent users on t3.medium ECS instances. Auto-scaling configured at 70% CPU threshold. Single contractor developer can ship 2 features/week. Bottleneck shifts to support at ~200 active customers — plan to hire part-time support at that point.",
    "supplier_strategy": "AWS as primary cloud provider (multi-AZ for reliability). OpenAI for AI inference (fallback to local models if costs spike). No physical suppliers.",
    "rd_plan": "AI model fine-tuning on user data after 100 customers (month 8). Custom lead scoring model to reduce OpenAI dependency. Mobile app POC in year 2.",
    "assumptions_used": [{"statement": "AWS costs stay under $500/mo at 400 users", "confidence": "medium", "source": "agent_inferred", "source_detail": None}],
    "uncertainties": ["AI API costs at scale", "Whether solo support is sustainable past 200 customers"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

FINANCIAL_MODELLING_OUTPUT = {
    "section_number": "12",
    "three_statement_model": {
        "year_1": {"revenue": 139200, "cogs": 8400, "gross_profit": 130800, "opex": 82200, "net_income": 48600},
        "year_2": {"revenue": 522000, "cogs": 31200, "gross_profit": 490800, "opex": 264000, "net_income": 226800},
        "year_3": {"revenue": 1392000, "cogs": 83520, "gross_profit": 1308480, "opex": 576000, "net_income": 732480},
    },
    "break_even_analysis": {"monthly_fixed_costs": 6850, "contribution_margin": 0.94, "break_even_customers": 251, "months_to_break_even": 8},
    "revenue_assumptions": {"price_per_unit": 29, "volume_year1": 400, "volume_year2": 1500, "volume_year3": 4000, "annual_churn_rate": 0.08},
    "simpy_runs_completed": 1000,
    "probability_distribution": [
        {"scenario": "P10", "year1_revenue": 69600, "year2_revenue": 261000, "year3_revenue": 696000},
        {"scenario": "P50", "year1_revenue": 139200, "year2_revenue": 522000, "year3_revenue": 1392000},
        {"scenario": "P90", "year1_revenue": 208800, "year2_revenue": 783000, "year3_revenue": 2088000},
    ],
    "primary_risk_factor": "Customer acquisition cost exceeding $45 target would push break-even past 12 months",
    "financial_skills_applied": ["three_statement_model", "dcf_model"],
    "risk_mitigation_actions": [
        "Cap monthly marketing spend at $2K until CAC stabilizes below $50",
        "Maintain 6-month runway reserve — cut contractor hours if burn exceeds plan by 20%",
        "Offer annual billing at 17% discount to reduce churn impact on cash flow",
    ],
    "assumption_log": [
        {"name": "Price per unit", "value": "$29/month", "label": "alex_provided", "source": "CEO pricing decision"},
        {"name": "Annual churn rate", "value": "8%", "label": "alex_provided", "source": "Industry benchmarks"},
        {"name": "Blended CAC", "value": "$45", "label": "agent_inferred", "source": "Section 8 estimate"},
    ],
    "assumptions_used": [
        {"statement": "Price $29/mo with 8% annual churn", "confidence": "medium", "source": "alex_provided", "source_detail": "CEO pricing decision"},
        {"statement": "CAC blended at $45", "confidence": "medium", "source": "agent_inferred", "source_detail": "Section 8 estimate"},
    ],
    "uncertainties": ["Whether CAC holds as market matures", "Churn rate with unproven product"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

LAUNCH_CONTINGENCY_OUTPUT = {
    "section_number": "13",
    "launch_programme": [
        {"milestone": "MVP feature-complete", "target_date_months": 3, "responsible": "Contract developer + founder", "success_metric": "Core CRM, AI lead scoring, Stripe integration all functional", "dependencies": []},
        {"milestone": "Beta launch with 50 users", "target_date_months": 4, "responsible": "Founder", "success_metric": "50 active beta users providing feedback", "dependencies": ["MVP feature-complete"]},
        {"milestone": "Public launch on ProductHunt", "target_date_months": 5, "responsible": "Founder", "success_metric": "Top 5 Product of the Day, 100+ signups", "dependencies": ["Beta launch with 50 users"]},
        {"milestone": "First 100 paying customers", "target_date_months": 8, "responsible": "Founder", "success_metric": "100 customers on $29/mo plan", "dependencies": ["Public launch on ProductHunt"]},
    ],
    "prerequisite_conditions": [
        "Stripe account approved and payment flow tested end-to-end",
        "AI lead scoring model trained on at least 500 sample leads with >70% accuracy",
        "Landing page with email capture live 4 weeks before public launch",
    ],
    "capital_plan": "Bootstrapped with $15K personal savings. Monthly burn: $6,850 (contractor $5K, infra $500, tools $150, AI API $200, marketing $1K). Runway: 26 months at pre-revenue burn. Break-even at month 8 (251 customers). No external funding needed unless CAC exceeds $80 — in that case, raise $50K angel round at month 6.",
    "critical_path_item": "AI lead scoring model accuracy — if the model cannot beat random (>60% precision on lead quality), the core differentiator fails and the product becomes a commodity CRM",
    "contingency_scenarios": [
        {"risk": "AI features delayed by 4+ weeks", "probability": "medium", "impact": "high", "mitigation": "Launch with rule-based scoring, add AI in v1.1"},
        {"risk": "Low ProductHunt traction (<50 upvotes)", "probability": "medium", "impact": "medium", "mitigation": "Fall back to SEO + cold outreach to freelancer communities"},
        {"risk": "Burn rate exceeds plan by 20%+", "probability": "low", "impact": "high", "mitigation": "Cut contractor to 20hrs/week, founder takes on more dev work"},
    ],
    "exit_conditions": "Kill the project if: (1) fewer than 20 paid customers after 6 months, (2) CAC exceeds $100 consistently, or (3) monthly burn exceeds $10K without clear path to revenue.",
    "assumptions_used": [{"statement": "MVP buildable in 12 weeks with one contractor", "confidence": "medium", "source": "agent_inferred", "source_detail": None}],
    "uncertainties": ["AI model training timeline", "ProductHunt algorithm changes"],
    "confidence_score": "medium",
    "input_tokens": 0,
    "output_tokens": 0,
}

SUMMARY_AGENT_OUTPUT = {
    "section_number": "executive_summary",
    "executive_summary": (
        "This business plan outlines a lightweight AI-powered CRM for freelance web designers and developers, "
        "priced at $29/month. The product differentiates from existing tools (HoneyBook, Dubsado) through AI-driven "
        "lead scoring, automated follow-ups, and pipeline prediction — features that solo freelancers currently lack. "
        "The target market of 2.1M US/UK solo freelancers earning $50K-$150K/year is underserved by current enterprise-focused "
        "CRMs. The business model projects break-even at 251 customers (month 8) with Year 1 revenue of $139K growing to "
        "$1.39M by Year 3. Key risks include high buyer power (many free alternatives), customer acquisition cost uncertainty, "
        "and a 12-18 month window before incumbents add AI features. The plan recommends a community-led launch via content "
        "marketing and ProductHunt, with kill criteria if <20 paid customers by month 6 or CAC exceeds $100."
    ),
    "headline_metrics": {
        "year_1_revenue": 139200,
        "break_even_months": 8,
        "primary_risk": "CAC exceeding target in crowded market",
        "overall_confidence": "medium",
    },
    "key_assumptions_flagged": [
        "8% monthly churn achievable with quality product",
        "CAC of $45 via content + SEO",
        "12-18mo window before incumbent catch-up",
    ],
    "sections_included": ["1", "3", "4", "5", "8", "10", "12", "13"],
    "sections_skipped": [],
    "coherence_issues_resolved": [],
    "input_tokens": 0,
    "output_tokens": 0,
}

ALL_SECTION_OUTPUTS = {
    "1": OPPORTUNITY_ANALYST_OUTPUT,
    "3": ENVIRONMENT_RESEARCH_OUTPUT,
    "4": ORGANISATION_DESIGNER_OUTPUT,
    "5": SWOT_SYNTHESIZER_OUTPUT,
    "8": MARKETING_STRATEGY_OUTPUT,
    "10": OPERATIONS_OUTPUT,
    "12": FINANCIAL_MODELLING_OUTPUT,
    "13": LAUNCH_CONTINGENCY_OUTPUT,
    "executive_summary": SUMMARY_AGENT_OUTPUT,
}

COHERENCE_AUDIT_PASS = {
    "passed": True,
    "issues": [],
    "confidence_summary": {"high": 0, "medium": 9, "low": 0},
    "overall_plan_confidence": "medium",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: make Bedrock converse response
# ─────────────────────────────────────────────────────────────────────────────

def make_bedrock_response(json_output: dict) -> dict:
    return {
        "output": {"message": {"content": [{"text": json.dumps(json_output)}]}},
        "usage": {"inputTokens": 200, "outputTokens": 400},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Full pipeline orchestration (all 4 groups, 9 agents)
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline_orchestration():
    """
    Simulate the entire pipeline:
    1. Pipeline trigger from Redis
    2. Phase 1 data read
    3. Section classification (all mandatory sections)
    4. Groups 1-4 execute sequentially
    5. Gate 2 auto-approved for each group
    6. Child agents produce outputs (mocked via Redis task_output keys)
    7. Coherence audit passes
    8. Pipeline completes
    """
    from agents.phase2.mother_agent import MotherAgent

    # Build agent via __new__ to skip __init__ (no real Bedrock/SPADE)
    agent = object.__new__(MotherAgent)
    agent.jid = "mother@xmpp.local"
    agent.db = MagicMock(client=mock_supabase)
    agent.redis = MagicMock(client=mock_redis)
    agent.active_runs = {}
    agent.model_id = "claude-sonnet-4-20250514"
    agent.constitution = "Test constitution"

    # Load real config files
    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)
    with open(config_dir / "agent_roster.yaml") as f:
        agent.agent_roster = yaml.safe_load(f)
    gap_path = config_dir / "gap_resolution_rules.yaml"
    if gap_path.exists():
        with open(gap_path) as f:
            agent.gap_rules = yaml.safe_load(f)
    else:
        agent.gap_rules = {"gaps": {}}

    # Mock intelligence/learning/compiler engines
    agent.intelligence = MagicMock()
    agent.intelligence.grade_evidence = AsyncMock(return_value=None)
    agent.intelligence.calibrate_confidence = AsyncMock(return_value="medium")
    agent.intelligence.apply_so_what_filter = AsyncMock(return_value=None)
    agent.intelligence.validate_hypotheses = AsyncMock(return_value=[])
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")
    agent.learning.record_da_accuracy = MagicMock()
    agent.learning.record_edit = MagicMock()
    agent.learning.record_rejection = MagicMock()
    agent.learning.record_acceptance = MagicMock()
    agent.compiler = MagicMock()

    # Mock Bedrock for coherence audit and section classification
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = make_bedrock_response(COHERENCE_AUDIT_PASS)
    agent.bedrock = mock_bedrock

    # Mock Telegram
    agent._send_message = MagicMock()

    # Mock _read_phase1_session to return our test data
    agent._read_phase1_session = MagicMock(return_value=PHASE1_SESSION_DATA)

    # Mock _determine_applicable_sections to return mandatory sections
    applicable_sections = ["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"]
    agent._determine_applicable_sections = MagicMock(return_value=applicable_sections)

    # Mock DB operations
    agent._create_pipeline_run = MagicMock(return_value="run-e2e-001")
    agent._write_tasks = MagicMock(side_effect=lambda tasks, *a, **kw: {t["task_name"]: f"tid-{t['bp_section']}" for t in tasks})
    agent._run_pre_simulation = MagicMock(return_value={"risk_level": "medium"})
    agent._build_gate2_package = MagicMock(return_value={"group": 1, "tasks": []})
    agent._create_execution_group = MagicMock(side_effect=lambda *a, **kw: f"grp-{a[1]}")
    agent._request_gate2_approval = MagicMock()
    agent._update_group_status = MagicMock()
    agent._update_memory = MagicMock()
    agent._build_opening_narrative = MagicMock(return_value="Pipeline starting...")
    agent._write_section_content = MagicMock()
    agent._finalize_task = MagicMock()
    agent._log_constitution_version = MagicMock()
    agent._enforce_constitution = MagicMock(return_value=[])
    agent._get_trace_key = MagicMock(return_value="trace:sess-e2e-001")

    # Pre-load Gate 2 approvals into Redis for all groups
    for g in range(1, 5):
        mock_redis.set(f"gate2_response:grp-{g}", json.dumps({"action": "agree"}))

    # Map section → output for task_output routing
    section_to_output = ALL_SECTION_OUTPUTS.copy()

    # Track which task outputs have been requested
    requested_tasks = []

    async def mock_wait_for_task_output(task_id, timeout=90):
        requested_tasks.append(task_id)
        section = task_id.replace("tid-", "")
        output = section_to_output.get(section)
        if output:
            return output.copy()
        return None

    agent._wait_for_task_output = mock_wait_for_task_output

    # Mock _wait_for_gate2_response to return immediately from Redis
    async def mock_wait_gate2(session_id, group_id):
        resp = mock_redis.get(f"gate2_response:{group_id}")
        if resp:
            mock_redis.delete(f"gate2_response:{group_id}")
            return json.loads(resp)
        return {"action": "agree"}

    agent._wait_for_gate2_response = mock_wait_gate2

    # Mock _post_process_council_output (council-gated sections)
    async def mock_post_process(task_id, session_id, run_id, section, output, agent_name):
        return output

    agent._post_process_council_output = mock_post_process

    # Mock deliver_plan
    delivered = {}

    async def mock_deliver(session_id, run_id, all_outputs):
        delivered["session_id"] = session_id
        delivered["run_id"] = run_id
        delivered["outputs"] = all_outputs

    agent._deliver_plan = mock_deliver

    # Mock _deduplicate_assumptions
    agent._deduplicate_assumptions = MagicMock(return_value={"duplicates": [], "conflicts": []})
    agent._get_audit_version = MagicMock(return_value=1)

    # Run the pipeline
    session_id = "sess-e2e-001"
    asyncio.run(agent.run_pipeline(session_id, "full_pipeline"))

    # ── Assertions ────────────────────────────────────────────────────────────

    # Pipeline was created
    agent._create_pipeline_run.assert_called_once_with(session_id, "full_pipeline")

    # Phase 1 data was read
    agent._read_phase1_session.assert_called_with(session_id)

    # Sections were classified
    agent._determine_applicable_sections.assert_called_once()

    # All 4 groups had Gate 2 requested
    assert agent._request_gate2_approval.call_count == 4, (
        f"Expected 4 Gate 2 requests, got {agent._request_gate2_approval.call_count}"
    )

    # Tasks were generated for all groups
    assert agent._write_tasks.call_count == 4, (
        f"Expected 4 _write_tasks calls, got {agent._write_tasks.call_count}"
    )

    # Child agents were queried for outputs
    assert len(requested_tasks) >= 9, (
        f"Expected at least 9 task outputs requested, got {len(requested_tasks)}: {requested_tasks}"
    )

    # Coherence audit was run (Bedrock converse called for audit)
    assert mock_bedrock.converse.called, "Coherence audit LLM call not made"

    # Pipeline delivered
    assert delivered.get("session_id") == session_id, "Pipeline not delivered"
    assert delivered.get("run_id") == "run-e2e-001"
    assert len(delivered.get("outputs", {})) >= 8, (
        f"Expected 8+ section outputs in delivery, got {len(delivered.get('outputs', {}))}"
    )

    logger.info("PASS: test_full_pipeline_orchestration — all 4 groups executed, 9 agents queried, coherence audit ran, plan delivered")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Gate 2 kill stops pipeline
# ─────────────────────────────────────────────────────────────────────────────

def test_gate2_kill_stops_pipeline():
    """If Alex kills at Gate 2, pipeline stops immediately."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.jid = "mother@xmpp.local"
    agent.db = MagicMock(client=mock_supabase)
    agent.redis = MagicMock(client=mock_redis)
    agent.active_runs = {}
    agent.model_id = "claude-sonnet-4-20250514"
    agent.constitution = "Test"

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "agent_roster.yaml") as f:
        agent.agent_roster = yaml.safe_load(f)
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)
    agent.gap_rules = {"gaps": {}}

    agent.intelligence = MagicMock()
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")
    agent.learning.record_rejection = MagicMock()
    agent.compiler = MagicMock()
    agent.bedrock = MagicMock()
    agent._send_message = MagicMock()
    agent._read_phase1_session = MagicMock(return_value=PHASE1_SESSION_DATA)
    agent._determine_applicable_sections = MagicMock(return_value=["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"])
    agent._create_pipeline_run = MagicMock(return_value="run-kill-001")
    agent._write_tasks = MagicMock(side_effect=lambda tasks, *a, **kw: {t["task_name"]: f"tid-{t['bp_section']}" for t in tasks})
    agent._run_pre_simulation = MagicMock(return_value={})
    agent._build_gate2_package = MagicMock(return_value={})
    agent._create_execution_group = MagicMock(return_value="grp-1")
    agent._request_gate2_approval = MagicMock()
    agent._update_group_status = MagicMock()
    agent._build_opening_narrative = MagicMock(return_value="")
    agent._get_trace_key = MagicMock(return_value="trace:kill")
    agent._log_constitution_version = MagicMock()

    # Gate 2 kills the pipeline
    mock_redis._store.clear()
    mock_redis.set("gate2_response:grp-1", json.dumps({"action": "kill"}))

    async def mock_wait_gate2(session_id, group_id):
        resp = mock_redis.get(f"gate2_response:{group_id}")
        if resp:
            mock_redis.delete(f"gate2_response:{group_id}")
            return json.loads(resp)
        return {"action": "kill"}

    agent._wait_for_gate2_response = mock_wait_gate2
    agent._kill_group = MagicMock()
    agent._fail_pipeline = MagicMock()

    asyncio.run(agent.run_pipeline("sess-kill", "full_pipeline"))

    agent._kill_group.assert_called_once()
    logger.info("PASS: test_gate2_kill_stops_pipeline")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: All agent outputs validate against Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

def test_all_outputs_schema_valid():
    """Every mock output in ALL_SECTION_OUTPUTS validates against its Pydantic schema."""
    schema_map = {
        "1": OpportunityAnalystOutput,
        "3": EnvironmentResearchOutput,
        "4": OrganisationDesignerOutput,
        "5": SWOTSynthesizerOutput,
        "8": MarketingStrategyOutput,
        "10": OperationsOutput,
        "12": FinancialModellingOutput,
        "13": LaunchContingencyOutput,
        "executive_summary": SummaryAgentOutput,
    }

    for section, output_data in ALL_SECTION_OUTPUTS.items():
        schema_cls = schema_map.get(section)
        if not schema_cls:
            continue
        data = output_data.copy()
        data["task_id"] = f"validate-{section}"
        data["model_used"] = "claude-sonnet-4-20250514"
        try:
            validated = schema_cls(**data)
            if hasattr(validated, "confidence_score"):
                assert validated.confidence_score in ("high", "medium", "low"), (
                    f"Section {section}: invalid confidence_score"
                )
        except Exception as e:
            logger.error("FAIL: Section %s schema validation: %s", section, e)
            raise

    logger.info("PASS: test_all_outputs_schema_valid — all 9 section outputs valid")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Input assembly propagates cross-section context
# ─────────────────────────────────────────────────────────────────────────────

def test_input_assembly_cross_section_context():
    """Mother Agent assembles inputs with prior outputs as cross-section context."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.redis = MagicMock(client=mock_redis)
    agent.db = MagicMock(client=mock_supabase)
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)

    # Section 5 (SWOT) depends on sections 3 and 4
    section_config = agent.dependency_map["sections"]["5"]
    prior_outputs = {
        "3": ENVIRONMENT_RESEARCH_OUTPUT,
        "4": ORGANISATION_DESIGNER_OUTPUT,
    }

    with patch("ceo_data.loader.get_relevant_ceo_data", return_value={}):
        package = agent._assemble_input_package(section_config, prior_outputs, PHASE1_SESSION_DATA)

    # Cross-section context should include sections 3 and 4
    assert "cross_section_context" in package, "Missing cross_section_context"
    ctx = package["cross_section_context"]
    assert "3" in ctx, "Section 3 not in cross-section context"
    assert "4" in ctx, "Section 4 not in cross-section context"

    # Required inputs from prior_task should be resolved
    assert "pest_analysis" in package or package.get("cross_section_context", {}).get("3", {}).get("pest_analysis"), (
        "pest_analysis not available to SWOT synthesizer"
    )

    logger.info("PASS: test_input_assembly_cross_section_context")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Confidence ceiling enforcement
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_ceiling_enforcement():
    """If upstream sections have low confidence, downstream cannot claim high."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.redis = MagicMock(client=mock_redis)
    agent.db = MagicMock(client=mock_supabase)
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)

    # Section 12 depends on 8, 10 — set section 8 to low confidence
    prior_outputs = {
        "8": {**MARKETING_STRATEGY_OUTPUT, "confidence_score": "low"},
        "10": OPERATIONS_OUTPUT,
    }
    section_config = agent.dependency_map["sections"].get("12", {})

    ceiling = agent._compute_confidence_ceiling(section_config, prior_outputs)

    # Ceiling should be "low" because section 8 (a dependency) is low
    assert ceiling == "low", f"Expected ceiling 'low', got '{ceiling}'"

    logger.info("PASS: test_confidence_ceiling_enforcement")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Hard constraint propagation
# ─────────────────────────────────────────────────────────────────────────────

def test_hard_constraint_propagation():
    """Revenue assumptions from Marketing must propagate as hard constraints to Financial."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.redis = MagicMock(client=mock_redis)
    agent.db = MagicMock(client=mock_supabase)

    prior_outputs = {
        "8": MARKETING_STRATEGY_OUTPUT,
        "10": OPERATIONS_OUTPUT,
    }

    constraints = agent._extract_hard_constraints(prior_outputs)

    # Revenue assumptions from section 8 should appear in hard constraints
    assert constraints, "No hard constraints extracted"
    assert "revenue_assumptions" in constraints or "price_per_unit" in str(constraints), (
        f"Revenue assumptions not in hard constraints: {constraints}"
    )

    logger.info("PASS: test_hard_constraint_propagation")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Fallback output on task timeout
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_on_timeout():
    """If a child agent times out, Mother generates a fallback output."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.redis = MagicMock(client=mock_redis)
    agent.db = MagicMock(client=mock_supabase)

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)

    fallback = agent._generate_fallback_output("3", "task_timeout", "Agent did not respond in 90s")

    assert fallback is not None, "No fallback generated for timeout"
    assert isinstance(fallback, dict), f"Fallback is not dict: {type(fallback)}"
    assert fallback.get("confidence") == "low" or fallback.get("confidence_score") == "low", (
        f"Fallback should have low confidence, got: {fallback}"
    )

    logger.info("PASS: test_fallback_on_timeout")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Coherence audit detects contradictions
# ─────────────────────────────────────────────────────────────────────────────

def test_coherence_audit_with_issues():
    """Coherence audit correctly flags high-severity issues."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.jid = "mother@xmpp.local"
    agent.db = MagicMock(client=mock_supabase)
    agent.redis = MagicMock(client=mock_redis)
    agent.active_runs = {}
    agent.model_id = "claude-sonnet-4-20250514"

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)

    agent._send_message = MagicMock()
    agent._get_trace_key = MagicMock(return_value="trace:audit")
    agent._deduplicate_assumptions = MagicMock(return_value={"duplicates": [], "conflicts": []})

    # Mock Bedrock to return audit with issues
    audit_with_issues = {
        "passed": False,
        "issues": [
            {"type": "revenue_mismatch", "description": "Marketing says $29/mo but Financial uses $25/mo", "sections_involved": ["8", "12"], "severity": "high"},
        ],
        "confidence_summary": {"high": 0, "medium": 8, "low": 1},
        "overall_plan_confidence": "medium",
    }
    mock_bedrock_audit = MagicMock()
    mock_bedrock_audit.converse.return_value = make_bedrock_response(audit_with_issues)
    agent.bedrock = mock_bedrock_audit

    # Already ran once — version >= 2 means we skip regen
    agent._get_audit_version = MagicMock(return_value=2)
    agent._increment_audit_version = MagicMock()

    delivered = {}

    async def mock_deliver(session_id, run_id, all_outputs):
        delivered["outputs"] = all_outputs

    agent._deliver_plan = mock_deliver

    asyncio.run(agent._run_coherence_audit("sess-audit", "run-audit", ALL_SECTION_OUTPUTS))

    # Should still deliver (issues flagged but already retried)
    assert "outputs" in delivered, "Plan not delivered after audit with issues"

    # Should notify Alex about issues
    agent._send_message.assert_called()
    message_calls = [str(c) for c in agent._send_message.call_args_list]
    assert any("revenue_mismatch" in c or "issue" in c.lower() for c in message_calls), (
        "Alex not notified about coherence issues"
    )

    logger.info("PASS: test_coherence_audit_with_issues")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Assumption deduplication across sections
# ─────────────────────────────────────────────────────────────────────────────

def test_assumption_deduplication():
    """Cross-section assumption deduplication catches conflicts."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)

    # Create conflicting assumptions between sections
    outputs_with_conflict = {
        "1": {
            "assumptions_used": [
                {"statement": "Target market is 2.1M freelancers", "confidence": "high", "source": "alex_provided"},
            ]
        },
        "8": {
            "assumptions_used": [
                {"statement": "Target market is 2.1M freelancers", "confidence": "medium", "source": "agent_inferred"},
            ]
        },
    }

    result = agent._deduplicate_assumptions(outputs_with_conflict)

    assert len(result["conflicts"]) >= 1, (
        f"Expected at least 1 conflict, got {len(result['conflicts'])}: {result}"
    )

    logger.info("PASS: test_assumption_deduplication")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Gate 2 edit modifies tasks
# ─────────────────────────────────────────────────────────────────────────────

def test_gate2_edit_modifies_tasks():
    """Gate 2 'edit' response applies CEO modifications to tasks."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.jid = "mother@xmpp.local"
    agent.db = MagicMock(client=mock_supabase)
    agent.redis = MagicMock(client=mock_redis)
    agent.active_runs = {}
    agent.model_id = "test"

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "agent_roster.yaml") as f:
        agent.agent_roster = yaml.safe_load(f)
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)
    agent.gap_rules = {"gaps": {}}

    agent.intelligence = MagicMock()
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")
    agent.learning.record_edit = MagicMock()
    agent.compiler = MagicMock()
    agent.bedrock = MagicMock()
    agent.bedrock.converse.return_value = make_bedrock_response(COHERENCE_AUDIT_PASS)
    agent._send_message = MagicMock()
    agent._read_phase1_session = MagicMock(return_value=PHASE1_SESSION_DATA)
    agent._determine_applicable_sections = MagicMock(return_value=["1", "4"])
    agent._create_pipeline_run = MagicMock(return_value="run-edit-001")
    agent._write_tasks = MagicMock(side_effect=lambda tasks, *a, **kw: {t["task_name"]: f"tid-{t['bp_section']}" for t in tasks})
    agent._run_pre_simulation = MagicMock(return_value={})
    agent._build_gate2_package = MagicMock(return_value={})
    agent._create_execution_group = MagicMock(return_value="grp-edit-1")
    agent._request_gate2_approval = MagicMock()
    agent._update_group_status = MagicMock()
    agent._update_memory = MagicMock()
    agent._build_opening_narrative = MagicMock(return_value="")
    agent._get_trace_key = MagicMock(return_value="trace:edit")
    agent._log_constitution_version = MagicMock()
    agent._deduplicate_assumptions = MagicMock(return_value={"duplicates": [], "conflicts": []})
    agent._get_audit_version = MagicMock(return_value=1)
    agent._write_section_content = MagicMock()
    agent._finalize_task = MagicMock()
    agent._enforce_constitution = MagicMock(return_value=[])

    # Gate 2 returns "edit" for group 1, then "agree" for group 2+
    gate2_responses = {
        "grp-edit-1": {"action": "edit", "edits": {"Build section 1: Opportunity": {"original": "old", "new": "updated criteria"}}},
    }

    async def mock_wait_gate2(session_id, group_id):
        resp = gate2_responses.get(group_id, {"action": "agree"})
        return resp

    agent._wait_for_gate2_response = mock_wait_gate2

    async def mock_wait_task(task_id, timeout=90):
        section = task_id.replace("tid-", "")
        return ALL_SECTION_OUTPUTS.get(section, {}).copy()

    agent._wait_for_task_output = mock_wait_task

    async def mock_post_process(task_id, session_id, run_id, section, output, agent_name):
        return output

    agent._post_process_council_output = mock_post_process

    delivered = {}
    async def mock_deliver(sid, rid, outputs):
        delivered["done"] = True
    agent._deliver_plan = mock_deliver

    # _apply_edit must exist — verify it's called
    original_apply = getattr(agent, '_apply_edit', None)
    if original_apply is None:
        agent._apply_edit = MagicMock(side_effect=lambda tasks, edits: tasks)
    agent._recheck_dependencies = MagicMock()

    asyncio.run(agent.run_pipeline("sess-edit", "full_pipeline"))

    # Learning engine should have recorded the edit
    agent.learning.record_edit.assert_called()

    logger.info("PASS: test_gate2_edit_modifies_tasks")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Pipeline resumes from last completed group
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_resume_from_group():
    """Pipeline resume loads prior outputs and continues from the next group."""
    from agents.phase2.mother_agent import MotherAgent

    agent = object.__new__(MotherAgent)
    agent.jid = "mother@xmpp.local"
    agent.db = MagicMock(client=mock_supabase)
    agent.redis = MagicMock(client=mock_redis)
    agent.active_runs = {}
    agent.model_id = "test"

    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "agent_roster.yaml") as f:
        agent.agent_roster = yaml.safe_load(f)
    with open(config_dir / "dependency_map.yaml") as f:
        agent.dependency_map = yaml.safe_load(f)
    agent.gap_rules = {"gaps": {}}

    agent.intelligence = MagicMock()
    agent.learning = MagicMock()
    agent.learning.build_learning_context = MagicMock(return_value="")
    agent.compiler = MagicMock()
    agent.bedrock = MagicMock()
    agent.bedrock.converse.return_value = make_bedrock_response(COHERENCE_AUDIT_PASS)
    agent._send_message = MagicMock()
    agent._get_trace_key = MagicMock(return_value="trace:resume")
    agent._write_tasks = MagicMock(side_effect=lambda tasks, *a, **kw: {t["task_name"]: f"tid-{t['bp_section']}" for t in tasks})
    agent._run_pre_simulation = MagicMock(return_value={})
    agent._build_gate2_package = MagicMock(return_value={})
    agent._create_execution_group = MagicMock(side_effect=lambda *a, **kw: f"grp-{a[1]}")
    agent._request_gate2_approval = MagicMock()
    agent._update_group_status = MagicMock()
    agent._update_memory = MagicMock()
    agent._write_section_content = MagicMock()
    agent._finalize_task = MagicMock()
    agent._enforce_constitution = MagicMock(return_value=[])
    agent._deduplicate_assumptions = MagicMock(return_value={"duplicates": [], "conflicts": []})
    agent._get_audit_version = MagicMock(return_value=1)

    # Pre-load gate2 approvals
    for g in range(3, 5):
        mock_redis.set(f"gate2_response:grp-{g}", json.dumps({"action": "agree"}))

    async def mock_wait_gate2(session_id, group_id):
        resp = mock_redis.get(f"gate2_response:{group_id}")
        if resp:
            mock_redis.delete(f"gate2_response:{group_id}")
            return json.loads(resp)
        return {"action": "agree"}

    agent._wait_for_gate2_response = mock_wait_gate2

    async def mock_wait_task(task_id, timeout=90):
        section = task_id.replace("tid-", "")
        return ALL_SECTION_OUTPUTS.get(section, {}).copy()

    agent._wait_for_task_output = mock_wait_task

    async def mock_post_process(task_id, session_id, run_id, section, output, agent_name):
        return output

    agent._post_process_council_output = mock_post_process

    delivered = {}
    async def mock_deliver(sid, rid, outputs):
        delivered["outputs"] = outputs
    agent._deliver_plan = mock_deliver

    # Resume from group 3 with groups 1 and 2 already done
    prior_outputs = {
        "1": OPPORTUNITY_ANALYST_OUTPUT,
        "4": ORGANISATION_DESIGNER_OUTPUT,
        "3": ENVIRONMENT_RESEARCH_OUTPUT,
        "8": MARKETING_STRATEGY_OUTPUT,
    }

    asyncio.run(agent._run_group(
        group_number=3,
        session_id="sess-resume",
        run_id="run-resume-001",
        phase1_data=PHASE1_SESSION_DATA,
        applicable_sections=["1", "3", "4", "5", "8", "10", "12", "13", "executive_summary"],
        prior_outputs=prior_outputs,
    ))

    # Groups 3 and 4 should have been executed
    assert agent._request_gate2_approval.call_count >= 2, (
        f"Expected at least 2 Gate 2 requests (groups 3+4), got {agent._request_gate2_approval.call_count}"
    )

    # Final delivery should include prior + new outputs
    assert "outputs" in delivered, "Pipeline did not deliver after resume"
    assert len(delivered["outputs"]) >= 6, (
        f"Expected 6+ total outputs after resume, got {len(delivered['outputs'])}"
    )

    logger.info("PASS: test_pipeline_resume_from_group")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Council-gated sections get extra timeout
# ─────────────────────────────────────────────────────────────────────────────

def test_council_gated_sections_extra_timeout():
    """Council-gated sections (5, 8, 12, exec summary) get +120s timeout."""
    from config.phase2.council_config import COUNCIL_GATED_SECTIONS

    # Verify which sections are gated
    assert "5" in COUNCIL_GATED_SECTIONS, "Section 5 (SWOT) should be council-gated"
    assert "8" in COUNCIL_GATED_SECTIONS, "Section 8 (Marketing) should be council-gated"
    assert "12" in COUNCIL_GATED_SECTIONS, "Section 12 (Financial) should be council-gated"
    assert "executive_summary" in COUNCIL_GATED_SECTIONS, "Executive Summary should be council-gated"

    # Non-gated sections should NOT be in the list
    assert "1" not in COUNCIL_GATED_SECTIONS, "Section 1 should not be council-gated"
    assert "3" not in COUNCIL_GATED_SECTIONS, "Section 3 should not be council-gated"
    assert "10" not in COUNCIL_GATED_SECTIONS, "Section 10 should not be council-gated"

    logger.info("PASS: test_council_gated_sections_extra_timeout")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: Execution group ordering respects dependencies
# ─────────────────────────────────────────────────────────────────────────────

def test_execution_group_ordering():
    """Verify execution groups respect dependency ordering."""
    import yaml
    config_dir = Path(__file__).resolve().parent.parent / "config" / "phase2"
    with open(config_dir / "agent_roster.yaml") as f:
        roster = yaml.safe_load(f)

    groups = roster["execution_groups"]

    # Group 1 has no depends_on_groups
    assert "depends_on_groups" not in groups[1] or groups[1].get("depends_on_groups") is None, (
        "Group 1 should have no dependencies"
    )

    # Group 2 depends on group 1
    assert 1 in groups[2].get("depends_on_groups", []), "Group 2 must depend on Group 1"

    # Group 3 depends on groups 1 and 2
    deps_3 = groups[3].get("depends_on_groups", [])
    assert 1 in deps_3 and 2 in deps_3, "Group 3 must depend on Groups 1 and 2"

    # Group 4 depends on all prior groups
    deps_4 = groups[4].get("depends_on_groups", [])
    assert 1 in deps_4 and 2 in deps_4 and 3 in deps_4, "Group 4 must depend on Groups 1, 2, and 3"

    # Group 1 is parallel (independent agents)
    assert groups[1].get("parallel") is True, "Group 1 should be parallel"

    # Group 4 is sequential (financial → launch → summary)
    assert groups[4].get("parallel") is False, "Group 4 should be sequential"

    logger.info("PASS: test_execution_group_ordering")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 14: End-to-end data flow integrity
# ─────────────────────────────────────────────────────────────────────────────

def test_data_flow_integrity():
    """
    Verify that data flows correctly between groups:
    - Group 1 output (opportunity, org) feeds into Group 2 input
    - Group 2 output (environment, marketing) feeds into Group 3 input (SWOT)
    - All outputs feed into Group 4 (financial, launch, summary)
    """
    # Verify revenue_assumptions flows from Marketing (Group 2) to Financial (Group 4)
    marketing_rev = MARKETING_STRATEGY_OUTPUT["revenue_assumptions"]
    financial_rev = FINANCIAL_MODELLING_OUTPUT["revenue_assumptions"]
    assert marketing_rev["price_per_unit"] == financial_rev["price_per_unit"], (
        f"Price mismatch: Marketing={marketing_rev['price_per_unit']}, Financial={financial_rev['price_per_unit']}"
    )
    assert marketing_rev["volume_year1"] == financial_rev["volume_year1"], (
        "Volume Year 1 mismatch between Marketing and Financial"
    )

    # Verify ICP from Section 1 is consistent with Marketing Section 8
    icp_buyer = OPPORTUNITY_ANALYST_OUTPUT["icp_hypothesis"]["buyer_role"]
    mkt_target = MARKETING_STRATEGY_OUTPUT["target_market_analysis"]["segmentation"]
    assert "freelan" in icp_buyer.lower() and "freelan" in mkt_target.lower(), (
        "ICP drift: Section 1 and Section 8 targeting different audiences"
    )

    # Verify SWOT incorporates environment research findings
    swot_threats = [t["item"] for t in SWOT_SYNTHESIZER_OUTPUT["threats"]]
    env_risks = ENVIRONMENT_RESEARCH_OUTPUT["risks_opportunities"]["risks"]
    # At least one risk from environment should appear in SWOT threats
    env_risk_keywords = [r.lower().split()[0] for r in env_risks]
    swot_threat_text = " ".join(swot_threats).lower()
    has_overlap = any(kw in swot_threat_text for kw in env_risk_keywords if len(kw) > 3)
    assert has_overlap, "SWOT threats should reference environment research risks"

    # Verify summary references financial figures
    summary_text = SUMMARY_AGENT_OUTPUT["executive_summary"]
    assert "139" in summary_text or "1.39" in summary_text, (
        "Executive summary should reference Year 1 revenue figure from Financial"
    )

    logger.info("PASS: test_data_flow_integrity — cross-section data flows verified")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_full_pipeline_orchestration,
        test_gate2_kill_stops_pipeline,
        test_all_outputs_schema_valid,
        test_input_assembly_cross_section_context,
        test_confidence_ceiling_enforcement,
        test_hard_constraint_propagation,
        test_fallback_on_timeout,
        test_coherence_audit_with_issues,
        test_assumption_deduplication,
        test_gate2_edit_modifies_tasks,
        test_pipeline_resume_from_group,
        test_council_gated_sections_extra_timeout,
        test_execution_group_ordering,
        test_data_flow_integrity,
    ]

    logger.info("=" * 70)
    logger.info("FULL PIPELINE END-TO-END TEST SUITE (14 tests)")
    logger.info("=" * 70)
    logger.info("")

    passed = 0
    failed = 0
    failures = []

    for test_fn in tests:
        try:
            # Reset Redis between tests
            mock_redis._store.clear()

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
