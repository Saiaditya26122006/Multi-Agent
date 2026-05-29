"""
Benchmark Data Collector — instruments the pipeline to capture data
needed by the IntelligenceBenchmark.

Wraps around a pipeline run and captures:
- All SPADE/ACL messages
- Reasoning traces from Intelligence Engine
- Fallback events (when agents use defaults)
- Negotiation attempts and outcomes
- Learning context injections
- Cross-section data flow

Usage:
    collector = BenchmarkCollector()
    collector.start(session_id)
    # ... run pipeline ...
    collector.stop()
    data = collector.export()
    # Feed to IntelligenceBenchmark
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class BenchmarkCollector:
    """Collects all data needed for the intelligence benchmark."""

    session_id: str = ""
    run_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    # Core data stores
    section_outputs: dict = field(default_factory=dict)
    reasoning_traces: dict = field(default_factory=dict)
    message_log: list = field(default_factory=list)
    learning_data: dict = field(default_factory=lambda: {
        "patterns": [],
        "recurring_errors": [],
        "context_injections": [],
        "run_scores_over_time": [],
    })
    fallback_events: list = field(default_factory=list)
    negotiation_log: list = field(default_factory=list)

    # Metadata
    test_idea: str = ""
    idea_input: dict = field(default_factory=dict)
    agent_configs: dict = field(default_factory=dict)

    def start(self, session_id: str, run_id: str, test_idea: str = "",
              idea_input: dict = None):
        """Begin collection for a pipeline run."""
        self.session_id = session_id
        self.run_id = run_id
        self.test_idea = test_idea
        self.idea_input = idea_input or {}
        self.start_time = time.time()
        logger.info(
            "[BenchmarkCollector] Started for session=%s run=%s",
            session_id, run_id,
        )

    def stop(self):
        """End collection."""
        self.end_time = time.time()
        logger.info(
            "[BenchmarkCollector] Stopped. Duration=%.1fs, Sections=%d, Messages=%d",
            self.end_time - self.start_time,
            len(self.section_outputs),
            len(self.message_log),
        )

    # ------------------------------------------------------------------
    # Message Tracking
    # ------------------------------------------------------------------

    def record_message(self, sender: str, receiver: str, performative: str,
                       content: dict, task_id: str = "",
                       status: str = "sent"):
        """Record an ACL message."""
        self.message_log.append({
            "sender": sender,
            "receiver": receiver,
            "performative": performative,
            "content": content,
            "task_id": task_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------
    # Section Output Tracking
    # ------------------------------------------------------------------

    def record_section_output(self, section: str, output: dict,
                              agent_name: str = ""):
        """Record a completed section output."""
        self.section_outputs[section] = output
        self.section_outputs["_idea_input"] = self.idea_input

    # ------------------------------------------------------------------
    # Reasoning Trace Tracking
    # ------------------------------------------------------------------

    def record_reasoning_trace(self, section: str, decomposition: str = "",
                               draft: str = "", challenge: str = "",
                               revision: str = "", revision_count: int = 0,
                               reasoning_budget: int = 3):
        """Record the Intelligence Engine's reasoning steps."""
        self.reasoning_traces[section] = {
            "decomposition": decomposition,
            "draft": draft,
            "challenge": challenge,
            "revision": revision,
            "revision_count": revision_count,
            "reasoning_budget": reasoning_budget,
        }

    # ------------------------------------------------------------------
    # Fallback Tracking
    # ------------------------------------------------------------------

    def record_fallback(self, section: str, output: dict,
                        reason: str = "", consumed_by: list = None):
        """Record when an agent falls back to defaults."""
        self.fallback_events.append({
            "section": section,
            "output": output,
            "reason": reason,
            "consumed_by_downstream": consumed_by or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------
    # Negotiation Tracking
    # ------------------------------------------------------------------

    def record_negotiation(self, initiator: str, responder: str,
                           claim: str, outcome: str,
                           rounds: int = 0, resolution: dict = None):
        """Record a negotiation attempt between agents."""
        self.negotiation_log.append({
            "initiator": initiator,
            "responder": responder,
            "claim": claim,
            "outcome": outcome,  # consensus, deadlock, escalated
            "rounds": rounds,
            "resolution": resolution or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------
    # Learning Tracking
    # ------------------------------------------------------------------

    def record_learning_pattern(self, section: int, root_cause: str,
                                anti_pattern: str, positive_pattern: str = "",
                                trigger_field: str = ""):
        """Record a learning pattern extraction."""
        self.learning_data["patterns"].append({
            "section": section,
            "root_cause": root_cause,
            "anti_pattern": anti_pattern,
            "positive_pattern": positive_pattern,
            "trigger_field": trigger_field,
        })

    def record_learning_context_injection(self, section: int, context: str):
        """Record what learning context was injected into an agent."""
        self.learning_data["context_injections"].append(context)

    def record_recurring_error(self, section: int, error_type: str, count: int):
        """Record a recurring error pattern."""
        self.learning_data["recurring_errors"].append({
            "section": section,
            "error_type": error_type,
            "count": count,
        })

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self) -> dict:
        """Export all collected data for benchmark consumption."""
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "test_idea": self.test_idea,
            "idea_input": self.idea_input,
            "duration_s": round(self.end_time - self.start_time, 1),
            "section_outputs": self.section_outputs,
            "reasoning_traces": self.reasoning_traces,
            "message_log": self.message_log,
            "learning_data": self.learning_data,
            "fallback_events": self.fallback_events,
            "negotiation_log": self.negotiation_log,
            "metadata": {
                "sections_produced": len(
                    [k for k in self.section_outputs if not k.startswith("_")]
                ),
                "total_messages": len(self.message_log),
                "fallback_count": len(self.fallback_events),
                "negotiation_count": len(self.negotiation_log),
            },
        }

    def save(self, filename: Optional[str] = None) -> Path:
        """Save collected data to JSON file."""
        if filename is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"benchmark_data_{timestamp}.json"

        path = RESULTS_DIR / filename
        with open(path, "w") as f:
            json.dump(self.export(), f, indent=2, default=str)

        logger.info("[BenchmarkCollector] Saved to %s", path)
        return path


# ------------------------------------------------------------------
# Integration helpers — patch into existing pipeline
# ------------------------------------------------------------------


def patch_send_acl(collector: BenchmarkCollector):
    """Returns a wrapper for send_acl that records messages.

    Usage:
        collector = BenchmarkCollector()
        original_send_acl = send_acl
        send_acl = patch_send_acl(collector)(original_send_acl)
    """
    def wrapper(original_fn):
        async def patched_send_acl(sender, to_jid, performative, content,
                                   task_id=None, session_id=None,
                                   pipeline_run_id=None):
            collector.record_message(
                sender=getattr(sender, "AGENT_NAME", str(sender)),
                receiver=to_jid,
                performative=performative,
                content=content,
                task_id=task_id or "",
            )
            return await original_fn(
                sender, to_jid, performative, content,
                task_id=task_id, session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
        return patched_send_acl
    return wrapper


def patch_intelligence_engine(collector: BenchmarkCollector):
    """Returns a wrapper for IE.reason_and_produce that records traces.

    Usage:
        collector = BenchmarkCollector()
        ie = IntelligenceEngine(...)
        ie.reason_and_produce = patch_intelligence_engine(collector)(ie)
    """
    def wrapper(ie_instance):
        original_fn = ie_instance.reason_and_produce

        async def patched_reason_and_produce(agent_role, input_data,
                                             output_schema_prompt,
                                             cross_section_context=None,
                                             reasoning_budget=3,
                                             learning_context="",
                                             **kwargs):
            result = await original_fn(
                agent_role=agent_role,
                input_data=input_data,
                output_schema_prompt=output_schema_prompt,
                cross_section_context=cross_section_context,
                reasoning_budget=reasoning_budget,
                learning_context=learning_context,
                **kwargs,
            )

            # Extract trace from result
            parsed, reasoning_trace, token_usage = result
            if isinstance(reasoning_trace, dict):
                section = input_data.get(
                    "section_number",
                    input_data.get("bp_section", "unknown"),
                )
                collector.record_reasoning_trace(
                    section=str(section),
                    decomposition=reasoning_trace.get("decomposition", ""),
                    draft=reasoning_trace.get("draft", ""),
                    challenge=reasoning_trace.get("challenge", ""),
                    revision=reasoning_trace.get("revision", ""),
                    revision_count=reasoning_trace.get("revision_count", 0),
                    reasoning_budget=reasoning_budget,
                )

            return result

        ie_instance.reason_and_produce = patched_reason_and_produce
        return ie_instance
    return wrapper


def patch_fallback(collector: BenchmarkCollector):
    """Returns a wrapper for _fallback_defaults that records events.

    Usage:
        collector = BenchmarkCollector()
        agent._fallback_defaults = patch_fallback(collector)(agent)
    """
    def wrapper(agent_instance):
        original_fn = agent_instance._fallback_defaults

        def patched_fallback(*args, **kwargs):
            output = original_fn(*args, **kwargs)
            collector.record_fallback(
                section=str(getattr(agent_instance, "SECTION_NUMBER", "?")),
                output=output,
                reason="LLM failure — fallback defaults used",
            )
            return output

        agent_instance._fallback_defaults = patched_fallback
        return agent_instance
    return wrapper
