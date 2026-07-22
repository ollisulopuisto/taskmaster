"""Tests for Task and TriagePlan models and pure Python reordering logic."""

from datetime import date

from src.models.task import Task, TriagePlan


def test_task_duration_and_overdue_fields():
    t = Task(
        id="t1",
        content="Write report",
        project_id="p1",
        due_date=date(2026, 7, 20),
        duration_minutes=45,
        is_overdue=True,
        days_overdue=2,
    )
    assert t.duration_minutes == 45
    assert t.is_overdue is True
    assert t.days_overdue == 2
    assert t.is_stale is False

    t_stale = Task(
        id="t2",
        content="Old task",
        project_id="p1",
        is_overdue=True,
        days_overdue=10,
    )
    assert t_stale.is_stale is True


def test_triage_plan_has_postponed_list():
    plan = TriagePlan()
    assert plan.postponed == []


def test_reassign_task_moves_task_between_slots():
    t1 = Task(id="1", content="Task 1", project_id="p1")
    t2 = Task(id="2", content="Task 2", project_id="p1")
    t3 = Task(id="3", content="Task 3", project_id="p1")

    plan = TriagePlan(big=[t1], medium=[t2], postponed=[t3])

    # Reassign t3 (from postponed) to big -> t1 should demote to medium
    updated = plan.reassign_task("3", "big")
    assert [t.id for t in updated.big] == ["3"]
    assert [t.id for t in updated.medium] == ["1", "2"]
    assert [t.id for t in updated.postponed] == []


def test_reassign_task_overflows_medium_to_small():
    t_big = Task(id="b", content="Big", project_id="p1")
    t_m1 = Task(id="m1", content="M1", project_id="p1")
    t_m2 = Task(id="m2", content="M2", project_id="p1")
    t_m3 = Task(id="m3", content="M3", project_id="p1")
    t_post = Task(id="p", content="Postponed", project_id="p1")

    plan = TriagePlan(big=[t_big], medium=[t_m1, t_m2, t_m3], postponed=[t_post])

    # Move t_post to medium (which is already full with 3 tasks) -> t_m3 should demote to small
    updated = plan.reassign_task("p", "medium")
    assert [t.id for t in updated.medium] == ["p", "m1", "m2"]
    assert [t.id for t in updated.small] == ["m3"]
    assert [t.id for t in updated.postponed] == []
