"""Feature 28 — Streaming agent runs are persisted to SQLite.

Pre-Step-28, only ``agent_bridge.run_agent_chat`` (the non-streaming
chat endpoint) called ``persist_agent_result``. ``stream_agent_chat``
emitted ``final_snapshot`` / ``loop_summary`` / ``done`` but never
recorded the run, which meant any conversation that ran through the
SSE endpoint left no audit trail and no row to compare against.

This module pins:

  * Deterministic streaming branch persists a row.
  * Tool-use streaming branch persists a row tagged ``mode=tool_use_loop``.
  * Both branches inject ``run_id`` into ``final_snapshot`` and the
    terminal ``done`` event so downstream consumers (UI history pane,
    Feature 29 replay endpoint) can address the run.
  * Persistence failure does NOT break the stream — the run_id field
    is simply absent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from accelerators.ayosa import agent_stream as stream_module
from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.schemas import (
    AgentAIConfig,
    AgentInput,
    AgentToolConfig,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_stream import stream_agent_chat
from accelerators.ayosa.models import AyosaChatRequest, AyosaToolConfig
from accelerators.ayosa.persistence import (
    Repository,
    set_default_repository,
)
from accelerators.ayosa.persistence.db import init_db


# ──────────────────────────────────────────────────────────────────────── #
# Repo fixture — clean tmp_path SQLite per test, restored on teardown
# ──────────────────────────────────────────────────────────────────────── #
@pytest.fixture
def repo(tmp_path) -> Repository:
    r = Repository(init_db(tmp_path / "ayosa.db"))
    set_default_repository(r)
    try:
        yield r
    finally:
        set_default_repository(None)


# ──────────────────────────────────────────────────────────────────────── #
# Adapter fake (matches accelerator constructor contract)
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": "p99 = 2.3s on payments",
                "status": "ok",
                "raw": {"timestamp": "2026-06-05T10:00:00Z"},
            }
        ]


def _agent() -> AyosaAgent:
    return AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=2,
    )


def _det_request() -> AyosaChatRequest:
    """Request that does NOT enable the tool-use loop."""
    return AyosaChatRequest(
        message="why is payments slow?",
        service="payments",
        time_range="15m",
        tools=[
            AyosaToolConfig(
                tool="prometheus",
                base_url="http://prom.local",
                auth_token=None,
            )
        ],
        agent_mode=True,
    )


async def _collect(gen):
    out = []
    async for ev in gen:
        out.append(ev)
    return out


# ──────────────────────────────────────────────────────────────────────── #
# Deterministic streaming branch
# ──────────────────────────────────────────────────────────────────────── #
class TestDeterministicStreamingPersistence:
    def test_run_is_persisted_with_deterministic_mode_tag(self, repo):
        events = asyncio.run(_collect(stream_agent_chat(_det_request(), agent=_agent())))

        # final_snapshot + done both carry the same run_id
        final_snap = next(e for e in events if e["type"] == "final_snapshot")
        done = next(e for e in events if e["type"] == "done")
        run_id = final_snap["data"].get("run_id")
        assert run_id, "final_snapshot must carry run_id"
        assert done.get("run_id") == run_id, "done.run_id must match final_snapshot.data.run_id"

        # Row is actually in the DB and tagged as deterministic
        persisted = repo.get_run(run_id)
        assert persisted is not None
        assert persisted.loop_summary is not None
        assert persisted.loop_summary.get("mode") == "deterministic"

    def test_loop_summary_event_carries_mode_tag(self, repo):
        events = asyncio.run(_collect(stream_agent_chat(_det_request(), agent=_agent())))
        loop_summary_event = next(e for e in events if e["type"] == "loop_summary")
        assert loop_summary_event.get("mode") == "deterministic"

    def test_persistence_failure_does_not_break_stream(self, repo, monkeypatch):
        # Force the persistence helper to fail; the stream must still
        # complete and yield final_snapshot + done, just without a run_id.
        def _boom(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(stream_module, "persist_agent_result", _boom)

        events = asyncio.run(_collect(stream_agent_chat(_det_request(), agent=_agent())))
        final_snap = next(e for e in events if e["type"] == "final_snapshot")
        done = next(e for e in events if e["type"] == "done")
        assert "run_id" not in final_snap["data"]
        assert done.get("run_id") is None

    def test_persisted_row_includes_iteration_and_tool_info(self, repo):
        events = asyncio.run(_collect(stream_agent_chat(_det_request(), agent=_agent())))
        run_id = next(e for e in events if e["type"] == "done")["run_id"]
        persisted = repo.get_run(run_id)
        assert persisted is not None
        assert persisted.iterations >= 1
        assert "prometheus" in (persisted.tools_used or [])
        assert any(
            (s.get("tool") or "").lower() == "prometheus"
            for s in (persisted.tool_steps or [])
        )


# ──────────────────────────────────────────────────────────────────────── #
# Tool-use streaming branch (scripted Anthropic client)
# ──────────────────────────────────────────────────────────────────────── #
@dataclass
class _FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"


@dataclass
class _FakeBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)


class _FakeMessages:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("scripted client exhausted")
        return self._scripted.pop(0)


class _FakeAnthropicClient:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self.messages = _FakeMessages(scripted)


def _scripted_two_turn() -> _FakeAnthropicClient:
    """One tool_use turn followed by an end_turn explanation."""
    return _FakeAnthropicClient([
        _FakeMessage(
            content=[
                _FakeBlock(
                    type="tool_use",
                    id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments", "time_range": "15m"},
                )
            ],
            stop_reason="tool_use",
        ),
        _FakeMessage(
            content=[
                _FakeBlock(
                    type="text",
                    text="Prometheus shows p99 = 2.3s on payments — above SLO.",
                )
            ],
            stop_reason="end_turn",
        ),
    ])


def _tooluse_request() -> AyosaChatRequest:
    """Request that opts in to the Anthropic tool-use loop."""
    return AyosaChatRequest(
        message="why is payments slow?",
        service="payments",
        time_range="15m",
        tools=[
            AyosaToolConfig(
                tool="prometheus",
                base_url="http://prom.local",
                auth_token=None,
            )
        ],
        agent_mode=True,
        ai={
            "enabled": True,
            "provider": "anthropic",
            "api_key": "sk-test",
            "model": "claude-sonnet-4-6",
            "use_tool_use_loop": True,
        },
    )


@pytest.fixture
def _stub_intent_classifier(monkeypatch):
    """Keep the intent classifier off the network."""
    def _fake(message, llm_config=None, service_hint=None):  # noqa: ARG001
        return ("latency_issues", {"source": "fake", "confidence": 1.0})

    monkeypatch.setattr(stream_module, "classify_intent_smart", _fake)


class TestToolUseStreamingPersistence:
    def test_run_is_persisted_with_tool_use_mode_tag(
        self, repo, monkeypatch, _stub_intent_classifier,
    ):
        client = _scripted_two_turn()
        monkeypatch.setattr(
            stream_module, "build_anthropic_client", lambda inp: client,
        )

        events = asyncio.run(_collect(
            stream_agent_chat(_tooluse_request(), agent=_agent())
        ))

        # Confirm we actually went through the tool-use branch by looking
        # for an llm_call_start event — only emitted by ToolUseLoop.
        assert any(e.get("type") == "llm_call_start" for e in events), (
            "test did not exercise the tool-use loop branch"
        )

        final_snap = next(e for e in events if e["type"] == "final_snapshot")
        done = next(e for e in events if e["type"] == "done")
        run_id = final_snap["data"].get("run_id")
        assert run_id, "tool-use final_snapshot must carry run_id"
        assert done.get("run_id") == run_id

        persisted = repo.get_run(run_id)
        assert persisted is not None
        assert persisted.loop_summary is not None
        assert persisted.loop_summary.get("mode") == "tool_use_loop"
        # The scripted scenario invoked Prometheus exactly once.
        assert "prometheus" in (persisted.tools_used or [])

    def test_loop_summary_event_carries_tool_use_mode(
        self, repo, monkeypatch, _stub_intent_classifier,
    ):
        client = _scripted_two_turn()
        monkeypatch.setattr(
            stream_module, "build_anthropic_client", lambda inp: client,
        )
        events = asyncio.run(_collect(
            stream_agent_chat(_tooluse_request(), agent=_agent())
        ))
        loop_summary_event = next(e for e in events if e["type"] == "loop_summary")
        assert loop_summary_event.get("mode") == "tool_use_loop"

    def test_persistence_failure_does_not_break_tool_use_stream(
        self, repo, monkeypatch, _stub_intent_classifier,
    ):
        client = _scripted_two_turn()
        monkeypatch.setattr(
            stream_module, "build_anthropic_client", lambda inp: client,
        )

        def _boom(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(stream_module, "persist_agent_result", _boom)

        events = asyncio.run(_collect(
            stream_agent_chat(_tooluse_request(), agent=_agent())
        ))
        final_snap = next(e for e in events if e["type"] == "final_snapshot")
        done = next(e for e in events if e["type"] == "done")
        assert "run_id" not in final_snap["data"]
        assert done.get("run_id") is None
        # And the stream still completed normally.
        assert done.get("mode") == "agent"


# ──────────────────────────────────────────────────────────────────────── #
# Non-streaming bridge — confirm the same mode tag (parity check)
# ──────────────────────────────────────────────────────────────────────── #
class TestNonStreamingModeTag:
    """Ensures ``agent_bridge.run_agent_chat`` also stamps ``mode`` so a
    consumer reading rows from SQLite can't tell them apart by accident."""

    def test_non_streaming_run_persists_deterministic_mode(self, repo):
        from accelerators.ayosa.agent_bridge import run_agent_chat

        response = run_agent_chat(_det_request(), agent=_agent())
        loop_summary = response.get("loop_summary") or {}
        assert loop_summary.get("mode") == "deterministic"

        run_id = response.get("run_id")
        assert run_id, "non-streaming run must produce a run_id"
        persisted = repo.get_run(run_id)
        assert persisted is not None
        assert (persisted.loop_summary or {}).get("mode") == "deterministic"
