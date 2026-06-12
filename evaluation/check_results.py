#!/usr/bin/env python3
"""
Check results from the latest grounded evaluation run.

Loads the most recent grounded_epistemic_os_*.json file from evaluation/results/
and displays a summary of section parsing status, confidence scores, and errors.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_latest_result_file() -> Optional[Path]:
    """Find the most recent grounded_epistemic_os_*.json file."""
    results_dir = Path(__file__).parent / "results"
    if not results_dir.exists():
        logger.error(f"Results directory not found: {results_dir}")
        return None

    pattern = "grounded_epistemic_os_*.json"
    files = sorted(results_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    if not files:
        logger.error(f"No result files found matching {pattern} in {results_dir}")
        return None

    return files[0]


def load_result_file(file_path: Path) -> Optional[Dict]:
    """Load and parse the JSON result file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return None


def check_results():
    """Main function to check and display evaluation results."""
    result_file = find_latest_result_file()
    if not result_file:
        sys.exit(1)

    logger.info(f"=" * 80)
    logger.info(f"GROUNDED EVALUATION RESULTS")
    logger.info(f"=" * 80)
    logger.info(f"File: {result_file.name}")
    logger.info(f"Path: {result_file}")
    logger.info("")

    data = load_result_file(result_file)
    if not data:
        sys.exit(1)

    # Extract metadata
    idea_name = data.get("idea_name", "Unknown")
    started_at = data.get("started_at", "Unknown")
    completed_at = data.get("completed_at", "Unknown")
    grounded = data.get("grounded", False)
    logger.info(f"Idea: {idea_name}")
    logger.info(f"Started: {started_at}")
    logger.info(f"Completed: {completed_at}")
    logger.info(f"Grounded with CEO data: {'Yes' if grounded else 'No'}")
    logger.info("")

    # Section order (11 sections)
    section_keys = [
        "1",           # Opportunity Analyst
        "3",           # Environment Research
        "4",           # Organisation Designer
        "5",           # SWOT Synthesizer
        "6.5",         # Tech Stack & Data Privacy
        "8",           # Marketing Strategy
        "10",          # Operations
        "12",          # Financial Modelling
        "13",          # Launch & Contingency
        "14",          # Exit Strategy
        "executive_summary"  # Summary Agent
    ]

    sections = data.get("sections", {})
    total_sections = len(section_keys)
    parsed_count = 0
    error_count = 0
    errors: List[str] = []

    logger.info(f"SECTION SUMMARY ({total_sections} sections)")
    logger.info(f"-" * 80)

    for key in section_keys:
        section_data = sections.get(key)

        if not section_data:
            logger.info(f"Section {key:20s} | ❌ MISSING")
            error_count += 1
            errors.append(f"Section {key} not found in results")
            continue

        parsed = section_data.get("parsed_successfully", False)
        agent_name = section_data.get("agent_name", "Unknown")
        latency = section_data.get("latency_seconds", 0)
        error_msg = section_data.get("error")

        status = "✓ PARSED" if parsed else "✗ FAILED"
        status_symbol = "✓" if parsed else "✗"

        if parsed:
            parsed_count += 1
            logger.info(
                f"Section {key:20s} | {status_symbol} {status:8s} | "
                f"{agent_name:30s} | {latency:.1f}s"
            )
        else:
            error_count += 1
            logger.info(
                f"Section {key:20s} | {status_symbol} {status:8s} | "
                f"Error: {error_msg or 'Unknown error'}"
            )
            if error_msg:
                errors.append(f"Section {key}: {error_msg}")

    logger.info("")
    logger.info(f"=" * 80)
    logger.info(f"TOTALS")
    logger.info(f"=" * 80)
    logger.info(f"Total sections:   {total_sections}")
    logger.info(f"Parsed:           {parsed_count} ({parsed_count/total_sections*100:.1f}%)")
    logger.info(f"Failed:           {error_count}")
    logger.info("")

    # Display errors if any
    if errors:
        logger.info(f"=" * 80)
        logger.info(f"ERRORS ({len(errors)})")
        logger.info(f"=" * 80)
        for idx, error in enumerate(errors, 1):
            logger.info(f"{idx}. {error}")
        logger.info("")

    # Display token usage
    input_tokens = data.get("total_input_tokens", 0)
    output_tokens = data.get("total_output_tokens", 0)
    total_tokens = input_tokens + output_tokens

    logger.info(f"=" * 80)
    logger.info(f"TOKEN USAGE")
    logger.info(f"=" * 80)
    logger.info(f"Input tokens:     {input_tokens:,}")
    logger.info(f"Output tokens:    {output_tokens:,}")
    logger.info(f"Total tokens:     {total_tokens:,}")
    logger.info("")

    # Display total time
    total_time = data.get("total_latency_seconds", 0)
    if total_time:
        minutes = int(total_time // 60)
        seconds = int(total_time % 60)
        logger.info(f"Total time:       {minutes}m {seconds}s")
        logger.info("")

    # Display scores if available
    scores = data.get("scores", {})
    if scores:
        logger.info(f"=" * 80)
        logger.info(f"SCORES")
        logger.info(f"=" * 80)
        for score_name, score_value in scores.items():
            logger.info(f"{score_name:30s} {score_value}")
        logger.info("")

    logger.info(f"=" * 80)

    # Exit with error code if any sections failed
    if error_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    check_results()
