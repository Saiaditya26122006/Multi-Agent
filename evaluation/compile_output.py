"""
Compile evaluation results into Telegram-friendly summary.
"""

import json
from pathlib import Path


def compile_for_delivery(results_path: str) -> str:
    """
    Compile eval results into a Telegram-friendly summary (under 4096 chars).

    Args:
        results_path: Path to the results JSON file

    Returns:
        Formatted summary string for Telegram
    """
    try:
        with open(results_path) as f:
            data = json.load(f)
    except Exception as e:
        return f"⚠️ Error reading results: {e}"

    sections = data.get("sections", {})
    parsed = sum(
        1 for s in sections.values()
        if s.get("parsed_successfully")
    )
    total = len(sections)

    # Get executive summary if available
    exec_summary = sections.get("executive_summary", {}).get("output", {})
    exec_text = exec_summary.get("executive_summary", "")[:500] if exec_summary else ""

    # Count confidence distribution
    confidence = {}
    for s in sections.values():
        if s.get("parsed_successfully"):
            conf = s.get("output", {}).get("confidence_score", "unknown")
            confidence[conf] = confidence.get(conf, 0) + 1

    # Count total tokens
    total_tokens = data.get("total_input_tokens", 0) + data.get("total_output_tokens", 0)
    latency = data.get("total_latency_seconds", 0)

    lines = [
        "✅ *BUSINESS PLAN COMPLETE*",
        "",
        f"📊 Analysis: {parsed}/{total} sections generated",
        f"⏱️ Time: {latency:.1f}s | Tokens: {total_tokens:,}",
        "",
    ]

    if exec_text:
        lines.append("*Executive Summary:*")
        lines.append(exec_text)
        lines.append("")

    if confidence:
        conf_str = ", ".join(f"{k}: {v}" for k, v in sorted(confidence.items()))
        lines.append(f"*Confidence:* {conf_str}")
        lines.append("")

    # Extract key findings
    errors = data.get("errors", [])
    if errors:
        lines.append(f"⚠️ {len(errors)} section(s) had issues")
        lines.append("")

    # Add scores if available
    scores = data.get("scores", {})
    if scores:
        overall = scores.get("overall_score", "N/A")
        lines.append(f"*Overall Score:* {overall}/10")
        lines.append("")

    lines.append("Full report saved to:")
    lines.append(f"`{Path(results_path).name}`")
    lines.append("")
    lines.append("Review detailed outputs in the web interface.")

    result = "\n".join(lines)

    # Ensure under 4096 chars (Telegram limit)
    if len(result) > 4000:
        result = result[:3997] + "..."

    return result
