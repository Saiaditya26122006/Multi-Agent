"""
Tests for Council Agent Telegram notifications — formatting and content.
"""

import pytest

from schemas.outputs.council_agent import CouncilVerdict, PersonaCritique


class TestNotificationFormatting:
    """Test Telegram message formatting for Alex."""

    def _format_deliberation(self, section_name: str, reviews: list, verdict: CouncilVerdict) -> str:
        """Replicate the formatting logic from council_agent."""
        icons = {
            "skeptic": "⚠️",
            "architect": "\U0001f3d7️",
            "visionary": "\U0001f4a1",
            "stranger": "❓",
            "operator": "\U0001f527",
        }
        lines = [f"\U0001f4cb Council Review: {section_name}\n"]
        for review in reviews:
            persona = review["persona"]
            icon = icons.get(persona, "•")
            finding = review["top_finding"][:120]
            severity = review.get("severity", "none")
            sev_tag = f" [{severity.upper()}]" if severity != "none" else ""
            lines.append(f"┊ {icon} {persona.title()}: {finding}{sev_tag}")

        if verdict.decision == "pass":
            lines.append(f"\n✅ Verdict: PASS (score {verdict.score:.1f}/10)")
        else:
            lines.append(f"\n\U0001f504 Verdict: REVISE ({verdict.critical_count} critical issues)")

        return "\n".join(lines)

    def test_pass_notification_format(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "Minor: no citation for 8% churn", "severity": "minor"},
            {"persona": "architect", "top_finding": "Consistent with prior sections", "severity": "none"},
            {"persona": "visionary", "top_finding": "Could explore marketplace model", "severity": "minor"},
            {"persona": "stranger", "top_finding": "All terms defined", "severity": "none"},
            {"persona": "operator", "top_finding": "Timeline is feasible", "severity": "none"},
        ]
        verdict = CouncilVerdict(
            decision="pass", score=8.5, critical_count=0, minor_count=2, feedback="", improvements=[]
        )
        msg = self._format_deliberation("Financial Plan", reviews, verdict)

        assert "Council Review: Financial Plan" in msg
        assert "PASS" in msg
        assert "8.5/10" in msg
        assert "Skeptic:" in msg
        assert "Operator:" in msg

    def test_revise_notification_format(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "Revenue assumption unsupported", "severity": "critical"},
            {"persona": "architect", "top_finding": "Contradicts marketing section", "severity": "critical"},
            {"persona": "visionary", "top_finding": "Thinking too small", "severity": "minor"},
            {"persona": "stranger", "top_finding": "DTC not defined", "severity": "minor"},
            {"persona": "operator", "top_finding": "Hire timeline impossible", "severity": "critical"},
        ]
        verdict = CouncilVerdict(
            decision="revise", score=4.0, critical_count=3, minor_count=2, feedback="Fix all", improvements=[]
        )
        msg = self._format_deliberation("SWOT Analysis", reviews, verdict)

        assert "REVISE" in msg
        assert "3 critical issues" in msg
        assert "SWOT Analysis" in msg

    def test_review_start_notification(self):
        msg = f"\U0001f50d Council is reviewing: Marketing Strategy"
        assert "Council is reviewing" in msg
        assert "Marketing Strategy" in msg

    def test_pass_after_revision_notification(self):
        verdict = CouncilVerdict(
            decision="pass", score=8.2, critical_count=0, minor_count=0,
            feedback="", improvements=["Added citation for churn rate", "Defined all acronyms"]
        )
        suffix = " (Revised)"
        improvements = "\nImprovements:\n" + "\n".join(f"• {i}" for i in verdict.improvements[:5])
        msg = f"✅ Council: Financial Plan{suffix} — Score {verdict.score:.1f}/10{improvements}"

        assert "(Revised)" in msg
        assert "8.2/10" in msg
        assert "Added citation" in msg

    def test_escalation_notification(self):
        verdict = CouncilVerdict(
            decision="revise", score=3.5, critical_count=3, minor_count=2,
            feedback="Fundamental issues remain", improvements=[]
        )
        msg = (
            f"⚠️ Council: SWOT Analysis hit max revisions — passing with warnings\n"
            f"Score: {verdict.score:.1f}/10\nRemaining issues: {verdict.feedback[:200]}"
        )
        assert "max revisions" in msg
        assert "3.5/10" in msg
        assert "Fundamental issues" in msg

    def test_message_length_reasonable(self):
        reviews = [
            {"persona": p, "top_finding": f"Finding for {p} " * 10, "severity": "minor"}
            for p in ["skeptic", "architect", "visionary", "stranger", "operator"]
        ]
        verdict = CouncilVerdict(
            decision="revise", score=5.0, critical_count=0, minor_count=5, feedback="Fix all", improvements=[]
        )
        msg = self._format_deliberation("Financial Plan", reviews, verdict)
        assert len(msg) < 2000
