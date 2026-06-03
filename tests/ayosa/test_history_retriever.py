"""Tests for accelerators.ayosa.agent.history_retriever.

Step 6 — Persistence-backed retrieval. Validates that:
* The retriever returns empty/unavailable cleanly when persistence is off.
* Service matches outrank token-only matches.
* Intent matches add a smaller boost.
* Token overlap on the prior `message` field is the third ranking signal.
* The retriever never fabricates rows when the repo is empty.
* All failure modes are swallowed so chat keeps working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from accelerators.ayosa.agent.history_retriever import retrieve_prior_runs
from accelerators.ayosa.persistence import (
    Repository,
    init_db,
    set_default_repository,
)


# ──────────────────────────────────────────────────────────────────────── #
# Fixtures
# ──────────────────────────────────────────────────────────────────────── #
@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    db = init_db(tmp_path / "ayosa.db")
    return Repository(db)


@pytest.fixture(autouse=True)
def _clear_default_repo():
    """Tests pass the repo explicitly; ensure no global leaks."""
    set_default_repository(None)
    yield
    set_default_repository(None)


def _seed(repo: Repository, rows: list[dict[str, Any]]) -> None:
    for r in rows:
        repo.create_run(**r)


# ──────────────────────────────────────────────────────────────────────── #
# Availability
# ──────────────────────────────────────────────────────────────────────── #
class TestAvailability:
    def test_returns_unavailable_when_no_repo(self):
        # Default repo cleared by fixture; do not pass one.
        out = retrieve_prior_runs(message="anything", service="api")
        assert out["available"] is False
        assert out["matches"] == []
        assert out["count"] == 0
        assert "message" in out

    def test_empty_repo_returns_available_with_no_matches(self, repo: Repository):
        out = retrieve_prior_runs(message="latency spike", service="api", repo=repo)
        assert out["available"] is True
        assert out["matches"] == []
        assert out["count"] == 0
        assert out["scanned"] == 0

    def test_broken_repo_is_swallowed(self):
        class _Broken:
            def list_runs(self, **_):
                raise RuntimeError("simulated DB failure")

        out = retrieve_prior_runs(
            message="anything", service="api", repo=_Broken(),  # type: ignore[arg-type]
        )
        assert out["available"] is False
        assert out["matches"] == []


# ──────────────────────────────────────────────────────────────────────── #
# Ranking
# ──────────────────────────────────────────────────────────────────────── #
class TestRanking:
    def test_service_match_outranks_token_only(self, repo: Repository):
        _seed(repo, [
            {
                "run_id": "tok-only",
                "session_id": "s1",
                "message": "latency spike investigation",
                "service": "checkout",
                "intent": "latency_issues",
                "tools_used": ["prometheus"],
                "confidence": 0.6,
            },
            {
                "run_id": "svc-match",
                "session_id": "s1",
                "message": "completely unrelated text",
                "service": "api",
                "intent": "service_health",
                "tools_used": ["loki"],
                "confidence": 0.7,
            },
        ])
        out = retrieve_prior_runs(
            message="latency spike on api", service="api", repo=repo,
        )
        assert out["count"] == 2
        ids = [m["run_id"] for m in out["matches"]]
        # Service match (score 1.0) should beat token-only (score < 1.0)
        assert ids[0] == "svc-match"
        assert "service" in out["matches"][0]["matched_on"]

    def test_intent_match_adds_boost(self, repo: Repository):
        _seed(repo, [
            {
                "run_id": "intent-and-tokens",
                "message": "error rate spike",
                "service": None,
                "intent": "error_investigation",
                "tools_used": ["loki"],
            },
            {
                "run_id": "tokens-only",
                "message": "error rate spike",
                "service": None,
                "intent": "service_health",
                "tools_used": ["loki"],
            },
        ])
        out = retrieve_prior_runs(
            message="error rate spike",
            service=None,
            intent="error_investigation",
            repo=repo,
        )
        ids = [m["run_id"] for m in out["matches"]]
        assert ids[0] == "intent-and-tokens"
        assert "intent" in out["matches"][0]["matched_on"]

    def test_token_overlap_picks_related_message(self, repo: Repository):
        _seed(repo, [
            {"run_id": "r1", "message": "deploy rollback completed", "service": "api"},
            {"run_id": "r2", "message": "latency spike on checkout payments", "service": "api"},
            {"run_id": "r3", "message": "nightly batch finished", "service": "api"},
        ])
        out = retrieve_prior_runs(
            message="latency spike payments",
            service=None,  # disable service boost so token overlap drives ranking
            repo=repo,
        )
        ids = [m["run_id"] for m in out["matches"]]
        assert ids[0] == "r2"
        assert "tokens" in out["matches"][0]["matched_on"]

    def test_no_token_overlap_excludes_unrelated_rows(self, repo: Repository):
        _seed(repo, [
            {"run_id": "r-unrelated", "message": "nightly batch", "service": "x"},
        ])
        out = retrieve_prior_runs(
            message="completely different words here",
            service=None,
            repo=repo,
        )
        assert out["count"] == 0
        assert out["scanned"] >= 1

    def test_limit_is_respected(self, repo: Repository):
        _seed(repo, [
            {"run_id": f"r{i}", "message": f"latency spike #{i}", "service": "api"}
            for i in range(12)
        ])
        out = retrieve_prior_runs(
            message="latency spike", service="api", repo=repo, limit=3,
        )
        assert out["count"] == 3
        assert len(out["matches"]) == 3


# ──────────────────────────────────────────────────────────────────────── #
# Payload shape
# ──────────────────────────────────────────────────────────────────────── #
class TestPayloadShape:
    def test_match_carries_expected_fields(self, repo: Repository):
        repo.create_run(
            run_id="r1",
            session_id="s1",
            message="latency spike on api",
            intent="latency_issues",
            service="api",
            time_range="1h",
            tools_used=["prometheus", "loki"],
            confidence=0.82,
        )
        out = retrieve_prior_runs(
            message="latency spike", service="api", repo=repo,
        )
        m = out["matches"][0]
        assert m["run_id"] == "r1"
        assert m["service"] == "api"
        assert m["intent"] == "latency_issues"
        assert m["time_range"] == "1h"
        assert m["tools_used"] == ["prometheus", "loki"]
        assert m["confidence"] == pytest.approx(0.82)
        assert isinstance(m["score"], float)
        assert m["score"] > 0
        assert isinstance(m["matched_on"], list)
        assert m["matched_on"]  # at least one signal
        assert m["message"] == "latency spike on api"
        assert m["created_at"]
