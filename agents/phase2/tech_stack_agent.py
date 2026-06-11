import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import os
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from schemas.inputs.tech_stack import TechStackInput
from schemas.outputs.tech_stack import TechStackOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Tech Stack & Data Privacy agent in a multi-agent business plan system.
Your role: design the technical architecture, estimate infrastructure costs, and ensure regulatory compliance (GDPR, CCPA, DPDP).

## REASONING FRAMEWORK:

### 1. INFRASTRUCTURE DESIGN (Evidence-based, not aspirational)
- **Cloud Provider Selection**:
  - AWS: Best for LLM access (Bedrock), mature services, global coverage
  - Azure: Best for Microsoft integration, enterprise compliance
  - GCP: Best for ML/AI workloads, data analytics
  - Choose based on: LLM availability, data residency requirements, team expertise
  - Example: "EU business + Claude LLM → AWS eu-west-1 (Ireland) for Bedrock access"

- **Region Selection**:
  - GDPR compliance → EU regions ONLY (eu-west-1, eu-central-1)
  - CCPA compliance → US regions acceptable
  - Global → Multi-region (primary + failover)
  - NEVER store EU customer data in US regions (GDPR violation)

- **Cost Estimation**:
  - Base on expected usage: concurrent users, API calls/month, storage needs
  - Typical SaaS costs:
    - EC2/compute: $100-500/month (depends on traffic)
    - RDS/database: $50-300/month (depends on data volume)
    - LLM APIs: $50-500/month (depends on token usage)
    - S3/storage: $10-50/month
  - Include 20-30% buffer for spikes
  - Flag if monthly tech cost > 30% of monthly revenue (unsustainable)

### 2. AI/ML STACK (If business uses AI)
- **LLM Selection**:
  - Claude (Bedrock): Best for reasoning, European data residency
  - GPT-4 (OpenAI): Good performance, but US-based (GDPR risk)
  - Open-source (Llama, Mistral): Lower cost, but self-hosting overhead
- **Cost Calculation**:
  - Claude Sonnet: $3 per 1M input tokens, $15 per 1M output tokens
  - Haiku: $0.25 per 1M input, $1.25 per 1M output
  - Estimate monthly tokens: users × sessions × tokens_per_session
  - Example: 100 users × 10 sessions/month × 50K tokens = 50M tokens/month
- **Validation**:
  - If LLM cost > 40% of revenue → flag as unsustainable
  - If no rate limiting → flag as cost risk

### 3. DATABASE DESIGN
- **Primary Database**:
  - Postgres (Supabase, RDS): Best for relational data, ACID compliance
  - MongoDB: Best for document storage, flexible schema
  - MySQL: Good for standard web apps
- **Vector Database** (if AI/search features):
  - Pgvector (Postgres extension): Best for simplicity, low cost
  - Pinecone: Best for scale, but $70+/month
  - Weaviate: Open-source, self-hosted
- **Caching**:
  - Redis (Upstash): Best for session storage, rate limiting
  - Memcached: Simple caching
  - Cost: $10-50/month for startups

### 4. DATA PRIVACY & COMPLIANCE (CRITICAL for EU)
- **GDPR Compliance Checklist**:
  ✓ Data residency: EU servers only
  ✓ Encryption at rest: AES-256 (minimum)
  ✓ Encryption in transit: TLS 1.3 (minimum)
  ✓ User rights: Right to erasure, data portability, access
  ✓ Data Processing Agreement (DPA): Signed with all vendors
  ✓ Data Protection Officer (DPO): Required if >250 employees or sensitive data
  ✓ Consent management: GDPR Article 7 compliant
  ✓ Breach notification: 72-hour requirement
  ✓ Privacy policy: Published and accessible
  ✓ Cookie consent: Required for EU users

- **CCPA Compliance** (California):
  ✓ Right to know what data is collected
  ✓ Right to delete
  ✓ Right to opt-out of data sales
  ✓ "Do Not Sell My Personal Information" link

- **DPDP Compliance** (India):
  ✓ Data fiduciary registration
  ✓ Consent manager integration
  ✓ Data localization (for sensitive data)

### 5. AUTHENTICATION & SECURITY
- **Auth Providers**:
  - Auth0: Enterprise-grade, GDPR-compliant, $23+/month
  - Clerk: Developer-friendly, GDPR-compliant, free tier
  - Supabase Auth: Integrated with DB, GDPR-compliant, free tier
  - Custom JWT: Lowest cost, highest maintenance
- **Security Requirements**:
  - Multi-factor authentication (MFA) for admin accounts
  - Role-based access control (RBAC)
  - Audit logs for all data access
  - Password hashing: bcrypt or Argon2 (NEVER MD5)

### 6. THIRD-PARTY APIS
- **Common APIs**:
  - Email: SendGrid ($15+/month), Resend ($20/month)
  - Payments: Stripe (2.9% + $0.30 per transaction)
  - Search: Tavily ($10-100/month), Algolia ($1+/month)
  - Analytics: PostHog (free tier), Mixpanel ($25+/month)
- **Vendor Risk**:
  - Each vendor MUST have DPA signed (GDPR requirement)
  - Check data residency: US vendors may not be GDPR-compliant
  - Flag if >5 vendors (integration complexity risk)

### 7. COST VALIDATION RULES
- Total monthly tech cost should be:
  - Pre-revenue: <$500/month (bootstrap)
  - <$10K MRR: <$1K/month (<10% of revenue)
  - <$100K MRR: <$10K/month (<10% of revenue)
- If tech cost > 30% of revenue → FLAG as "unsustainable burn rate"
- If tech cost > revenue → FLAG as "FATAL — business cannot operate profitably"

### 8. ANTI-PATTERNS (Never write these)
- NEVER say "cloud-agnostic" without specifying the abstraction layer (Kubernetes, Terraform)
- NEVER claim "GDPR-compliant" without listing specific controls
- NEVER estimate costs without usage assumptions
- NEVER recommend US-only vendors for EU businesses without flagging GDPR risk
- NEVER claim "enterprise-grade security" — state specific measures (encryption, MFA, auditing)

## OUTPUT REQUIREMENTS:
You must return ONLY valid JSON with these exact fields:
- section_number: "6.5"
- infrastructure: {cloud_provider, regions, estimated_monthly_cost, key_services}
- ai_ml_stack: {primary_llm, cost_per_1m_tokens, estimated_monthly_tokens, estimated_monthly_cost}
- database: {primary_db, vector_db, cache, total_monthly_cost}
- third_party_apis: [{name, purpose, monthly_cost}]
- authentication: {provider, approach, gdpr_compliant}
- data_privacy_compliance: {regulations_covered, data_residency, encryption, user_rights, dpa_signed, dpo_appointed}
- total_tech_cost_monthly: <float>
- total_tech_cost_annual: <float>
- tech_risk_assessment: {scalability_concerns, vendor_lock_in, compliance_gaps}
- assumptions_used: [{statement, confidence, source, source_detail}]
- uncertainties: [<string>]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0

No markdown, no code blocks, no preamble — ONLY the JSON object.
"""


class ListenBehaviour(CyclicBehaviour):
    async def run(self):
        msg = await self.receive(timeout=5)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        task_id = msg.get_metadata("task_id")
        session_id = msg.get_metadata("session_id")
        pipeline_run_id = msg.get_metadata("pipeline_run_id")
        content = json.loads(msg.body)

        if performative == "request":
            await self.agent.handle_request(task_id, session_id, pipeline_run_id, content)


class TechStackAgent(Agent):
    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[TechStack] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "tech_stack")

    async def _send_msg(self, msg: Message):
        class _Send(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _Send()
        self.add_behaviour(b)
        await b.join(timeout=10)

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = TechStackInput(
                task_id=task_id,
                session_id=session_id,
                business_type=input_package.get("business_type", ""),
                product_description=input_package.get("product_description", ""),
                team_capabilities=input_package.get("team_capabilities"),
                technology_requirements=input_package.get("technology_requirements"),
                delivery_model=input_package.get("delivery_model"),
                infrastructure_needs=input_package.get("infrastructure_needs"),
                target_geography=input_package.get("target_geography"),
                data_sensitivity=input_package.get("data_sensitivity"),
                compliance_requirements=input_package.get("compliance_requirements"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[TechStack] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        input_data = {
            "business_type": input_package.get("business_type", ""),
            "product_description": input_package.get("product_description", ""),
            "team_capabilities": input_package.get("team_capabilities"),
            "delivery_model": input_package.get("delivery_model"),
            "target_geography": input_package.get("target_geography", "EU"),
            "compliance_requirements": input_package.get("compliance_requirements", ["GDPR"]),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Tech Stack & Data Privacy — you design the technical architecture, "
                "estimate infrastructure costs, and ensure GDPR/CCPA/DPDP compliance"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=3,
            learning_context=learning_context,
        )

        if not parsed:
            user_message = self._build_prompt(validated_input)
            llm_response, fallback_usage = await self._call_llm(user_message)
            if not llm_response:
                await self._escalate(task_id, session_id, pipeline_run_id, "weak_evidence", "Intelligence engine and fallback both failed")
                return
            parsed = self._parse_llm_response(llm_response, validated_input)
            token_usage["input_tokens"] = token_usage.get("input_tokens", 0) + fallback_usage.get("input_tokens", 0)
            token_usage["output_tokens"] = token_usage.get("output_tokens", 0) + fallback_usage.get("output_tokens", 0)

        try:
            parsed["task_id"] = task_id
            parsed["model_used"] = self.model_id
            parsed["input_tokens"] = token_usage.get("input_tokens", 0)
            parsed["output_tokens"] = token_usage.get("output_tokens", 0)
            validated_output = TechStackOutput(**parsed)
        except Exception as e:
            logger.error("[TechStack] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    def _build_schema_prompt(self) -> str:
        return """Return JSON with:
- section_number: "6.5"
- infrastructure, ai_ml_stack, database, third_party_apis, authentication
- data_privacy_compliance, total_tech_cost_monthly, total_tech_cost_annual
- tech_risk_assessment, assumptions_used, uncertainties, confidence_score
- input_tokens, output_tokens"""

    def _build_prompt(self, inp: TechStackInput) -> str:
        return f"""Design tech stack and ensure data privacy compliance.

BUSINESS TYPE: {inp.business_type}
PRODUCT: {inp.product_description}
GEOGRAPHY: {inp.target_geography or 'EU'}
COMPLIANCE: {inp.compliance_requirements or ['GDPR']}

Deliver JSON matching the schema."""

    def _parse_llm_response(self, raw: str, inp: TechStackInput) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="TechStack",
            max_tokens=4096,
        )
        if result is not None:
            return result

        logger.warning("[TechStack] Parse failed, constructing fallback")
        return {
            "section_number": "6.5",
            "infrastructure": {
                "cloud_provider": "AWS",
                "regions": ["eu-west-1"],
                "estimated_monthly_cost": 300,
                "key_services": ["EC2", "RDS", "S3", "Bedrock"]
            },
            "ai_ml_stack": {
                "primary_llm": "Claude via Bedrock",
                "cost_per_1m_tokens": 3.0,
                "estimated_monthly_tokens": 10000000,
                "estimated_monthly_cost": 30
            },
            "database": {
                "primary_db": "Postgres (Supabase)",
                "vector_db": "Pgvector",
                "cache": "Redis (Upstash)",
                "total_monthly_cost": 100
            },
            "third_party_apis": [
                {"name": "SendGrid", "purpose": "Email delivery", "monthly_cost": 15},
                {"name": "Stripe", "purpose": "Payments", "monthly_cost": 0}
            ],
            "authentication": {
                "provider": "Supabase Auth",
                "approach": "JWT with MFA",
                "gdpr_compliant": True
            },
            "data_privacy_compliance": {
                "regulations_covered": ["GDPR"],
                "data_residency": "EU only (Ireland)",
                "encryption": {"at_rest": "AES-256", "in_transit": "TLS 1.3"},
                "user_rights": ["Right to erasure", "Data portability", "Access"],
                "dpa_signed": False,
                "dpo_appointed": False
            },
            "total_tech_cost_monthly": 445,
            "total_tech_cost_annual": 5340,
            "tech_risk_assessment": {
                "scalability_concerns": "Vendor limits at 10K users/month",
                "vendor_lock_in": "Moderate — AWS Bedrock dependency",
                "compliance_gaps": ["DPA not signed", "No DPO appointed"]
            },
            "assumptions_used": [
                {"statement": "LLM usage estimated at 10M tokens/month", "confidence": "low", "source": "assumed", "source_detail": "No usage data"}
            ],
            "uncertainties": [
                "Actual cloud costs depend on traffic",
                "LLM token usage unknown until launch"
            ],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0
        }

    async def _call_llm(self, user_message: str) -> tuple[Optional[str], dict]:
        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": 4096},
            )
            usage = response.get("usage", {})
            text = response["output"]["message"]["content"][0]["text"]
            return text, {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)}
        except Exception as e:
            logger.error("[TechStack] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "6.5", "gap_key": "tech_stack"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "output": output,
            "section_number": "6.5",
            "agent_name": "tech_stack",
        })
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("TECH_STACK_JID")
    password = os.getenv("TECH_STACK_PASSWORD")
    if not jid or not password:
        raise ValueError("TECH_STACK_JID and PASSWORD must be set")
    agent = TechStackAgent(jid=jid, password=password)
    await agent.start(auto_register=True)
    try:
        while agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
