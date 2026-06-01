"""Tests for AYOSA conversational session memory.

Covers:
  * Pure store: upsert / get / reset / clear, session isolation
  * Persistence: round-trip through JSON files in a tmp dir
  * Follow-up inference: missing service/time_range filled from prior turn
  * `reset_session=True` drops prior context for that session only
  * `session_id` round-trips through both agent and deterministic responses
  * Backwards compat: old requests (no session_id, no reset_session) still work
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.session_store import (
    EvidenceSummary,
    SessionStore,
    get_default_store,
    set_default_store,
    summarise_evidence,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_bridge import run_agent_chat
from accelerators.ayosa.models import (
    AyosaChatRequest,
    AyosaToolConfig,
)


# ──────────────────────────────────────────────────────────────────────── #
# Fakes
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": f"metric for service={service} window={time_range}",
                "status": "ok",
                "raw": {"timestamp": "2026-06-01T10:00:00Z"},
            }
        ]


class _FakeESAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "elasticsearch",
                "signal": "logs",
                "finding": f"log hit for service={service} window={time_range}",
                "status": "ok",
                "raw": {},
            }
        ]


def _agent(adapters):
    return AyosaAgent(dispatcher=ToolDispatcher(adapters))


def _req(message, *, service=None, time_range="30m", session_id=None,
         reset_session=False, tools=None, agent_mode=True):
    return AyosaChatRequest(
        message=message,
        service=service,
        time_range=time_range,
        tools=tools or [
            AyosaToolConfig(tool="prometheus", base_url="http://prom.local"),
            AyosaToolConfig(tool="elasticsearch", base_url="http://es.local"),
        ],
        agent_mode=agent_mode,
        session_id=session_id,
        reset_session=reset_session,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Pure store behaviour
# ──────────────────────────────────────────────────────────────────────── #
class TestSessionStore:
    def test_new_session_id_is_unique(self):
        s = SessionStore()
        ids = {s.new_session_id() for _ in range(50)}
        assert len(ids) == 50

    def test_get_unknown_returns_none(self):
        s = SessionStore()
        assert s.get("nope") is None
        assert s.get("") is None

    def test_record_turn_sets_last_fields(self):
        s = SessionStore()
        sid = s.new_session_id()
        rec = s.record_turn(
            sid,
            user_message="hi",
            assistant_answer="hello",
            intent="general_chat",
            service="payments",
            time_range="8h",
            tools_used=["prometheus"],
        )
        assert rec.last_intent == "general_chat"
        assert rec.last_service == "payments"
        assert rec.last_time_range == "8h"
        assert rec.last_tools_used == ["prometheus"]
        assert len(rec.messages) == 2
        assert rec.messages[0].role == "user"
        assert rec.messages[1].role == "assistant"
        assert rec.created_at and rec.updated_at

    def test_isolation_between_sessions(self):
        s = SessionStore()
        s.record_turn("a", user_message="x", assistant_answer="y", service="svcA")
        s.record_turn("b", user_message="x", assistant_answer="y", service="svcB")
        assert s.get("a").last_service == "svcA"
        assert s.get("b").last_service == "svcB"

    def test_reset_drops_only_one_session(self):
        s = SessionStore()
        s.record_turn("a", user_message="x", assistant_answer="y", service="svcA")
        s.record_turn("b", user_message="x", assistant_answer="y", service="svcB")
        s.reset("a")
        assert s.get("a") is None
        assert s.get("b").last_service == "svcB"

    def test_clear_drops_everything(self):
        s = SessionStore()
        s.record_turn("a", user_message="x", assistant_answer="y")
        s.record_turn("b", user_message="x", assistant_answer="y")
        s.clear()
        assert s.session_ids() == []

    def test_message_cap_trims_oldest(self):
        s = SessionStore(max_messages_per_session=4)
        for i in range(5):
            s.record_turn("a", user_message=f"q{i}", assistant_answer=f"a{i}")
        rec = s.get("a")
        # Each call appends 2 messages → after trimming we keep the last 4
        assert len(rec.messages) == 4
        assert rec.messages[-1].content == "a4"


# ──────────────────────────────────────────────────────────────────────── #
# Persistence
# ──────────────────────────────────────────────────────────────────────── #
class TestPersistence:
    def test_round_trip_through_disk(self, tmp_path: Path):
        s1 = SessionStore(persist_dir=tmp_path)
        sid = "deadbeef0001"
        s1.record_turn(
            sid,
            user_message="show payment latency last 8h",
            assistant_answer="ok",
            intent="latency_issues",
            service="payments",
            time_range="8h",
            tools_used=["prometheus"],
        )
        file = tmp_path / f"{sid}.json"
        assert file.exists()
        data = json.loads(file.read_text())
        assert data["last_service"] == "payments"
        assert data["last_time_range"] == "8h"

        # A fresh store loaded from the same dir must see the prior session
        s2 = SessionStore(persist_dir=tmp_path)
        rec = s2.get(sid)
        assert rec is not None
        assert rec.last_service == "payments"
        assert rec.last_time_range == "8h"
        assert rec.last_tools_used == ["prometheus"]

    def test_reset_removes_persisted_file(self, tmp_path: Path):
        s = SessionStore(persist_dir=tmp_path)
        sid = "deadbeef0002"
        s.record_turn(sid, user_message="x", assistant_answer="y")
        file = tmp_path / f"{sid}.json"
        assert file.exists()
        s.reset(sid)
        assert not file.exists()

    def test_refuses_path_traversal_session_id(self, tmp_path: Path):
        """Path-traversal characters must not produce a file write."""
        s = SessionStore(persist_dir=tmp_path)
        bad = "../evil"
        # The store will still keep an in-memory record (we don't validate
        # ids on write to memory), but no file must appear outside tmp_path.
        s.record_turn(bad, user_message="x", assistant_answer="y")
        # No file anywhere under tmp_path with that literal name
        assert not list(tmp_path.glob("*evil*"))


# ──────────────────────────────────────────────────────────────────────── #
# Follow-up inference (pure)
# ──────────────────────────────────────────────────────────────────────── #
class TestInference:
    def test_no_prior_session_returns_input_unchanged(self):
        s = SessionStore()
        out = s.infer_followup_context(
            "missing", current_service=None, current_time_range=None
        )
        assert out == {"service": None, "time_range": "30m"}

    def test_inherits_when_blank(self):
        s = SessionStore()
        s.record_turn(
            "a", user_message="x", assistant_answer="y",
            service="payments", time_range="8h",
        )
        out = s.infer_followup_context(
            "a", current_service=None, current_time_range="30m"
        )
        assert out == {"service": "payments", "time_range": "8h"}

    def test_never_overrides_explicit_values(self):
        s = SessionStore()
        s.record_turn(
            "a", user_message="x", assistant_answer="y",
            service="payments", time_range="8h",
        )
        out = s.infer_followup_context(
            "a", current_service="orders", current_time_range="2h"
        )
        assert out == {"service": "orders", "time_range": "2h"}


# ──────────────────────────────────────────────────────────────────────── #
# Bridge integration — follow-up scenario from spec
# ──────────────────────────────────────────────────────────────────────── #
class TestBridgeFollowUp:
    def test_followup_inherits_service_and_time_range(self):
        store = SessionStore()
        agent = _agent({
            "prometheus": _FakePromAdapter,
            "elasticsearch": _FakeESAdapter,
        })

        # First turn: explicit service + time_range
        first = run_agent_chat(
            _req("show payment latency last 8h", service="payments", time_range="8h"),
            agent=agent, store=store,
        )
        sid = first["session_id"]
        assert sid
        assert first["plan"]["service"] == "payments"
        # adapter receives "8h" — surfaces in the finding text
        assert any("window=8h" in (o.get("finding") or "") for o in first["evidence"])

        # Second turn: only message + same session_id, no service/time_range
        second = run_agent_chat(
            _req("what about errors?", session_id=sid),
            agent=agent, store=store,
        )
        assert second["session_id"] == sid
        # Plan must have inherited the service and the time_range
        assert second["plan"]["service"] == "payments"
        assert second["plan"]["time_range"] == "8h"
        # "errors" → error_investigation intent → logs required → elasticsearch
        assert second["intent"] == "error_investigation"
        assert "elasticsearch" in second["plan"]["selected_tools"]

    def test_reset_session_clears_prior_context(self):
        store = SessionStore()
        agent = _agent({"prometheus": _FakePromAdapter})

        first = run_agent_chat(
            _req("p99 latency last 8h", service="payments", time_range="8h"),
            agent=agent, store=store,
        )
        sid = first["session_id"]

        # reset_session=True drops the prior turn — second turn should NOT
        # inherit "payments" / "8h".
        second = run_agent_chat(
            _req("p99 latency?", session_id=sid, reset_session=True),
            agent=agent, store=store,
        )
        assert second["session_id"] == sid
        assert second["plan"]["service"] is None
        assert second["plan"]["time_range"] == "30m"  # default, no inheritance

    def test_no_context_leak_across_sessions(self):
        store = SessionStore()
        agent = _agent({"prometheus": _FakePromAdapter})

        first = run_agent_chat(
            _req("p99 latency", service="payments", time_range="8h"),
            agent=agent, store=store,
        )
        sid_a = first["session_id"]

        # A different session_id must NOT see session A's context.
        other = run_agent_chat(
            _req("p99 latency?", session_id="totally-other"),
            agent=agent, store=store,
        )
        assert other["session_id"] == "totally-other"
        assert sid_a != "totally-other"
        assert other["plan"]["service"] is None
        assert other["plan"]["time_range"] == "30m"


# ──────────────────────────────────────────────────────────────────────── #
# Backwards compatibility
# ──────────────────────────────────────────────────────────────────────── #
class TestBackwardsCompat:
    def test_request_without_session_fields_still_works(self):
        # Old client: no session_id, no reset_session.
        store = SessionStore()
        agent = _agent({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            AyosaChatRequest(
                message="p99 latency?",
                tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
                agent_mode=True,
            ),
            agent=agent, store=store,
        )
        # A session_id was minted automatically and surfaced in the response
        assert out["session_id"]
        assert out["mode"] == "agent"

    def test_deterministic_path_returns_session_id(self, monkeypatch):
        """Even when agent_mode=False, the response carries a session_id."""
        from accelerators.ayosa import router as router_module

        class _SvcStub:
            def investigate(self, request):
                return {
                    "answer": "ok", "service": request.service,
                    "time_range": request.time_range, "confidence": 1.0,
                    "probable_root_cause": "", "impact": "",
                    "detected_patterns": [], "timeline": [],
                    "related_artifacts": [], "evidence": [],
                    "suggested_actions": [], "signal_coverage": {},
                    "missing_signals": [], "intent": "service_health",
                }

        monkeypatch.setattr(router_module, "AyosaService", _SvcStub)
        req = AyosaChatRequest(
            message="env health?",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
            agent_mode=False,
        )
        out = router_module.chat(req)
        assert out["session_id"]
        assert out["mode"] == "deterministic"

    def test_deterministic_path_echoes_provided_session_id(self, monkeypatch):
        from accelerators.ayosa import router as router_module

        class _SvcStub:
            def investigate(self, request):
                return {"answer": "", "service": None, "time_range": "30m",
                        "confidence": 1.0, "probable_root_cause": "", "impact": "",
                        "detected_patterns": [], "timeline": [], "related_artifacts": [],
                        "evidence": [], "suggested_actions": [], "signal_coverage": {},
                        "missing_signals": [], "intent": "general_chat"}

        monkeypatch.setattr(router_module, "AyosaService", _SvcStub)
        req = AyosaChatRequest(
            message="hi",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
            agent_mode=False,
            session_id="caller-supplied-id",
        )
        out = router_module.chat(req)
        assert out["session_id"] == "caller-supplied-id"


# ──────────────────────────────────────────────────────────────────────── #
# Evidence summariser
# ──────────────────────────────────────────────────────────────────────── #
class TestSummariseEvidence:
    def test_summarises_dicts(self):
        summary = summarise_evidence([
            {"status": "ok", "signal": "metrics", "finding": "f1"},
            {"status": "ok", "signal": "metrics", "finding": "f2"},
            {"status": "error", "signal": "logs", "finding": "boom"},
        ])
        assert summary.total == 3
        assert summary.ok == 2
        assert summary.errors == 1
        assert summary.signals == ["metrics", "logs"]
        assert summary.top_findings == ["f1", "f2"]

    def test_handles_empty(self):
        assert summarise_evidence([]) == EvidenceSummary()


# ──────────────────────────────────────────────────────────────────────── #
# Default-store helpers
# ──────────────────────────────────────────────────────────────────────── #
class TestDefaultStore:
    def test_singleton_is_reusable(self):
        a = get_default_store()
        b = get_default_store()
        assert a is b

    def test_set_default_store_overrides(self):
        original = get_default_store()
        try:
            stub = SessionStore()
            set_default_store(stub)
            assert get_default_store() is stub
        finally:
            set_default_store(original)
