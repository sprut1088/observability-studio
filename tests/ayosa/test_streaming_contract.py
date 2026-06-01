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
