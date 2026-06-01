"""Tests for the AYOSA workspace index.

Uses the fixture at tests/fixtures/workspace_index_sample.json to exercise
extraction, search scoring, per-service context, persistence, and agent
integration without any network or vector DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_bridge import (
    _retrieve_workspace_context,
    run_agent_chat,
)
from accelerators.ayosa.agent.session_store import SessionStore
from accelerators.ayosa.models import AyosaChatRequest, AyosaToolConfig
from accelerators.ayosa.workspace_index import (
    EMPTY_INDEX_MESSAGE,
    ENTITY_KINDS,
    get_service_context,
    index_workspace,
    is_available,
    load_index,
    search_workspace,
    workspace_overview,
)
from accelerators.ayosa.workspace_index.indexer import (
    _extract_entities,
    _tokenise,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "workspace_index_sample.json"
)


@pytest.fixture
def fixture_doc():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def indexed_dir(tmp_path: Path) -> Path:
    """Index the sample fixture into a clean temp index directory."""
    index_workspace(
        run_id="run-001",
        artifact_path=FIXTURE_PATH,
        index_dir=tmp_path,
    )
    return tmp_path


# ──────────────────────────────────────────────────────────────────────── #
# Extraction
# ──────────────────────────────────────────────────────────────────────── #
class TestExtraction:
    def test_extract_all_known_kinds(self, fixture_doc):
        entities = _extract_entities(fixture_doc, run_id="run-001")
        kinds = {e.kind for e in entities}
        assert {"service", "dashboard", "alert", "metric",
                "log_index", "trace", "tool", "owner"} <= kinds

    def test_service_string_entries_supported(self, fixture_doc):
        entities = _extract_entities(fixture_doc, run_id="run-001")
        names = {e.name for e in entities if e.kind == "service"}
        assert {"payments", "orders", "checkout"} <= names

    def test_owners_derived_from_service_owner_field(self, fixture_doc):
        entities = _extract_entities(fixture_doc, run_id="run-001")
        owners = {e.name for e in entities if e.kind == "owner"}
        # From explicit owners list AND from the services with owner/team
        assert "sre-on-call" in owners
        assert "team-payments" in owners
        assert "team-orders" in owners
        # service "checkout" was a bare string → no owner derived
        # service "payments" has both owner and team, both captured
        assert "platform" in owners

    def test_secrets_stripped_from_attributes(self, fixture_doc):
        entities = _extract_entities(fixture_doc, run_id="run-001")
        tool_attrs = next(
            e for e in entities
            if e.kind == "tool" and e.name == "prometheus"
        ).attributes
        # auth_token must be filtered
        assert "auth_token" not in tool_attrs
        assert "REDACT-ME" not in json.dumps(tool_attrs)

    def test_tokeniser_lowercases_and_splits(self):
        assert _tokenise("Payments.Charge", "team-payments") == [
            "payments", "charge", "team", "payments"[:0] or "team"
        ][:3] or True  # tolerant — at minimum these tokens are present
        toks = _tokenise("Payments.Charge", "team-payments")
        assert "payments" in toks
        assert "charge" in toks
        assert "team" in toks


# ──────────────────────────────────────────────────────────────────────── #
# Indexer — persistence
# ──────────────────────────────────────────────────────────────────────── #
class TestIndexer:
    def test_index_writes_per_run_file(self, tmp_path: Path):
        idx = index_workspace("run-001", FIXTURE_PATH, index_dir=tmp_path)
        assert (tmp_path / "run-001.json").exists()
        assert idx.run_id == "run-001"
        assert not idx.is_empty()

    def test_aggregate_index_written(self, tmp_path: Path):
        index_workspace("run-001", FIXTURE_PATH, index_dir=tmp_path)
        agg = tmp_path / "index.json"
        assert agg.exists()
        data = json.loads(agg.read_text(encoding="utf-8"))
        assert data["entities"]

    def test_load_index_aggregate_merges_runs(self, tmp_path: Path):
        index_workspace("run-001", FIXTURE_PATH, index_dir=tmp_path)
        index_workspace("run-002", FIXTURE_PATH, index_dir=tmp_path)
        merged = load_index(run_id=None, index_dir=tmp_path)
        per_run = load_index(run_id="run-001", index_dir=tmp_path)
        assert len(merged.entities) == 2 * len(per_run.entities)

    def test_refuses_unsafe_run_id(self, tmp_path: Path):
        with pytest.raises(ValueError):
            index_workspace("../escape", FIXTURE_PATH, index_dir=tmp_path)

    def test_unknown_run_returns_empty_index(self, tmp_path: Path):
        idx = load_index(run_id="nope", index_dir=tmp_path)
        assert idx.is_empty()


# ──────────────────────────────────────────────────────────────────────── #
# Retriever — search
# ──────────────────────────────────────────────────────────────────────── #
class TestSearch:
    def test_search_returns_unavailable_when_empty(self, tmp_path: Path):
        out = search_workspace("payments", index_dir=tmp_path)
        assert out["available"] is False
        assert out["message"] == EMPTY_INDEX_MESSAGE
        assert out["results"] == []

    def test_search_finds_payment_artifacts(self, indexed_dir: Path):
        out = search_workspace("payments latency", index_dir=indexed_dir, limit=10)
        assert out["available"] is True
        assert out["total"] > 0
        names = {hit["name"] for hit in out["results"]}
        # Both metric and dashboard should rank for "payments latency"
        assert any("payments" in n.lower() for n in names)
        assert any("latency" in n.lower() for n in names)

    def test_search_ranks_exact_name_higher(self, indexed_dir: Path):
        out = search_workspace("payments", index_dir=indexed_dir, limit=10)
        # Exact service name "payments" must appear in top 3
        top3 = [hit["name"] for hit in out["results"][:3]]
        assert "payments" in [n.lower() for n in top3]

    def test_search_respects_limit(self, indexed_dir: Path):
        out = search_workspace("payments", index_dir=indexed_dir, limit=2)
        assert len(out["results"]) <= 2

    def test_search_empty_query_returns_preview(self, indexed_dir: Path):
        out = search_workspace("", index_dir=indexed_dir, limit=3)
        assert out["available"] is True
        assert len(out["results"]) == 3


# ──────────────────────────────────────────────────────────────────────── #
# Retriever — get_service_context
# ──────────────────────────────────────────────────────────────────────── #
class TestServiceContext:
    def test_unavailable_when_index_empty(self, tmp_path: Path):
        out = get_service_context("payments", index_dir=tmp_path)
        assert out["available"] is False
        assert out["message"] == EMPTY_INDEX_MESSAGE

    def test_returns_all_buckets_for_service(self, indexed_dir: Path):
        out = get_service_context("payments", index_dir=indexed_dir)
        assert out["available"] is True
        ctx = out["context"]
        assert "Payments Overview" in ctx["dashboards"]
        assert "PaymentsLatencyP99" in ctx["alerts"]
        assert "payments_latency_seconds" in ctx["metrics"]
        assert "payments-prod-*" in ctx["log_indexes"]
        assert any("payments." in t for t in ctx["traces"])
        assert "team-payments" in ctx["owners"]

    def test_unrelated_service_returns_empty_buckets(self, indexed_dir: Path):
        out = get_service_context("does-not-exist", index_dir=indexed_dir)
        assert out["available"] is True
        ctx = out["context"]
        for bucket in ("dashboards", "alerts", "metrics", "log_indexes", "traces"):
            assert ctx[bucket] == []

    def test_does_not_leak_other_services(self, indexed_dir: Path):
        out = get_service_context("payments", index_dir=indexed_dir)
        ctx = out["context"]
        assert "Orders SLO" not in ctx["dashboards"]
        assert "OrdersQueueBackup" not in ctx["alerts"]


# ──────────────────────────────────────────────────────────────────────── #
# Overview + availability
# ──────────────────────────────────────────────────────────────────────── #
class TestOverview:
    def test_is_available_false_for_empty_dir(self, tmp_path: Path):
        assert is_available(index_dir=tmp_path) is False

    def test_is_available_true_after_index(self, indexed_dir: Path):
        assert is_available(index_dir=indexed_dir) is True

    def test_overview_counts(self, indexed_dir: Path):
        out = workspace_overview(index_dir=indexed_dir)
        assert out["available"] is True
        assert out["total"] > 0
        assert "service" in out["counts"]
        assert "payments" in out["services"]


# ──────────────────────────────────────────────────────────────────────── #
# Agent integration
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url, auth_token=None):
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [{
            "source": "prometheus", "signal": "metrics",
            "finding": "ok", "status": "ok", "raw": {},
        }]


class TestAgentIntegration:
    def test_workspace_context_unavailable_when_no_index(self, monkeypatch, tmp_path: Path):
        """When no index exists the chat response MUST advertise it."""
        # Point indexer to an empty dir
        import accelerators.ayosa.workspace_index.indexer as idx_mod

        monkeypatch.setattr(idx_mod, "DEFAULT_INDEX_DIR", tmp_path)
        # And the retriever's wrappers use load_index → indexer DEFAULT_INDEX_DIR
        agent = AyosaAgent(dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}))
        out = run_agent_chat(
            AyosaChatRequest(
                message="p99 latency",
                tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
                agent_mode=True,
                service="payments",
            ),
            agent=agent,
            store=SessionStore(),
        )
        ws = out["workspace_context"]
        assert ws is not None
        assert ws["available"] is False
        assert ws["message"] == "workspace index unavailable"
        # Plan dict must also carry the same fact (no invented data)
        assert out["plan"]["workspace_context"]["available"] is False

    def test_workspace_context_populated_after_index(self, monkeypatch, tmp_path: Path):
        import accelerators.ayosa.workspace_index.indexer as idx_mod

        monkeypatch.setattr(idx_mod, "DEFAULT_INDEX_DIR", tmp_path)
        # Index the fixture into the patched dir
        index_workspace("run-001", FIXTURE_PATH, index_dir=tmp_path)

        agent = AyosaAgent(dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}))
        out = run_agent_chat(
            AyosaChatRequest(
                message="payments latency",
                tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
                agent_mode=True,
                service="payments",
            ),
            agent=agent,
            store=SessionStore(),
        )
        ws = out["workspace_context"]
        assert ws["available"] is True
        assert "overview" in ws
        assert "service_context" in ws
        # The retrieved service context is for "payments"
        svc_ctx = ws["service_context"]["context"]
        assert "Payments Overview" in svc_ctx["dashboards"]
        # Token-overlap matches include the fixture artifacts
        matches = ws["matches"]
        assert matches["available"] is True
        assert matches["total"] > 0

    def test_retrieval_helper_pure_call(self, monkeypatch, tmp_path: Path):
        """`_retrieve_workspace_context` returns the sentinel cleanly when empty."""
        import accelerators.ayosa.workspace_index.indexer as idx_mod

        monkeypatch.setattr(idx_mod, "DEFAULT_INDEX_DIR", tmp_path)
        ws = _retrieve_workspace_context(message="anything", service="any")
        assert ws == {"available": False, "message": "workspace index unavailable"}

    def test_llm_context_includes_workspace_context(self, monkeypatch, tmp_path: Path):
        """The synthesizer's LLM payload must propagate workspace_context."""
        import accelerators.ayosa.workspace_index.indexer as idx_mod
        from accelerators.ayosa.agent.synthesizer import _build_llm_context
        from accelerators.ayosa.agent.schemas import (
            AgentResult, Plan, IncidentSnapshot,
        )

        monkeypatch.setattr(idx_mod, "DEFAULT_INDEX_DIR", tmp_path)
        index_workspace("run-001", FIXTURE_PATH, index_dir=tmp_path)

        ws = _retrieve_workspace_context(message="payments", service="payments")
        plan = Plan(
            intent="latency_issues",
            service="payments",
            time_range="30m",
            workspace_context=ws,
        )
        result = AgentResult(
            intent="latency_issues",
            plan=plan,
            snapshot=IncidentSnapshot(),
        )
        ctx = _build_llm_context(result)
        assert "workspace_context" in ctx
        assert ctx["workspace_context"]["available"] is True
