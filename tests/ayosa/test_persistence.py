"""Tests for AYOSA SQLite persistence.

Covers:
- DB bootstrap creates every required table
- create_run / get_run round-trip preserves all fields
- list_runs filters by session_id / service, honours `limit`, newest-first
- compare_runs reports field-level differences and missing runs
- persist_agent_result wires a chat-response dict into the repo
- agent_bridge chat path still works when persistence raises
- /api/ayosa/runs and /runs/{id} and /runs/compare route shapes
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from accelerators.ayosa.agent_bridge import run_agent_chat
from accelerators.ayosa.agent.session_store import SessionStore
from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.models import AyosaChatRequest, AyosaToolConfig
from accelerators.ayosa.persistence import (
    Repository,
    init_db,
    persist_agent_result,
    set_default_repository,
)
from accelerators.ayosa.router import router as ayosa_router


REQUIRED_TABLES = {
    "ayosa_sessions", "ayosa_messages", "ayosa_runs",
    "ayosa_tool_steps", "ayosa_snapshots",
}


# ──────────────────────────────────────────────────────────────────────── #
# Fixtures
# ──────────────────────────────────────────────────────────────────────── #
@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    db = init_db(tmp_path / "ayosa.db")
    return Repository(db)


@pytest.fixture
def install_repo(repo: Repository):
    """Install the temp repo as the process default for the duration of the test."""
    set_default_repository(repo)
    try:
        yield repo
    finally:
        set_default_repository(None)


@pytest.fixture
def app_client(install_repo: Repository) -> TestClient:
    app = FastAPI()
    app.include_router(ayosa_router, prefix="/api/ayosa")
    return TestClient(app)


def _sample_chat_response(**overrides: Any) -> dict[str, Any]:
    base = {
        "session_id": "sess-A",
        "intent": "latency_issues",
        "service": "payments",
        "time_range": "30m",
        "confidence": 0.42,
        "answer": "Latency increased on payments.",
        "plan": {"selected_tools": ["prometheus", "grafana"]},
        "tool_steps": [
            {"index": 0, "tool": "prometheus",
             "label": "query p99", "status": "ok", "error": None},
            {"index": 1, "tool": "grafana",
             "label": "fetch dashboard", "status": "ok", "error": None},
        ],
        "incident_snapshot": {
            "root_cause": "DB pool exhaustion",
            "impact": "5xx on /charge",
            "confidence": 0.6,
            "coverage": {"metrics": ["payments_latency_seconds"]},
            "top_findings": ["p99 4x normal"],
            "recommended_actions": ["scale DB pool"],
        },
        "missing_signals": ["traces"],
        "signal_coverage": {"metrics": ["latency"]},
        "evidence": [{"source": "prometheus"}],
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────── #
# Bootstrap / schema
# ──────────────────────────────────────────────────────────────────────── #
class TestBootstrap:
    def test_db_creates_required_tables(self, tmp_path: Path):
        db = init_db(tmp_path / "ayosa.db")
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        names = {r["name"] for r in rows}
        assert REQUIRED_TABLES <= names

    def test_init_db_idempotent(self, tmp_path: Path):
        p = tmp_path / "ayosa.db"
        init_db(p); init_db(p)  # second call must not raise

    def test_db_parent_dir_created(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "ayosa.db"
        init_db(nested)
        assert nested.parent.is_dir()


# ──────────────────────────────────────────────────────────────────────── #
# Repository CRUD
# ──────────────────────────────────────────────────────────────────────── #
class TestRepository:
    def test_create_and_get_run_roundtrip(self, repo: Repository):
        rid = repo.create_run(
            run_id="run-1",
            session_id="sess-A",
            message="why is payments slow?",
            intent="latency_issues",
            service="payments",
            time_range="30m",
            tools_used=["prometheus", "grafana"],
            confidence=0.55,
            answer="see snapshot",
            snapshot={"root_cause": "pool", "impact": "5xx",
                      "confidence": 0.55, "coverage": {"metrics": ["a"]},
                      "top_findings": ["x"], "recommended_actions": ["y"]},
            evidence_summary={"missing_signals": ["traces"]},
            tool_steps=[{"index": 0, "tool": "prometheus",
                         "label": "q", "status": "ok"}],
        )
        assert rid == "run-1"

        run = repo.get_run("run-1")
        assert run is not None
        assert run.session_id == "sess-A"
        assert run.tools_used == ["prometheus", "grafana"]
        assert run.snapshot["root_cause"] == "pool"
        assert run.evidence_summary["missing_signals"] == ["traces"]
        assert run.tool_steps[0]["tool"] == "prometheus"
        assert run.confidence == pytest.approx(0.55)

    def test_create_run_autogenerates_id(self, repo: Repository):
        rid = repo.create_run(session_id="s", message="m")
        assert rid and rid.startswith("run-")

    def test_get_unknown_run_returns_none(self, repo: Repository):
        assert repo.get_run("does-not-exist") is None
        assert repo.get_run("") is None

    def test_create_run_writes_messages(self, repo: Repository):
        repo.create_run(
            run_id="run-msg", session_id="s1",
            message="hi", answer="hello",
        )
        with repo.db.connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM ayosa_messages WHERE run_id = ?"
                " ORDER BY message_id",
                ("run-msg",),
            ).fetchall()
        roles = [(r["role"], r["content"]) for r in rows]
        assert ("user", "hi") in roles
        assert ("assistant", "hello") in roles

    def test_create_run_inserts_session(self, repo: Repository):
        repo.create_run(run_id="r", session_id="sess-X", message="m")
        with repo.db.connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM ayosa_sessions WHERE session_id = ?",
                ("sess-X",),
            ).fetchone()
        assert row is not None

    def test_create_run_snapshot_table_populated(self, repo: Repository):
        repo.create_run(
            run_id="snap1",
            snapshot={"root_cause": "rc", "impact": "imp",
                      "confidence": 0.9, "coverage": {},
                      "top_findings": [], "recommended_actions": []},
        )
        with repo.db.connect() as conn:
            row = conn.execute(
                "SELECT root_cause, impact, confidence "
                "FROM ayosa_snapshots WHERE run_id = ?",
                ("snap1",),
            ).fetchone()
        assert row["root_cause"] == "rc"
        assert row["impact"] == "imp"
        assert row["confidence"] == pytest.approx(0.9)

    def test_create_run_overwrites_existing(self, repo: Repository):
        repo.create_run(run_id="same", message="v1", confidence=0.1)
        repo.create_run(run_id="same", message="v2", confidence=0.7,
                        tool_steps=[{"index": 0, "tool": "prometheus"}])
        run = repo.get_run("same")
        assert run.message == "v2"
        assert run.confidence == pytest.approx(0.7)
        # child rows replaced, not duplicated
        with repo.db.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM ayosa_tool_steps WHERE run_id = ?",
                ("same",),
            ).fetchone()["c"]
        assert n == 1


# ──────────────────────────────────────────────────────────────────────── #
# list_runs / filters
# ──────────────────────────────────────────────────────────────────────── #
class TestListRuns:
    def _seed(self, repo: Repository) -> None:
        repo.create_run(run_id="a", session_id="s1", service="payments",
                        created_at="2026-06-01T10:00:00+00:00")
        repo.create_run(run_id="b", session_id="s1", service="orders",
                        created_at="2026-06-01T10:05:00+00:00")
        repo.create_run(run_id="c", session_id="s2", service="payments",
                        created_at="2026-06-01T10:10:00+00:00")

    def test_list_all_newest_first(self, repo: Repository):
        self._seed(repo)
        runs = repo.list_runs()
        assert [r.run_id for r in runs] == ["c", "b", "a"]

    def test_list_filters_by_session(self, repo: Repository):
        self._seed(repo)
        runs = repo.list_runs(session_id="s1")
        assert {r.run_id for r in runs} == {"a", "b"}

    def test_list_filters_by_service(self, repo: Repository):
        self._seed(repo)
        runs = repo.list_runs(service="payments")
        assert {r.run_id for r in runs} == {"a", "c"}

    def test_list_filters_combined(self, repo: Repository):
        self._seed(repo)
        runs = repo.list_runs(session_id="s1", service="payments")
        assert [r.run_id for r in runs] == ["a"]

    def test_list_respects_limit(self, repo: Repository):
        self._seed(repo)
        runs = repo.list_runs(limit=2)
        assert len(runs) == 2

    def test_list_empty_returns_empty(self, repo: Repository):
        assert repo.list_runs() == []


# ──────────────────────────────────────────────────────────────────────── #
# compare_runs
# ──────────────────────────────────────────────────────────────────────── #
class TestCompareRuns:
    def test_diff_highlights_changed_fields(self, repo: Repository):
        repo.create_run(run_id="r1", service="payments", confidence=0.3,
                        answer="A", tools_used=["prometheus"])
        repo.create_run(run_id="r2", service="payments", confidence=0.8,
                        answer="B", tools_used=["prometheus", "grafana"])
        cmp = repo.compare_runs("r1", "r2")
        assert cmp.missing_run_ids == []
        assert "confidence" in cmp.differences
        assert "answer" in cmp.differences
        assert "tools_used" in cmp.differences

    def test_diff_snapshot_subkeys(self, repo: Repository):
        repo.create_run(
            run_id="s1",
            snapshot={"root_cause": "x", "impact": "i1", "confidence": 0.1,
                      "coverage": {}, "top_findings": [],
                      "recommended_actions": []},
        )
        repo.create_run(
            run_id="s2",
            snapshot={"root_cause": "y", "impact": "i1", "confidence": 0.1,
                      "coverage": {}, "top_findings": ["finding"],
                      "recommended_actions": []},
        )
        cmp = repo.compare_runs("s1", "s2")
        snap = cmp.differences.get("snapshot", {})
        assert "root_cause" in snap
        assert "top_findings" in snap
        assert "impact" not in snap

    def test_diff_missing_runs(self, repo: Repository):
        repo.create_run(run_id="only", message="m")
        cmp = repo.compare_runs("only", "ghost")
        assert cmp.missing_run_ids == ["ghost"]
        assert cmp.left is not None
        assert cmp.right is None

    def test_diff_identical_runs(self, repo: Repository):
        repo.create_run(run_id="x", service="s", answer="ans")
        repo.create_run(run_id="y", service="s", answer="ans")
        cmp = repo.compare_runs("x", "y")
        assert cmp.differences == {}


# ──────────────────────────────────────────────────────────────────────── #
# persist_agent_result helper
# ──────────────────────────────────────────────────────────────────────── #
class TestPersistHelper:
    def test_persists_full_chat_response(self, repo: Repository):
        rid = persist_agent_result(
            _sample_chat_response(),
            request_message="why is payments slow?",
            repo=repo,
        )
        assert rid is not None
        run = repo.get_run(rid)
        assert run.intent == "latency_issues"
        assert run.service == "payments"
        assert run.tools_used == ["prometheus", "grafana"]
        assert run.snapshot["root_cause"] == "DB pool exhaustion"
        assert run.evidence_summary["missing_signals"] == ["traces"]
        assert len(run.tool_steps) == 2

    def test_returns_none_when_no_default_repo(self):
        set_default_repository(None)
        rid = persist_agent_result(_sample_chat_response(), request_message="m")
        assert rid is None


# ──────────────────────────────────────────────────────────────────────── #
# Chat path remains alive when persistence fails
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url, auth_token=None):
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [{"source": "prometheus", "signal": "metrics",
                 "finding": "ok", "status": "ok", "raw": {}}]


class _BrokenRepo:
    """A repository stand-in that always raises — to prove chat survives."""

    def list_runs(self, **_):  # pragma: no cover - not used in this path
        raise RuntimeError("boom")

    def get_run(self, *_):  # pragma: no cover
        raise RuntimeError("boom")

    def create_run(self, **_):
        raise RuntimeError("boom")

    def compare_runs(self, *_):  # pragma: no cover
        raise RuntimeError("boom")


class TestChatResilience:
    def test_chat_survives_when_persistence_fails(self):
        set_default_repository(_BrokenRepo())  # type: ignore[arg-type]
        try:
            agent = AyosaAgent(dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}))
            out = run_agent_chat(
                AyosaChatRequest(
                    message="latency",
                    tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
                    agent_mode=True,
                    service="payments",
                ),
                agent=agent,
                store=SessionStore(),
            )
            # The response must come back even though persistence raised.
            assert out["mode"] == "agent"
            assert "answer" in out
            # run_id must NOT be set when persistence failed.
            assert out.get("run_id") is None
        finally:
            set_default_repository(None)

    def test_chat_populates_run_id_when_persistence_works(self, tmp_path: Path):
        repo = Repository(init_db(tmp_path / "ayosa.db"))
        set_default_repository(repo)
        try:
            agent = AyosaAgent(dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}))
            out = run_agent_chat(
                AyosaChatRequest(
                    message="latency",
                    tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
                    agent_mode=True,
                    service="payments",
                ),
                agent=agent,
                store=SessionStore(),
            )
            assert out.get("run_id")
            stored = repo.get_run(out["run_id"])
            assert stored is not None
            assert stored.service == "payments"
        finally:
            set_default_repository(None)


# ──────────────────────────────────────────────────────────────────────── #
# HTTP routes
# ──────────────────────────────────────────────────────────────────────── #
class TestRoutes:
    def test_list_runs_endpoint(self, app_client: TestClient, install_repo: Repository):
        install_repo.create_run(run_id="r1", session_id="s1", service="payments")
        install_repo.create_run(run_id="r2", session_id="s1", service="orders")
        r = app_client.get("/api/ayosa/runs")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert len(body["runs"]) == 2

    def test_list_runs_filters_via_query(self, app_client, install_repo):
        install_repo.create_run(run_id="r1", service="payments")
        install_repo.create_run(run_id="r2", service="orders")
        r = app_client.get("/api/ayosa/runs", params={"service": "payments"})
        ids = [run["run_id"] for run in r.json()["runs"]]
        assert ids == ["r1"]

    def test_get_run_endpoint(self, app_client, install_repo):
        install_repo.create_run(run_id="r1", service="payments",
                                answer="found it")
        r = app_client.get("/api/ayosa/runs/r1")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "r1"
        assert body["answer"] == "found it"

    def test_get_run_404(self, app_client, install_repo):
        r = app_client.get("/api/ayosa/runs/missing")
        assert r.status_code == 404

    def test_compare_runs_endpoint(self, app_client, install_repo):
        install_repo.create_run(run_id="a", service="payments", confidence=0.1)
        install_repo.create_run(run_id="b", service="payments", confidence=0.9)
        r = app_client.get("/api/ayosa/runs/compare",
                           params={"left": "a", "right": "b"})
        assert r.status_code == 200
        body = r.json()
        assert "confidence" in body["differences"]
        assert body["left"]["run_id"] == "a"
        assert body["right"]["run_id"] == "b"

    def test_compare_runs_404_when_missing(self, app_client, install_repo):
        install_repo.create_run(run_id="a", service="payments")
        r = app_client.get("/api/ayosa/runs/compare",
                           params={"left": "a", "right": "ghost"})
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert "ghost" in json.dumps(detail)

    def test_routes_when_persistence_unavailable(self):
        """When the default repository is None, /runs returns an empty
        list and /runs/{id} + /runs/compare return 503."""
        set_default_repository(None)
        app = FastAPI()
        app.include_router(ayosa_router, prefix="/api/ayosa")
        client = TestClient(app)

        r = client.get("/api/ayosa/runs")
        assert r.status_code == 200
        assert r.json() == {"available": False, "runs": []}

        r = client.get("/api/ayosa/runs/anything")
        assert r.status_code == 503

        r = client.get("/api/ayosa/runs/compare",
                       params={"left": "a", "right": "b"})
        assert r.status_code == 503
