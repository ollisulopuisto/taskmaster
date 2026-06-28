"""Streamlit frontend for TaskMaster Triage Helper.

Two tabs:
- Morning Triage: shows today's GCal schedule and the LLM's proposed 1-3-5 plan.
- Evening Debrief: check off what got done, roll incomplete items to tomorrow.
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:8000"


def fetch_morning_plan() -> dict | None:
    """Call the FastAPI morning endpoint. Returns None on failure."""
    try:
        response = requests.get(f"{API_BASE}/api/triage/morning", timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Failed to fetch morning plan: {exc}")
        return None


def submit_evening(completed_ids: list[str], rolled_over_ids: list[str]) -> bool:
    """Post completed/rolled-over tasks to the FastAPI evening endpoint."""
    try:
        response = requests.post(
            f"{API_BASE}/api/triage/evening",
            json={"completed_ids": completed_ids, "rolled_over_ids": rolled_over_ids},
            timeout=30,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        st.error(f"Failed to submit evening debrief: {exc}")
        return False


def render_morning_tab() -> None:
    st.header("Morning Triage")
    st.markdown("Today's schedule and your proposed 1-3-5 plan.")

    if st.button("Generate plan", type="primary"):
        with st.spinner("Asking the local LLM for today's plan..."):
            plan = fetch_morning_plan()
        if plan is not None:
            st.session_state["morning_plan"] = plan

    plan = st.session_state.get("morning_plan")
    if plan is None:
        st.info("Click **Generate plan** to start.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.subheader("Big (1)")
        for task in plan.get("big", []):
            st.checkbox(f"**{task['content']}**", key=f"big-{task['id']}")
    with col2:
        st.subheader("Medium (3)")
        for task in plan.get("medium", []):
            st.checkbox(task["content"], key=f"med-{task['id']}")
    with col3:
        st.subheader("Small (5)")
        for task in plan.get("small", []):
            st.checkbox(task["content"], key=f"small-{task['id']}")

    st.caption(f"Quadrant: {plan.get('quadrant')} · Domain: {plan.get('domain')}")


def render_evening_tab() -> None:
    st.header("Evening Debrief")
    st.markdown("Check off what you actually completed today.")

    plan = st.session_state.get("morning_plan")
    if plan is None:
        st.warning("Run the Morning Triage first.")
        return

    all_tasks = plan.get("big", []) + plan.get("medium", []) + plan.get("small", [])
    completed: list[str] = []
    st.subheader("Completed")
    for task in all_tasks:
        if st.checkbox(task["content"], key=f"done-{task['id']}"):
            completed.append(task["id"])

    rolled_over = [t["id"] for t in all_tasks if t["id"] not in completed]

    if st.button("Submit debrief", type="primary"):
        if submit_evening(completed, rolled_over):
            st.success(f"Logged {len(completed)} completed, {len(rolled_over)} rolled over.")


def main() -> None:
    st.set_page_config(page_title="TaskMaster", layout="wide")
    st.title("TaskMaster Triage Helper")

    morning_tab, evening_tab = st.tabs(["Morning Triage", "Evening Debrief"])
    with morning_tab:
        render_morning_tab()
    with evening_tab:
        render_evening_tab()


if __name__ == "__main__":
    main()
