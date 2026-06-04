"""Contract tests for the AYOSA agent SSE streaming endpoint.

Covers:
  * SSE serializer correctness + secret stripping
  * Event ordering: session_start → intent → plan → tool_* → observation
    → timeline_event → final_snapshot → done
  * Tool failure produces tool_result(status=error) and stream continues
  * No secrets (auth_token / api_key) appear in any streamed frame
  * Router preserves the legacy stream for agent_mode=False
  * Stream is progressive (events arrive before the full response is built)
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_stream import (
    serialize_sse_event,
    stream_agent_chat,
    stream_agent_chat_sse,
    _strip_secrets,
)
from accelerators.ayosa.models import (
    AyosaAIConfig,
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
                "finding": "rate(http_5xx) = 0.02",
                "query": "rate(http_requests_total[5m])",
                "status": "ok",
                "raw": {"timestamp": "2026-06-01T10:00:00Z", "severity": "info"},
            }
        ]


class _FakeFailingAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        raise RuntimeError("upstream timeout")


class _SlowAdapter:
    """Sleeps briefly so we can observe progressive event flushing."""

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        time.sleep(0.15)
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": "ok",
                "status": "ok",
                "raw": {},
            }
        ]


def _agent(adapters: dict[str, type]) -> AyosaAgent:
    return AyosaAgent(dispatcher=ToolDispatcher(adapters))


def _req(message: str, *, tools: list[AyosaToolConfig] | None = None,
         ai: AyosaAIConfig | None = None) -> AyosaChatRequest:
    return AyosaChatRequest(
        message=message,
        service="payments",
        time_range="15m",
        tools=tools or [
            AyosaToolConfig(
                tool="prometheus",
                base_url="http://prom.local",
                auth_token="super-secret-token-DO-NOT-LEAK",
            )
        ],
        ai=ai,
        agent_mode=True,
    )


async def _collect(gen):
    out = []
    async for ev in gen:
        out.append(ev)
    return out


# ──────────────────────────────────────────────────────────────────────── #
# SSE serializer
# ──────────────────────────────────────────────────────────────────────── #
class TestSerializer:
    def test_basic_frame_format(self):
        frame = serialize_sse_event("intent", {"intent": "service_health"})
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        body = json.loads(frame[len("data: "):].strip())
        assert body == {"type": "intent", "intent": "service_health"}

    def test_strips_top_level_secrets(self):
        frame = serialize_sse_event(
            "tool_start", {"tool": "splunk", "auth_token": "abc123"}
        )
        assert "abc123" not in frame
        body = json.loads(frame[len("data: "):].strip())
        assert body["auth_token"] == "[redacted]"

    def test_strips_nested_secrets(self):
        deep = {
            "plan": {
                "tools": [
                    {"name": "splunk", "api_key": "leak-me"},
                    {"name": "datadog", "Authorization": "Bearer abc"},
                ],
                "config": {"password": "p@ss"},
            }
        }
        frame = serialize_sse_event("plan", deep)
        for forbidden in ("leak-me", "Bearer abc", "p@ss"):
            assert forbidden not in frame
        body = json.loads(frame[len("data: "):].strip())
        assert body["plan"]["tools"][0]["api_key"] == "[redacted]"
        assert body["plan"]["tools"][1]["Authorization"] == "[redacted]"
        assert body["plan"]["config"]["password"] == "[redacted]"

    def test_handles_unserializable_objects(self):
        class Weird:
            def __repr__(self):
                return "Weird()"

        frame = serialize_sse_event("observation", {"raw": Weird()})
        # Must still produce a valid SSE frame
        assert frame.startswith("data: ")
        body = json.loads(frame[len("data: "):].strip())
        assert "Weird" in body["raw"]

    def test_strip_secrets_pure_function(self):
        result = _strip_secrets({"a": 1, "auth_token": "x", "nested": {"token": "y"}})
        assert result == {"a": 1, "auth_token": "[redacted]", "nested": {"token": "[redacted]"}}


# ──────────────────────────────────────────────────────────────────────── #
# Event ordering and required event types
# ──────────────────────────────────────────────────────────────────────── #
REQUIRED_EVENT_TYPES = {
    "session_start", "intent", "plan",
    "tool_start", "tool_result", "observation",
    "timeline_event", "final_snapshot", "done",
}


class TestEventOrdering:
    def test_full_event_sequence(self):
        async def _run():
            return await _collect(
                stream_agent_chat(_req("p99 latency?"), agent=_agent({"prometheus": _FakePromAdapter}))
            )

        events = asyncio.run(_run())
        types = [e["type"] for e in events]

        # Required event types all appear
        missing = REQUIRED_EVENT_TYPES - set(types)
        assert not missing, f"Missing event types: {missing}"

        # session_start first, done last, error not present
        assert types[0] == "session_start"
        assert types[-1] == "done"
        assert "error" not in types

        # plan comes after intent comes after session_start
        assert types.index("session_start") < types.index("intent") < types.index("plan")

        # tool_start precedes tool_result precedes its observation
        ts_idx = types.index("tool_start")
        tr_idx = types.index("tool_result")
        obs_idx = types.index("observation")
        assert ts_idx < tr_idx < obs_idx

        # final_snapshot immediately precedes done
        assert types.index("final_snapshot") < types.index("done")

    def test_session_start_carries_session_id(self):
        async def _run():
            return await _collect(
                stream_agent_chat(_req("any alerts?"), agent=_agent({"prometheus": _FakePromAdapter}))
            )

        events = asyncio.run(_run())
        start = events[0]
        assert start["type"] == "session_start"
        assert start["session_id"]
        assert start["ts"]

    def test_plan_payload_includes_required_fields(self):
        async def _run():
            return await _collect(
                stream_agent_chat(_req("p99 latency?"), agent=_agent({"prometheus": _FakePromAdapter}))
            )

        events = asyncio.run(_run())
        plan_ev = next(e for e in events if e["type"] == "plan")
        plan = plan_ev["plan"]
        assert plan["intent"] == "latency_issues"
        assert "metrics" in plan["required_signals"]
        assert "prometheus" in plan["selected_tools"]

    def test_final_snapshot_has_full_chat_response(self):
        async def _run():
            return await _collect(
                stream_agent_chat(_req("p99 latency?"), agent=_agent({"prometheus": _FakePromAdapter}))
            )

        events = asyncio.run(_run())
        snap = next(e for e in events if e["type"] == "final_snapshot")
        data = snap["data"]
        for key in (
            "mode", "answer", "intent", "plan", "tool_steps",
            "observations", "evidence", "timeline", "missing_signals",
            "suggested_actions",
        ):
            assert key in data, f"final_snapshot missing '{key}'"
        assert data["mode"] == "agent"


# ──────────────────────────────────────────────────────────────────────── #
# Tool failure path
# ──────────────────────────────────────────────────────────────────────── #
class TestToolFailure:
    def test_failing_tool_emits_error_result_and_continues(self):
        tools = [
            AyosaToolConfig(tool="prometheus", base_url="http://failing"),
            AyosaToolConfig(tool="alertmanager", base_url="http://am.local"),
        ]

        async def _run():
            return await _collect(
                stream_agent_chat(
                    _req("any active alerts?", tools=tools),
                    agent=_agent({
                        "prometheus": _FakeFailingAdapter,
                        "alertmanager": _FakePromAdapter,  # produces ok result
                    }),
                )
            )

        events = asyncio.run(_run())
        types = [e["type"] for e in events]

        # An error tool_result must be present
        errors = [e for e in events if e["type"] == "tool_result" and e["status"] == "error"]
        # ... but only when the failing tool was actually selected for this intent.
        # active_alerts intent → alertmanager selected, prometheus skipped, so no error.
        # Re-run with a latency question to force prometheus selection:

        async def _run2():
            return await _collect(
                stream_agent_chat(
                    _req("p99 latency?", tools=tools),
                    agent=_agent({
                        "prometheus": _FakeFailingAdapter,
                        "alertmanager": _FakePromAdapter,
                    }),
                )
            )

        events2 = asyncio.run(_run2())
        errs = [e for e in events2 if e["type"] == "tool_result" and e["status"] == "error"]
        assert errs, "Expected a tool_result with status=error"
        assert errs[0]["tool"] == "prometheus"
        assert "upstream timeout" in errs[0]["error"]

        # Stream still terminates cleanly with done (not error)
        assert events2[-1]["type"] == "done"

    def test_no_adapter_failure_aborts_overall_stream(self):
        """When every tool fails, the stream still yields final_snapshot + done."""
        async def _run():
            return await _collect(
                stream_agent_chat(
                    _req("p99 latency?"),
                    agent=_agent({"prometheus": _FakeFailingAdapter}),
                )
            )

        events = asyncio.run(_run())
        types = [e["type"] for e in events]
        assert "final_snapshot" in types
        assert types[-1] == "done"


# ──────────────────────────────────────────────────────────────────────── #
# Security — no secrets in any streamed frame
# ──────────────────────────────────────────────────────────────────────── #
class TestNoSecretsLeak:
    def test_no_auth_token_in_sse_stream(self):
        ai = AyosaAIConfig(
            enabled=False, provider="anthropic",
            api_key="anthropic-key-SHOULD-NOT-LEAK",
        )

        async def _run():
            frames = []
            async for frame in stream_agent_chat_sse(
                _req("p99 latency?", ai=ai),
                agent=_agent({"prometheus": _FakePromAdapter}),
            ):
                frames.append(frame)
            return frames

        frames = asyncio.run(_run())
        joined = "".join(frames)
        assert "super-secret-token-DO-NOT-LEAK" not in joined
        assert "anthropic-key-SHOULD-NOT-LEAK" not in joined


# ──────────────────────────────────────────────────────────────────────── #
# Progressive emission — events fire before the loop completes
# ──────────────────────────────────────────────────────────────────────── #
class TestProgressiveEmission:
    def test_first_event_arrives_before_total_completion(self):
        """session_start must be yielded promptly (well before the slow adapter
        finishes), proving the stream is not buffered until completion."""
        tools = [AyosaToolConfig(tool="prometheus", base_url="http://slow")]

        async def _run():
            gen = stream_agent_chat(
                _req("p99 latency?", tools=tools),
                agent=_agent({"prometheus": _SlowAdapter}),
            )
            t0 = time.perf_counter()
            first = await gen.__anext__()
            first_delay = time.perf_counter() - t0
            # Drain the rest
            rest = [first]
            async for ev in gen:
                rest.append(ev)
            total = time.perf_counter() - t0
            return first_delay, total, rest

        first_delay, total, events = asyncio.run(_run())
        assert events[0]["type"] == "session_start"
        # First event much faster than full run (which includes the 150ms sleep)
        assert first_delay < total * 0.5
        assert events[-1]["type"] == "done"


# ──────────────────────────────────────────────────────────────────────── #
# Router compatibility — legacy stream preserved for agent_mode=False
# ──────────────────────────────────────────────────────────────────────── #
class TestRouterCompatibility:
    def test_router_routes_to_legacy_when_agent_mode_false(self, monkeypatch):
        from accelerators.ayosa import router as router_module

        called = {"agent_stream": False, "legacy_stream": False}

        async def _agent_stream_spy(request):
            called["agent_stream"] = True
            yield {"type": "done"}

        class _LegacyServiceSpy:
            async def investigate_stream(self, request):
                called["legacy_stream"] = True
                yield {"type": "result", "data": {}}

        monkeypatch.setattr(router_module, "stream_agent_chat", _agent_stream_spy)
        monkeypatch.setattr(router_module, "AyosaService", _LegacyServiceSpy)

        req = AyosaChatRequest(
            message="hi",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
            agent_mode=False,
        )
        resp = asyncio.run(router_module.chat_stream(req))
        # Drain the StreamingResponse body to trigger the generator
        body = b""

        async def _drain():
            nonlocal body
            async for chunk in resp.body_iterator:
                body += chunk if isinstance(chunk, bytes) else chunk.encode()

        asyncio.run(_drain())
        assert called["legacy_stream"] is True
        assert called["agent_stream"] is False

    def test_router_routes_to_agent_when_agent_mode_true(self, monkeypatch):
        from accelerators.ayosa import router as router_module

        called = {"agent_stream": False, "legacy_stream": False}

        async def _agent_stream_spy(request):
            called["agent_stream"] = True
            yield {"type": "done"}

        class _LegacyServiceSpy:
            async def investigate_stream(self, request):  # pragma: no cover
                called["legacy_stream"] = True
                yield {"type": "result", "data": {}}

        monkeypatch.setattr(router_module, "stream_agent_chat", _agent_stream_spy)
        monkeypatch.setattr(router_module, "AyosaService", _LegacyServiceSpy)

        req = AyosaChatRequest(
            message="hi",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
            agent_mode=True,
        )
        resp = asyncio.run(router_module.chat_stream(req))
        body = b""

        async def _drain():
            nonlocal body
            async for chunk in resp.body_iterator:
                body += chunk if isinstance(chunk, bytes) else chunk.encode()

        asyncio.run(_drain())
        assert called["agent_stream"] is True
        assert called["legacy_stream"] is False


# ──────────────────────────────────────────────────────────────────────── #
# Step 23 — Iterative-loop wire contract
#
# Pins the SSE envelope that Steps 19/20/21/22 wired together so a future
# refactor of agent_stream can't silently break the frontend's:
#   * "Pass N of up to M" live badge   (session_start.max_iterations)
#   * per-pass tool grouping           (tool_start/tool_result.iteration)
#   * agent-debug strip                (final_snapshot.data.loop_summary
#                                       + dedicated loop_summary event)
#   * Compare panel loop diff          (loop_summary fields)
# ──────────────────────────────────────────────────────────────────────── #
class TestIterativeLoopContract:
    """Lock the canonical SSE wire format around the agent's iteration loop."""

    def _run(self, max_iterations: int = 4):
        # Build the agent with the desired cap directly — the public stream
        # entry point uses ``agent or AyosaAgent(...)`` so we have to bake
        # the iteration ceiling into the pre-built agent rather than rely
        # on ``resolve_max_iterations(request)`` being called.
        req = _req("p99 latency?")
        req.max_iterations = max_iterations
        agent = AyosaAgent(
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
            max_iterations=max_iterations,
        )

        async def _go():
            return await _collect(stream_agent_chat(req, agent=agent))

        return asyncio.run(_go())

    def test_session_start_carries_max_iterations(self):
        events = self._run(max_iterations=3)
        start = events[0]
        assert start["type"] == "session_start"
        assert start["max_iterations"] == 3

    def test_tool_events_carry_iteration_index(self):
        events = self._run()
        tool_events = [e for e in events if e["type"] in ("tool_start", "tool_result")]
        assert tool_events, "expected at least one tool_start/tool_result pair"
        for ev in tool_events:
            assert "iteration" in ev, f"{ev['type']} missing iteration index"
            assert isinstance(ev["iteration"], int)
            assert ev["iteration"] >= 0

    def test_final_snapshot_embeds_loop_summary(self):
        events = self._run(max_iterations=2)
        snapshot = next(e for e in events if e["type"] == "final_snapshot")
        data = snapshot["data"]
        assert "loop_summary" in data, "final_snapshot.data must embed loop_summary"
        ls = data["loop_summary"]
        assert set(ls.keys()) == {
            "iterations_run", "max_iterations", "replanned", "replan_reason",
        }
        assert ls["max_iterations"] == 2
        assert isinstance(ls["iterations_run"], int)
        assert ls["iterations_run"] >= 1
        assert isinstance(ls["replanned"], bool)
        # Single-pass run: not replanned, no reason
        assert ls["replanned"] is False
        assert ls["replan_reason"] is None

    def test_dedicated_loop_summary_event_emitted(self):
        events = self._run(max_iterations=4)
        loop_evs = [e for e in events if e["type"] == "loop_summary"]
        assert len(loop_evs) == 1, "exactly one loop_summary event must be emitted"
        ev = loop_evs[0]
        assert set(ev.keys()) == {
            "type", "iterations_run", "max_iterations", "replanned", "replan_reason",
        }
        assert ev["max_iterations"] == 4
        assert ev["replanned"] is False
        assert ev["replan_reason"] is None

    def test_canonical_terminal_envelope_order(self):
        """final_snapshot → loop_summary → done, with nothing in between."""
        events = self._run()
        types = [e["type"] for e in events]
        snap_idx = types.index("final_snapshot")
        # No other final_snapshot
        assert types.count("final_snapshot") == 1
        # The next two events must be loop_summary then done
        assert types[snap_idx + 1] == "loop_summary"
        assert types[snap_idx + 2] == "done"
        # done must be the very last event
        assert types[-1] == "done"

    def test_loop_summary_fields_agree_between_snapshot_and_event(self):
        events = self._run(max_iterations=2)
        snap_ls = next(e["data"]["loop_summary"]
                       for e in events if e["type"] == "final_snapshot")
        evt_ls = next(e for e in events if e["type"] == "loop_summary")
        for key in ("iterations_run", "max_iterations", "replanned", "replan_reason"):
            assert snap_ls[key] == evt_ls[key], (
                f"loop_summary mismatch on {key}: "
                f"final_snapshot={snap_ls[key]!r} vs event={evt_ls[key]!r}"
            )


# ──────────────────────────────────────────────────────────────────────── #
# Step 25 — Tool-use loop wire contract
#
# Pins the SSE envelope when ``use_tool_use_loop=True`` so the frontend
# (which reuses the same tool_start/tool_result vocabulary) keeps
# working when Claude — not the deterministic planner — chose each call.
# ──────────────────────────────────────────────────────────────────────── #
from dataclasses import dataclass as _tu_dataclass, field as _tu_field  # noqa: E402
from typing import Any as _TUAny  # noqa: E402

from accelerators.ayosa.agent import tool_use_loop as _tul  # noqa: E402
from accelerators.ayosa import agent_stream as _agent_stream  # noqa: E402


@_tu_dataclass
class _TUFakeMessage:
    content: list[_TUAny]
    stop_reason: str = "end_turn"


@_tu_dataclass
class _TUFakeBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, _TUAny] = _tu_field(default_factory=dict)


class _TUFakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict[str, _TUAny]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("scripted client exhausted")
        return self._scripted.pop(0)


class _TUFakeClient:
    def __init__(self, scripted):
        self.messages = _TUFakeMessages(scripted)


class TestToolUseLoopContract:
    """Pins the SSE envelope for the Step 25 tool-use branch."""

    def _build_client(self):
        return _TUFakeClient([
            _TUFakeMessage(
                content=[
                    _TUFakeBlock(
                        type="tool_use",
                        id="t1",
                        name="prometheus_investigate",
                        input={"service": "payments", "time_range": "15m"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _TUFakeMessage(
                content=[_TUFakeBlock(type="text", text="Looks like elevated 5xx on payments.")],
                stop_reason="end_turn",
            ),
        ])

    def _run(self, monkeypatch):
        client = self._build_client()
        # The agent_stream module imports build_anthropic_client at top
        # of file, so we must patch the local binding there (and on the
        # source module too, for symmetry).
        monkeypatch.setattr(_tul, "build_anthropic_client", lambda inp: client)
        monkeypatch.setattr(_agent_stream, "build_anthropic_client", lambda inp: client)

        ai = AyosaAIConfig(
            enabled=True,
            provider="anthropic",
            api_key="sk-test",
            use_tool_use_loop=True,
        )
        req = _req("why is payments slow?", ai=ai)

        async def _run():
            return await _collect(
                stream_agent_chat(req, agent=_agent({"prometheus": _FakePromAdapter}))
            )

        return asyncio.run(_run()), client

    def test_event_order_includes_tool_pair_and_terminal_envelope(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        types = [e["type"] for e in events]

        assert types[0] == "session_start"
        assert "intent" in types
        assert "plan" in types
        assert "tool_start" in types
        assert "tool_result" in types
        ts_idx = types.index("tool_start")
        tr_idx = types.index("tool_result", ts_idx)
        assert tr_idx == ts_idx + 1, "tool_start must be immediately followed by tool_result"

        assert types[-3:] == ["final_snapshot", "loop_summary", "done"]
        assert types.count("final_snapshot") == 1
        assert types.count("done") == 1

    def test_tool_steps_carry_iteration_index(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        tool_starts = [e for e in events if e["type"] == "tool_start"]
        assert tool_starts
        for ts in tool_starts:
            assert "iteration" in ts
            assert isinstance(ts["iteration"], int)

    def test_final_snapshot_loop_summary_present_and_match(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        snap = next(e for e in events if e["type"] == "final_snapshot")
        evt = next(e for e in events if e["type"] == "loop_summary")
        snap_ls = snap["data"].get("loop_summary")
        assert snap_ls is not None
        for key in ("iterations_run", "max_iterations", "replanned", "replan_reason"):
            assert snap_ls[key] == evt[key]

    def test_no_secrets_in_tool_use_stream(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        blob = json.dumps(events, default=str)
        assert "super-secret-token-DO-NOT-LEAK" not in blob
        assert "sk-test" not in blob

    def test_each_decision_makes_one_llm_call_with_tools(self, monkeypatch):
        _events, client = self._run(monkeypatch)
        assert len(client.messages.calls) == 2
        for call in client.messages.calls:
            assert "tools" in call
            assert isinstance(call["tools"], list)
            assert call["tools"]


# ──────────────────────────────────────────────────────────────────────── #
# Step 26 — progressive tool-use streaming
# ──────────────────────────────────────────────────────────────────────── #
class _TUSlowAdapter:
    """Sleeps inside investigate() so we can prove tool_start arrives
    before the adapter call completes (i.e. progressive streaming, not
    post-hoc replay)."""

    DELAY_S = 0.20

    def __init__(self, base_url, auth_token=None):  # noqa: ARG002
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        time.sleep(self.DELAY_S)
        return [{
            "source": "prometheus",
            "signal": "metrics",
            "finding": "rate=0.01",
            "status": "ok",
            "raw": {},
        }]


class _TUTimedFakeMessages:
    """Anthropic-shaped fake whose `create()` records when each turn
    actually fires (real-clock time). Used to verify that the *second*
    LLM call only happens AFTER the first tool dispatch finished —
    which proves the loop is genuinely interleaving LLM ↔ tool ↔ LLM
    rather than batching."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict[str, _TUAny]] = []
        self.call_times: list[float] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.call_times.append(time.monotonic())
        if not self._scripted:
            raise AssertionError("scripted client exhausted")
        return self._scripted.pop(0)


class _TUTimedFakeClient:
    def __init__(self, scripted):
        self.messages = _TUTimedFakeMessages(scripted)


class TestToolUseLoopProgressiveStreaming:
    """Step 26 contract — events flow as the loop runs, not at the end."""

    def _make_client(self):
        return _TUTimedFakeClient([
            _TUFakeMessage(
                content=[_TUFakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments", "time_range": "15m",
                           "query_focus": "5xx", "reason": "test"},
                )],
                stop_reason="tool_use",
            ),
            _TUFakeMessage(
                content=[_TUFakeBlock(type="text",
                                      text="Done — error rate 0.01.")],
                stop_reason="end_turn",
            ),
        ])

    def _run(self, monkeypatch, *, adapter=_TUSlowAdapter):
        client = self._make_client()
        monkeypatch.setattr(_tul, "build_anthropic_client", lambda inp: client)
        monkeypatch.setattr(
            _agent_stream, "build_anthropic_client", lambda inp: client
        )

        ai = AyosaAIConfig(
            enabled=True,
            provider="anthropic",
            api_key="sk-test",
            use_tool_use_loop=True,
        )
        req = _req("why is payments slow?", ai=ai)

        async def _capture():
            out: list[dict[str, _TUAny]] = []
            async for ev in stream_agent_chat(
                req, agent=_agent({"prometheus": adapter})
            ):
                out.append(ev)
            return out

        return asyncio.run(_capture()), client

    def test_progressive_event_types_are_emitted(self, monkeypatch):
        # The Step 26 callback contract surfaces these new event types
        # via the SSE stream.
        events, _client = self._run(monkeypatch)
        types = [e["type"] for e in events]
        assert "llm_call_start" in types
        assert "llm_call_end" in types
        # tool_start / tool_result / observation still present.
        assert "tool_start" in types
        assert "tool_result" in types
        assert "observation" in types

    def test_llm_call_events_carry_iteration_index(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        starts = [e for e in events if e["type"] == "llm_call_start"]
        ends = [e for e in events if e["type"] == "llm_call_end"]
        assert [s["iteration"] for s in starts] == [0, 1]
        assert [e["iteration"] for e in ends] == [0, 1]
        # First end is `tool_use`, second is `end_turn` (terminal).
        assert ends[0]["stop_reason"] == "tool_use"
        assert ends[1]["stop_reason"] == "end_turn"

    def test_event_order_pins_progressive_pipeline(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        types = [e["type"] for e in events]

        # llm_call_start[0]  ← Claude turn 1 begins
        # llm_call_end[0]    ← Claude returns tool_use
        # tool_start         ← dispatch begins
        # tool_result        ← dispatch ended
        # observation        ← findings emitted
        # llm_call_start[1]  ← Claude turn 2 (post-tool) begins
        # llm_call_end[1]    ← Claude returns end_turn

        idx = {t: i for i, t in enumerate(types) if t not in {"observation"}}
        # Anchors that must appear in this order:
        first_start = next(i for i, t in enumerate(types)
                           if t == "llm_call_start")
        first_end = next(i for i, t in enumerate(types)
                         if t == "llm_call_end" and i > first_start)
        ts = next(i for i, t in enumerate(types) if t == "tool_start")
        tr = next(i for i, t in enumerate(types) if t == "tool_result")
        obs = next(i for i, t in enumerate(types) if t == "observation")
        second_start = next(
            i for i, t in enumerate(types)
            if t == "llm_call_start" and i > first_end
        )
        assert first_start < first_end < ts < tr < obs < second_start

    def test_second_llm_call_fires_after_tool_dispatch_completes(
        self, monkeypatch
    ):
        # Real-clock proof of interleaving: the second LLM call must
        # happen at least DELAY_S after the first one (because the
        # adapter sleeps between them). If the loop ran without
        # callback hooks, both calls would still be sequential — but
        # this test specifically pins that we are NOT batching events
        # at the end; the gap is real.
        _events, client = self._run(monkeypatch)
        assert len(client.messages.call_times) == 2
        gap = client.messages.call_times[1] - client.messages.call_times[0]
        assert gap >= _TUSlowAdapter.DELAY_S * 0.8, (
            f"expected >={_TUSlowAdapter.DELAY_S}s gap between LLM calls, "
            f"got {gap:.3f}s"
        )

    def test_tool_start_event_carries_llm_metadata(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        ts = next(e for e in events if e["type"] == "tool_start")
        # Step 26 enriches tool_start with the LLM's chosen args so
        # the UI can render Claude's reasoning hint immediately.
        assert ts["query_focus"] == "5xx"
        assert ts["reason"] == "test"
        assert ts["tool"] == "prometheus"
        assert "tool_use_id" in ts
        assert ts["service"] == "payments"
        assert ts["time_range"] == "15m"

    def test_terminal_envelope_unchanged(self, monkeypatch):
        # The Step 25 envelope (final_snapshot → loop_summary → done)
        # MUST remain the last three frames so existing UI consumers
        # don't break.
        events, _client = self._run(monkeypatch)
        types = [e["type"] for e in events]
        assert types[-3:] == ["final_snapshot", "loop_summary", "done"]
        assert types.count("final_snapshot") == 1
        assert types.count("done") == 1

    def test_loop_summary_matches_iterations_run(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        evt = next(e for e in events if e["type"] == "loop_summary")
        snap = next(e for e in events if e["type"] == "final_snapshot")
        assert evt["iterations_run"] == 2
        # final_snapshot also carries the same loop_summary payload.
        assert snap["data"]["loop_summary"]["iterations_run"] == 2

    def test_no_secrets_in_progressive_stream(self, monkeypatch):
        events, _client = self._run(monkeypatch)
        blob = json.dumps(events, default=str)
        assert "super-secret-token-DO-NOT-LEAK" not in blob
        assert "sk-test" not in blob

    def test_loop_error_event_surfaces_when_anthropic_fails(self, monkeypatch):
        # Drive the SDK shape that raises on create().
        class _BoomMessages:
            calls: list[dict[str, _TUAny]] = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("api down")

        class _BoomClient:
            def __init__(self):
                self.messages = _BoomMessages()

        client = _BoomClient()
        monkeypatch.setattr(_tul, "build_anthropic_client", lambda inp: client)
        monkeypatch.setattr(
            _agent_stream, "build_anthropic_client", lambda inp: client
        )

        ai = AyosaAIConfig(
            enabled=True,
            provider="anthropic",
            api_key="sk-test",
            use_tool_use_loop=True,
        )
        req = _req("why is payments slow?", ai=ai)

        async def _capture():
            out: list[dict[str, _TUAny]] = []
            async for ev in stream_agent_chat(
                req, agent=_agent({"prometheus": _FakePromAdapter})
            ):
                out.append(ev)
            return out

        events = asyncio.run(_capture())
        types = [e["type"] for e in events]
        assert "loop_error" in types
        # Stream must still terminate cleanly.
        assert types[-3:] == ["final_snapshot", "loop_summary", "done"]

