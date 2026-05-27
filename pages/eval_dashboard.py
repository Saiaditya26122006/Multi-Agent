"""
Evaluation Dashboard — visualize eval runs, compare baselines, track quality over time.
Run: streamlit run app.py (then navigate to this page)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "results"

st.set_page_config(page_title="Evaluation Dashboard", layout="wide")
st.title("Evaluation Dashboard")


def load_all_runs() -> list:
    """Load all evaluation run JSON files from results directory."""
    if not RESULTS_DIR.exists():
        return []
    runs = []
    for f in sorted(RESULTS_DIR.glob("eval_run_*.json"), reverse=True):
        try:
            with open(f) as fp:
                data = json.load(fp)
            if isinstance(data, list):
                for run in data:
                    run["_file"] = f.name
                runs.extend(data)
            elif isinstance(data, dict):
                data["_file"] = f.name
                runs.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load %s: %s", f.name, e)
    return runs


def render_run_selector(runs: list) -> dict:
    """Render a selectbox for choosing which run to inspect."""
    if not runs:
        return {}
    options = []
    for r in runs:
        ts = r.get("started_at", "")[:19]
        name = r.get("idea_name", "Unknown")
        score = r.get("scores", {}).get("overall_score", "?")
        label = f"{ts} — {name} (score: {score}/10)"
        options.append(label)
    idx = st.selectbox("Select a run to inspect", range(len(options)), format_func=lambda i: options[i])
    return runs[idx] if idx is not None else {}


def render_overview_metrics(runs: list):
    """Show aggregate metrics across all loaded runs."""
    if not runs:
        st.info("No evaluation results found. Run `python evaluation/eval_runner.py` to generate a baseline.")
        return

    total_runs = len(runs)
    avg_score = sum(r.get("scores", {}).get("overall_score", 0) for r in runs) / max(total_runs, 1)
    total_tokens = sum(r.get("total_input_tokens", 0) + r.get("total_output_tokens", 0) for r in runs)
    avg_latency = sum(r.get("total_latency_seconds", 0) for r in runs) / max(total_runs, 1)
    parse_rates = []
    for r in runs:
        sections = r.get("sections", {})
        if sections:
            ok = sum(1 for s in sections.values() if s.get("parsed_successfully"))
            parse_rates.append(ok / len(sections) * 100)

    avg_parse = sum(parse_rates) / max(len(parse_rates), 1)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Runs", total_runs)
    col2.metric("Avg Score", f"{avg_score:.1f}/10")
    col3.metric("Parse Rate", f"{avg_parse:.0f}%")
    col4.metric("Avg Latency", f"{avg_latency:.1f}s")
    col5.metric("Total Tokens", f"{total_tokens:,}")


def render_score_trend(runs: list):
    """Show score trend over time."""
    if len(runs) < 2:
        return

    st.subheader("Score Trend")
    data = []
    for r in runs:
        ts = r.get("started_at", "")
        score = r.get("scores", {}).get("overall_score", 0)
        if ts:
            data.append({"timestamp": ts[:19], "score": score, "idea": r.get("idea_name", "")})

    if data:
        import pandas as pd
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        st.line_chart(df.set_index("timestamp")["score"])


def render_section_heatmap(runs: list):
    """Show per-section score distribution as a heatmap-like table."""
    if not runs:
        return

    st.subheader("Section Scores Across Runs")

    section_names = {
        "1": "Opportunity",
        "3": "Environment",
        "5": "SWOT",
        "8": "Marketing",
        "12": "Financial",
        "13": "Launch",
    }

    rows = []
    for r in runs:
        scores = r.get("scores", {}).get("section_scores", {})
        row = {"Idea": r.get("idea_name", "?")[:25]}
        for sec_num, sec_name in section_names.items():
            sec_score = scores.get(sec_num, {})
            row[sec_name] = sec_score.get("total", 0)
        rows.append(row)

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.background_gradient(
                subset=list(section_names.values()),
                cmap="RdYlGn",
                vmin=0,
                vmax=10,
            ),
            use_container_width=True,
        )


def render_run_detail(run: dict):
    """Detailed view of a single evaluation run."""
    if not run:
        return

    st.subheader(f"Run Detail: {run.get('idea_name', 'Unknown')}")

    scores = run.get("scores", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Overall Score", f"{scores.get('overall_score', 0):.1f}/10")
    col2.metric("Schema Compliance", f"{scores.get('schema_compliance', 0)}%")
    col3.metric("Avg Confidence", scores.get("avg_confidence", "?"))
    col4.metric("Errors", len(run.get("errors", [])))

    conf_dist = scores.get("confidence_distribution", {})
    if conf_dist:
        st.markdown(
            f"**Confidence Distribution:** High: {conf_dist.get('high', 0)} | "
            f"Medium: {conf_dist.get('medium', 0)} | Low: {conf_dist.get('low', 0)}"
        )

    st.markdown("---")
    st.markdown("### Per-Section Breakdown")

    section_scores = scores.get("section_scores", {})
    sections_data = run.get("sections", {})

    for sec_num in sorted(sections_data.keys()):
        sec_data = sections_data[sec_num]
        sec_score = section_scores.get(sec_num, {})

        agent_name = sec_data.get("agent_name", f"Section {sec_num}")
        parsed = sec_data.get("parsed_successfully", False)
        latency = sec_data.get("latency_seconds", 0)
        tokens_in = sec_data.get("input_tokens", 0)
        tokens_out = sec_data.get("output_tokens", 0)

        status_icon = "+" if parsed else "x"
        with st.expander(
            f"[{status_icon}] Section {sec_num}: {agent_name} — "
            f"Score: {sec_score.get('total', 0)}/10 | {latency:.1f}s | {tokens_in + tokens_out} tokens"
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("Schema", f"{sec_score.get('schema_compliance', 0)}/10")
            c2.metric("Specificity", f"{sec_score.get('specificity', 0)}/10")
            c3.metric("Completeness", f"{sec_score.get('completeness', 0)}/10")

            issues = sec_score.get("issues", [])
            if issues:
                st.markdown("**Issues:**")
                for issue in issues[:10]:
                    st.markdown(f"- {issue}")

            if sec_data.get("output"):
                with st.expander("Raw Output JSON"):
                    st.json(sec_data["output"])

            trace = sec_data.get("reasoning_trace", {})
            if trace:
                with st.expander("Reasoning Trace"):
                    if trace.get("decomposition"):
                        st.markdown("**Decomposition:**")
                        st.text(trace["decomposition"][:1000])
                    if trace.get("challenge"):
                        st.markdown("**Challenge:**")
                        st.text(trace["challenge"][:1000])
                    st.markdown(f"**Revisions applied:** {trace.get('revisions_applied', False)}")
                    st.markdown(f"**Reasoning budget:** {trace.get('reasoning_budget', '?')}")

    if run.get("errors"):
        st.markdown("---")
        st.markdown("### Errors")
        for err in run["errors"]:
            st.error(err)


def render_comparison_tab(runs: list):
    """Allow comparing two runs side by side."""
    if len(runs) < 2:
        st.info("Need at least 2 runs to compare.")
        return

    st.subheader("Compare Two Runs")

    options = []
    for i, r in enumerate(runs):
        ts = r.get("started_at", "")[:19]
        name = r.get("idea_name", "Unknown")
        score = r.get("scores", {}).get("overall_score", "?")
        options.append(f"{ts} — {name} (score: {score}/10)")

    col1, col2 = st.columns(2)
    with col1:
        idx_a = st.selectbox("Baseline (A)", range(len(options)), format_func=lambda i: options[i], key="cmp_a")
    with col2:
        default_b = min(1, len(options) - 1)
        idx_b = st.selectbox("New (B)", range(len(options)), index=default_b, format_func=lambda i: options[i], key="cmp_b")

    if idx_a == idx_b:
        st.warning("Select two different runs to compare.")
        return

    run_a = runs[idx_a]
    run_b = runs[idx_b]

    scores_a = run_a.get("scores", {})
    scores_b = run_b.get("scores", {})

    overall_a = scores_a.get("overall_score", 0)
    overall_b = scores_b.get("overall_score", 0)
    delta = overall_b - overall_a

    tokens_a = run_a.get("total_input_tokens", 0) + run_a.get("total_output_tokens", 0)
    tokens_b = run_b.get("total_input_tokens", 0) + run_b.get("total_output_tokens", 0)
    token_delta = tokens_b - tokens_a

    latency_a = run_a.get("total_latency_seconds", 0)
    latency_b = run_b.get("total_latency_seconds", 0)
    latency_delta = latency_b - latency_a

    col1, col2, col3 = st.columns(3)
    col1.metric("Score Delta", f"{delta:+.1f}", delta_color="normal")
    col2.metric("Token Delta", f"{token_delta:+,}", delta_color="inverse")
    col3.metric("Latency Delta", f"{latency_delta:+.1f}s", delta_color="inverse")

    section_names = {"1": "Opportunity", "3": "Environment", "5": "SWOT", "8": "Marketing", "12": "Financial", "13": "Launch"}
    section_scores_a = scores_a.get("section_scores", {})
    section_scores_b = scores_b.get("section_scores", {})

    rows = []
    for sec_num, sec_name in section_names.items():
        sa = section_scores_a.get(sec_num, {}).get("total", 0)
        sb = section_scores_b.get(sec_num, {}).get("total", 0)
        d = sb - sa
        rows.append({"Section": sec_name, "A Score": sa, "B Score": sb, "Delta": f"{d:+.1f}"})

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)


def render_token_costs(runs: list):
    """Show token usage breakdown to estimate costs."""
    if not runs:
        return

    st.subheader("Token Usage & Cost Estimate")

    SONNET_INPUT_COST = 3.0 / 1_000_000  # $3 per 1M input tokens
    SONNET_OUTPUT_COST = 15.0 / 1_000_000  # $15 per 1M output tokens
    HAIKU_INPUT_COST = 0.80 / 1_000_000
    HAIKU_OUTPUT_COST = 4.0 / 1_000_000

    total_input = sum(r.get("total_input_tokens", 0) for r in runs)
    total_output = sum(r.get("total_output_tokens", 0) for r in runs)

    est_cost_sonnet = total_input * SONNET_INPUT_COST + total_output * SONNET_OUTPUT_COST
    est_cost_haiku = total_input * HAIKU_INPUT_COST + total_output * HAIKU_OUTPUT_COST
    est_cost_mixed = est_cost_sonnet * 0.6 + est_cost_haiku * 0.4

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Input Tokens", f"{total_input:,}")
    col2.metric("Total Output Tokens", f"{total_output:,}")
    col3.metric("Est. Cost (Mixed)", f"${est_cost_mixed:.2f}")
    col4.metric("Cost per Run", f"${est_cost_mixed / max(len(runs), 1):.3f}")

    st.caption(
        "Cost estimate assumes 60% Sonnet ($3/$15 per MTok) + 40% Haiku ($0.80/$4 per MTok). "
        "Actual cost depends on model split per run."
    )


def main():
    runs = load_all_runs()

    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Run Detail", "Compare", "Costs"])

    with tab1:
        render_overview_metrics(runs)
        render_score_trend(runs)
        render_section_heatmap(runs)

    with tab2:
        selected_run = render_run_selector(runs)
        render_run_detail(selected_run)

    with tab3:
        render_comparison_tab(runs)

    with tab4:
        render_token_costs(runs)


main()
