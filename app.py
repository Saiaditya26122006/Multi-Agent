"""
Streamlit dashboard for Phase 2 pipeline monitoring.
Run: streamlit run app.py
"""

import json
import logging
import os
from datetime import datetime, timedelta

import streamlit as st

from memory.supabase_client import SupabaseClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Phase 2 Pipeline Monitor", layout="wide")

st.title("Phase 2 — Pipeline Monitor")


@st.cache_resource
def get_db():
    return SupabaseClient()


db = get_db()


def load_pipeline_runs(limit: int = 10):
    try:
        result = db.client.table("pipeline_runs") \
            .select("*") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception as e:
        st.error(f"Failed to load pipeline runs: {e}")
        return []


def load_execution_groups(run_id: str):
    try:
        result = db.client.table("execution_groups") \
            .select("*") \
            .eq("pipeline_run_id", run_id) \
            .order("group_number") \
            .execute()
        return result.data or []
    except Exception as e:
        return []


def load_tasks(run_id: str):
    try:
        result = db.client.table("task_readiness") \
            .select("*") \
            .eq("pipeline_run_id", run_id) \
            .order("group_number") \
            .execute()
        return result.data or []
    except Exception as e:
        return []


def load_sections(run_id: str):
    try:
        result = db.client.table("bp_section_content") \
            .select("section_number, section_name, model_used, created_at") \
            .eq("pipeline_run_id", run_id) \
            .order("section_number") \
            .execute()
        return result.data or []
    except Exception as e:
        return []


# ── Sidebar: run selector ──────────────────────────────────────────────────
runs = load_pipeline_runs()

if not runs:
    st.info("No pipeline runs found. Start a pipeline to see data here.")
    st.stop()

st.sidebar.header("Pipeline Runs")
run_options = {
    f"{r['id'][:8]}… — {r.get('status', '?')} ({r.get('created_at', '')[:10]})": r["id"]
    for r in runs
}
selected_label = st.sidebar.selectbox("Select run", list(run_options.keys()))
selected_run_id = run_options[selected_label]
selected_run = next(r for r in runs if r["id"] == selected_run_id)

# ── Main: run overview ─────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)

status = selected_run.get("status", "unknown")
status_color = {"completed": "🟢", "running": "🔵", "failed": "🔴"}.get(status, "⚪")
col1.metric("Status", f"{status_color} {status}")

sections_completed = selected_run.get("sections_completed", [])
col2.metric("Sections", len(sections_completed) if sections_completed else 0)

total_in = selected_run.get("total_input_tokens", 0) or 0
total_out = selected_run.get("total_output_tokens", 0) or 0
col3.metric("Tokens (in/out)", f"{total_in:,} / {total_out:,}")

created = selected_run.get("created_at", "")
completed = selected_run.get("completed_at", "")
if created and completed:
    try:
        t_start = datetime.fromisoformat(created.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        duration = t_end - t_start
        col4.metric("Duration", str(duration).split(".")[0])
    except Exception:
        col4.metric("Duration", "—")
else:
    col4.metric("Duration", "in progress" if status == "running" else "—")

# ── Execution groups ───────────────────────────────────────────────────────
st.subheader("Execution Groups")
groups = load_execution_groups(selected_run_id)

if groups:
    for g in groups:
        g_status = g.get("status", "unknown")
        g_icon = {"completed": "✅", "running": "⏳", "awaiting_approval": "🔒", "killed": "❌"}.get(g_status, "⚪")
        with st.expander(f"{g_icon} Group {g.get('group_number', '?')} — {g.get('group_name', '')} [{g_status}]"):
            gate2 = g.get("gate2_package", {})
            if gate2 and isinstance(gate2, dict):
                tasks_in_gate = gate2.get("tasks", [])
                for t in tasks_in_gate:
                    st.text(f"  • {t.get('task_name', '')} → {t.get('owner', '')}")
            if g.get("started_at"):
                st.caption(f"Started: {g['started_at'][:19]}")
            if g.get("completed_at"):
                st.caption(f"Completed: {g['completed_at'][:19]}")
else:
    st.info("No execution groups yet.")

# ── Task status ────────────────────────────────────────────────────────────
st.subheader("Tasks")
tasks = load_tasks(selected_run_id)

if tasks:
    task_data = []
    for t in tasks:
        task_data.append({
            "Task": t.get("task_name", ""),
            "Section": t.get("bp_section", ""),
            "Owner": t.get("owner", ""),
            "Status": t.get("status", ""),
            "Group": t.get("group_number", ""),
        })
    st.dataframe(task_data, use_container_width=True)
else:
    st.info("No tasks dispatched yet.")

# ── Section outputs ────────────────────────────────────────────────────────
st.subheader("Completed Sections")
sections = load_sections(selected_run_id)

if sections:
    for s in sections:
        st.text(f"  Section {s.get('section_number', '?')} — {s.get('section_name', '')} (model: {s.get('model_used', '?')})")
else:
    st.info("No sections delivered yet.")

# ── Coherence audit ───────────────────────────────────────────────────────
coherence = selected_run.get("coherence_audit")
if coherence and isinstance(coherence, dict):
    st.subheader("Coherence Audit")
    audit_passed = coherence.get("passed", False)
    overall = coherence.get("overall_plan_confidence", "unknown")
    issues = coherence.get("issues", [])

    if audit_passed:
        st.success(f"Passed — overall confidence: {overall}")
    else:
        st.warning(f"Issues found — overall confidence: {overall}")
        for issue in issues:
            severity = issue.get("severity", "")
            sev_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(severity, "")
            st.text(f"  {sev_icon} [{issue.get('type', '')}] {issue.get('description', '')} — Sections {issue.get('sections_involved', [])}")
