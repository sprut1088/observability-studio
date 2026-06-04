"""Step 21 — loop_summary persistence on ``ayosa_runs``.

Covers:
  * ``create_run(loop_summary=...)`` writes the JSON column.
  * ``get_run`` round-trips it onto ``PersistedRun.loop_summary``.
  * ``record_loop_summary`` updates an existing row in place.
  * ``compare_runs`` exposes the delta inside ``differences.trajectory.loop_summary``.
  * ``persist_agent_result`` forwards a ``chat_response.loop_summary`` dict.
"""

from __future__ import annotations

from pathlib import Path

from accelerators.ayosa.persistence.db import init_db
from accelerators.ayosa.persistence.repository import (
    Repository,
    persist_agent_result,
)


def _repo(tmp_path: Path) -> Repository:
    return Repository(init_db(tmp_path / "ayosa.db"))


def test_create_run_persists_loop_summary(tmp_path):
    repo = _repo(tmp_path)
    summary = {
        "iterations_run": 2,
        "max_iterations": 4,
        "replanned": True,
        "replan_reason": "LLM iterative re-plan: alertmanager",
    }
    rid = repo.create_run(
        session_id="s1",
        service="payments",
        message="any errors?",
        iterations=2,
        replan_reason=summary["replan_reason"],
        loop_summary=summary,
    )
    assert rid is not None

    fetched = repo.get_run(rid)
    assert fetched is not None
    assert fetched.loop_summary == summary


def test_create_run_with_no_loop_summary_round_trips_none(tmp_path):
    repo = _repo(tmp_path)
    rid = repo.create_run(session_id="s1", message="x")
    fetched = repo.get_run(rid)
    assert fetched is not None
    assert fetched.loop_summary is None


def test_record_loop_summary_updates_existing_row(tmp_path):
    repo = _repo(tmp_path)
    rid = repo.create_run(session_id="s1", message="x")
    assert repo.get_run(rid).loop_summary is None

    summary = {
        "iterations_run": 1,
        "max_iterations": 1,
        "replanned": False,
        "replan_reason": None,
    }
    assert repo.record_loop_summary(rid, summary) is True
    assert repo.get_run(rid).loop_summary == summary


def test_record_loop_summary_clears_when_passed_none(tmp_path):
    repo = _repo(tmp_path)
    rid = repo.create_run(
        session_id="s1",
        message="x",
        loop_summary={"iterations_run": 3, "max_iterations": 4, "replanned": True, "replan_reason": "x"},
    )
    assert repo.get_run(rid).loop_summary is not None
    assert repo.record_loop_summary(rid, None) is True
    assert repo.get_run(rid).loop_summary is None


def test_record_loop_summary_returns_false_for_missing_run(tmp_path):
    repo = _repo(tmp_path)
    assert repo.record_loop_summary("does-not-exist", {"x": 1}) is False


def test_compare_runs_surfaces_loop_summary_delta(tmp_path):
    repo = _repo(tmp_path)
    a = repo.create_run(
        session_id="s1", service="payments", message="m",
        iterations=1,
        loop_summary={"iterations_run": 1, "max_iterations": 4, "replanned": False, "replan_reason": None},
    )
    b = repo.create_run(
        session_id="s1", service="payments", message="m",
        iterations=2, replan_reason="LLM iterative re-plan",
        loop_summary={"iterations_run": 2, "max_iterations": 4, "replanned": True, "replan_reason": "LLM iterative re-plan"},
    )
    cmp = repo.compare_runs(a, b)
    traj = cmp.differences.get("trajectory") or {}
    assert "loop_summary" in traj
    assert traj["loop_summary"]["left"]["replanned"] is False
    assert traj["loop_summary"]["right"]["replanned"] is True


def test_persist_agent_result_forwards_loop_summary(tmp_path):
    repo = _repo(tmp_path)
    chat_response = {
        "session_id": "s1",
        "intent": "error_investigation",
        "service": "payments",
        "time_range": "15m",
        "confidence": 0.7,
        "answer": "ok",
        "iterations": 2,
        "replan_reason": "LLM iterative re-plan",
        "loop_summary": {
            "iterations_run": 2,
            "max_iterations": 4,
            "replanned": True,
            "replan_reason": "LLM iterative re-plan",
        },
        "plan": {"selected_tools": ["prometheus"]},
        "tool_steps": [],
        "evidence": [],
        "missing_signals": [],
        "signal_coverage": {},
    }
    rid = persist_agent_result(chat_response, request_message="m", repo=repo)
    assert rid is not None
    fetched = repo.get_run(rid)
    assert fetched.loop_summary == chat_response["loop_summary"]
