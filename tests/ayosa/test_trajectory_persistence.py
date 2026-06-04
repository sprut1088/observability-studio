"""Step 16 \u2014 trajectory persistence + comparison.

Verifies:
 * iteration field on tool steps is round-tripped through SQLite
 * iterations + replan_reason are persisted on the run row
 * compare_runs surfaces a ``trajectory`` section with iteration counts,
   per-pass tools, and the "new tools per pass" delta
"""

from __future__ import annotations

from pathlib import Path

import pytest

from accelerators.ayosa.persistence import (
    Repository,
    init_db,
    persist_agent_result,
    set_default_repository,
)


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(init_db(tmp_path / "ayosa.db"))


def _steps_two_passes() -> list[dict]:
    return [
        {"index": 0, "tool": "prometheus",   "status": "done",  "iteration": 0},
        {"index": 1, "tool": "loki",         "status": "done",  "iteration": 0},
        {"index": 2, "tool": "alertmanager", "status": "done",  "iteration": 1},
        {"index": 3, "tool": "loki",         "status": "error", "iteration": 1, "error": "boom"},
    ]


class TestTrajectoryRoundTrip:
    def test_iteration_is_persisted_and_reloaded(self, repo: Repository):
        rid = repo.create_run(
            message="m",
            service="payments",
            tool_steps=_steps_two_passes(),
            iterations=2,
            replan_reason="LLM iterative re-plan: alertmanager, loki",
        )
        assert rid is not None
        run = repo.get_run(rid)
        assert run is not None
        assert run.iterations == 2
        assert "alertmanager" in (run.replan_reason or "")
        iterations = [s["iteration"] for s in run.tool_steps]
        assert iterations == [0, 0, 1, 1]
        # iteration is the primary sort key in get_run
        assert [s["tool"] for s in run.tool_steps] == [
            "prometheus", "loki", "alertmanager", "loki",
        ]

    def test_default_iteration_is_zero(self, repo: Repository):
        rid = repo.create_run(
            message="m",
            tool_steps=[
                {"index": 0, "tool": "prometheus", "status": "done"},
            ],
        )
        run = repo.get_run(rid)
        assert run is not None
        assert run.iterations == 1
        assert run.replan_reason is None
        assert run.tool_steps[0]["iteration"] == 0

    def test_list_runs_includes_iterations(self, repo: Repository):
        repo.create_run(message="m", iterations=3)
        summaries = repo.list_runs()
        assert summaries and summaries[0].iterations == 3


class TestPersistAgentResult:
    def test_persist_agent_result_carries_iteration_fields(self, repo: Repository):
        set_default_repository(repo)
        try:
            chat = {
                "intent": "error_investigation",
                "service": "payments",
                "time_range": "15m",
                "tools_used": ["prometheus", "loki"],
                "confidence": 0.7,
                "answer": "investigated",
                "plan": {"selected_tools": ["prometheus", "loki"]},
                "tool_steps": _steps_two_passes(),
                "iterations": 2,
                "replan_reason": "LLM iterative re-plan: alertmanager, loki",
            }
            rid = persist_agent_result(chat, request_message="why slow?")
            assert rid is not None
            run = repo.get_run(rid)
            assert run is not None
            assert run.iterations == 2
            assert run.replan_reason and "alertmanager" in run.replan_reason
            assert [s["iteration"] for s in run.tool_steps] == [0, 0, 1, 1]
        finally:
            set_default_repository(None)


class TestTrajectoryDiff:
    def test_compare_runs_surfaces_trajectory_section(self, repo: Repository):
        a = repo.create_run(
            message="yesterday",
            tool_steps=[
                {"index": 0, "tool": "prometheus", "status": "done", "iteration": 0},
            ],
            iterations=1,
        )
        b = repo.create_run(
            message="today",
            tool_steps=_steps_two_passes(),
            iterations=2,
            replan_reason="needed alerts + retry logs",
        )
        cmp = repo.compare_runs(a, b)
        diffs = cmp.differences
        assert "trajectory" in diffs
        traj = diffs["trajectory"]
        assert traj["iterations"] == {"left": 1, "right": 2}
        # right has 2 passes, left has 1
        assert len(traj["passes"]["right"]) == 2
        assert len(traj["passes"]["left"]) == 1
        # new tools added on pass 0 = loki; pass 1 = alertmanager + loki
        added = {p["iteration"]: p["new_tools"] for p in traj["new_tools_per_pass"]}
        assert "loki" in added.get(0, [])
        assert "alertmanager" in added.get(1, [])
        assert traj["replan_reason"]["right"] == "needed alerts + retry logs"

    def test_compare_identical_trajectories_has_no_trajectory_diff(
        self, repo: Repository
    ):
        steps = _steps_two_passes()
        a = repo.create_run(message="x", tool_steps=steps, iterations=2)
        b = repo.create_run(message="y", tool_steps=steps, iterations=2)
        cmp = repo.compare_runs(a, b)
        assert "trajectory" not in cmp.differences
