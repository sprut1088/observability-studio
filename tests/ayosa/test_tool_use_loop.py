"""Tests for the Anthropic tool-use loop — Step 24.

These tests use a scripted fake Anthropic client and the existing
``ToolDispatcher`` (with fake adapters) so we exercise the loop
end-to-end without any network or real LLM dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentToolConfig,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent.tool_use_loop import (
    ToolUseLoop,
    ToolUseLoopResult,
    build_anthropic_tool_schemas,
    _anthropic_to_tool_name,
    _build_synthetic_plan,
    _extract_text_blocks,
    _extract_tool_use_blocks,
    _observations_to_tool_result,
    _serialize_assistant_content,
    _tool_name_to_anthropic,
)


# ──────────────────────────────────────────────────────────────────────── #
# Fakes
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url
        self.last_kwargs: dict[str, Any] | None = None

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        self.last_kwargs = {
            "service": service,
            "time_range": time_range,
            "plan": plan,
        }
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": f"rate(http_5xx{{service='{service}'}}) = 0.02",
                "query": "rate(http_requests_total[5m])",
                "status": "ok",
                "raw": {},
            }
        ]


class _FakeLokiAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "loki",
                "signal": "logs",
                "finding": "47 ERROR lines matching 'connection refused'",
                "status": "ok",
                "raw": {},
            }
        ]


class _FakeFailingAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        raise RuntimeError("upstream timeout")


@dataclass
class _FakeMessage:
    """Mimic the shape of ``anthropic.types.Message``."""
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
            raise AssertionError(
                "FakeAnthropicClient ran out of scripted responses; "
                "the loop made more calls than the test expected."
            )
        return self._scripted.pop(0)


class _FakeAnthropicClient:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self.messages = _FakeMessages(scripted)


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _agent_input(*tool_names: str, message: str = "p99 on payments?") -> AgentInput:
    return AgentInput(
        message=message,
        service="payments",
        time_range="15m",
        tools=[
            AgentToolConfig(tool=n, base_url=f"http://{n}.local", auth_token="t")
            for n in tool_names
        ],
    )


# ──────────────────────────────────────────────────────────────────────── #
# Pure helpers
# ──────────────────────────────────────────────────────────────────────── #
class TestPureHelpers:
    def test_tool_name_roundtrip(self):
        assert _tool_name_to_anthropic("prometheus") == "prometheus_investigate"
        assert _tool_name_to_anthropic("Splunk") == "splunk_investigate"
        assert _anthropic_to_tool_name("prometheus_investigate") == "prometheus"
        assert _anthropic_to_tool_name("loki_investigate") == "loki"
        # Unknown / unsuffixed names pass through unchanged.
        assert _anthropic_to_tool_name("weird") == "weird"

    def test_tool_name_sanitizes_invalid_chars(self):
        name = _tool_name_to_anthropic("my.tool/with spaces")
        # Anthropic allows only [a-zA-Z0-9_-]{1,64}
        assert all(c.isalnum() or c in "_-" for c in name)
        assert name.endswith("_investigate")

    def test_build_schemas_emits_one_per_tool_type(self):
        ai = _agent_input("prometheus", "loki", "PROMETHEUS")  # duplicate by case
        schemas = build_anthropic_tool_schemas(ai.tools)
        names = [s["name"] for s in schemas]
        assert names == ["prometheus_investigate", "loki_investigate"]

    def test_build_schemas_includes_required_input_fields(self):
        ai = _agent_input("prometheus")
        schemas = build_anthropic_tool_schemas(ai.tools)
        s = schemas[0]
        assert s["input_schema"]["type"] == "object"
        assert "service" in s["input_schema"]["required"]
        for field_name in ("service", "time_range", "query_focus", "reason"):
            assert field_name in s["input_schema"]["properties"], field_name

    def test_build_schemas_uses_known_description(self):
        ai = _agent_input("prometheus")
        s = build_anthropic_tool_schemas(ai.tools)[0]
        assert "Prometheus" in s["description"]
        assert "alert" in s["description"].lower()

    def test_build_schemas_falls_back_for_unknown_tool(self):
        ai = _agent_input("mystery_tool")
        s = build_anthropic_tool_schemas(ai.tools)[0]
        assert "mystery_tool" in s["description"]

    def test_build_synthetic_plan_carries_tool_args(self):
        ai = _agent_input("prometheus")
        plan = _build_synthetic_plan(
            ai, "prometheus",
            {"service": "checkout", "time_range": "1h",
             "query_focus": "p99 latency", "reason": "high error rate"},
        )
        assert plan.selected_tools == ["prometheus"]
        assert plan.service == "checkout"
        assert plan.time_range == "1h"
        assert plan.tool_args["prometheus"]["query"] == "p99 latency"
        assert plan.selection_meta == {"mode": "tool_use_llm"}

    def test_extract_text_blocks_handles_mixed_content(self):
        content = [
            _FakeBlock(type="text", text="Hello"),
            _FakeBlock(type="tool_use", id="t1", name="x", input={}),
            _FakeBlock(type="text", text="World"),
        ]
        assert _extract_text_blocks(content) == "Hello\nWorld"

    def test_extract_text_blocks_handles_string_content(self):
        assert _extract_text_blocks("plain string") == "plain string"
        assert _extract_text_blocks(None) == ""

    def test_extract_tool_use_blocks(self):
        content = [
            _FakeBlock(type="text", text="reasoning"),
            _FakeBlock(type="tool_use", id="t1", name="prometheus_investigate",
                       input={"service": "payments"}),
            _FakeBlock(type="tool_use", id="t2", name="loki_investigate",
                       input={"service": "payments"}),
        ]
        calls = _extract_tool_use_blocks(content)
        assert [c["id"] for c in calls] == ["t1", "t2"]
        assert calls[0]["name"] == "prometheus_investigate"
        assert calls[0]["input"] == {"service": "payments"}

    def test_serialize_assistant_content_roundtrips_to_dicts(self):
        content = [
            _FakeBlock(type="text", text="thinking"),
            _FakeBlock(type="tool_use", id="t1", name="x_investigate",
                       input={"a": 1}),
        ]
        serialized = _serialize_assistant_content(content)
        assert serialized == [
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "id": "t1", "name": "x_investigate",
             "input": {"a": 1}},
        ]

    def test_observations_to_tool_result_is_valid_json(self):
        from accelerators.ayosa.agent.schemas import Observation
        body = _observations_to_tool_result(
            [Observation(source="prom", signal="metrics",
                         finding="x=1", status="ok")]
        )
        parsed = json.loads(body)
        assert parsed["observations"][0]["finding"] == "x=1"

    def test_observations_to_tool_result_error_branch(self):
        body = _observations_to_tool_result([], error_message="boom")
        assert json.loads(body) == {"error": "boom"}


# ──────────────────────────────────────────────────────────────────────── #
# Loop construction
# ──────────────────────────────────────────────────────────────────────── #
class TestConstruction:
    def test_requires_client(self):
        with pytest.raises(ValueError):
            ToolUseLoop(client=None, dispatcher=ToolDispatcher({}))

    def test_requires_dispatcher(self):
        with pytest.raises(ValueError):
            ToolUseLoop(client=_FakeAnthropicClient([]), dispatcher=None)

    def test_max_iterations_has_floor_of_one(self):
        loop = ToolUseLoop(
            client=_FakeAnthropicClient([]),
            dispatcher=ToolDispatcher({}),
            max_iterations=0,
        )
        assert loop.max_iterations == 1


# ──────────────────────────────────────────────────────────────────────── #
# End-to-end loop behaviour
# ──────────────────────────────────────────────────────────────────────── #
class TestLoopBehaviour:
    def test_zero_tool_calls_returns_final_text_immediately(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(type="text", text="Nothing to investigate.")],
                stop_reason="end_turn",
            )
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))

        assert isinstance(result, ToolUseLoopResult)
        assert result.final_response == "Nothing to investigate."
        assert result.tool_steps == []
        assert result.observations == []
        assert result.iterations_run == 1
        assert result.stop_reason == "end_turn"

    def test_single_tool_call_then_final_answer(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[
                    _FakeBlock(type="text", text="Checking metrics."),
                    _FakeBlock(
                        type="tool_use", id="t1",
                        name="prometheus_investigate",
                        input={"service": "payments", "time_range": "15m",
                               "query_focus": "5xx rate",
                               "reason": "user asked about errors"},
                    ),
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text",
                                    text="Error rate is 0.02 — within SLO.")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))

        assert result.final_response == "Error rate is 0.02 — within SLO."
        assert result.iterations_run == 2
        assert result.stop_reason == "end_turn"
        assert len(result.tool_steps) == 1
        assert result.tool_steps[0].tool == "prometheus"
        assert result.tool_steps[0].status == "done"
        assert result.tool_steps[0].iteration == 0
        assert result.tool_steps[0].index == 0
        assert len(result.observations) == 1
        assert "rate(http_5xx" in result.observations[0].finding

    def test_tool_result_is_appended_as_user_message(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="abc",
                    name="prometheus_investigate",
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="done")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))

        # messages: user → assistant(tool_use) → user(tool_result) → assistant(text)
        roles = [m["role"] for m in result.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        tool_result_msg = result.messages[2]["content"]
        assert isinstance(tool_result_msg, list)
        assert tool_result_msg[0]["type"] == "tool_result"
        assert tool_result_msg[0]["tool_use_id"] == "abc"
        assert tool_result_msg[0]["is_error"] is False
        # tool_result content is JSON-encoded observations
        parsed = json.loads(tool_result_msg[0]["content"])
        assert parsed["observations"][0]["source"] == "prometheus"

    def test_multi_tool_sequence(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(type="tool_use", id="t1",
                                    name="prometheus_investigate",
                                    input={"service": "payments"})],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="tool_use", id="t2",
                                    name="loki_investigate",
                                    input={"service": "payments"})],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text",
                                    text="Metrics ok; logs show connection refused.")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({
                "prometheus": _FakePromAdapter,
                "loki": _FakeLokiAdapter,
            }),
        )
        result = loop.run(_agent_input("prometheus", "loki"))

        assert result.iterations_run == 3
        assert [s.tool for s in result.tool_steps] == ["prometheus", "loki"]
        # Iteration index of the second tool call must reflect the second pass.
        assert result.tool_steps[0].iteration == 0
        assert result.tool_steps[1].iteration == 1
        # Indices are monotonically increasing across the merged history.
        assert [s.index for s in result.tool_steps] == [0, 1]
        sources = {o.source for o in result.observations}
        assert sources == {"prometheus", "loki"}

    def test_unknown_tool_name_surfaces_as_is_error_without_dispatch(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="datadog_investigate",  # NOT in agent_input.tools
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="Recovered.")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))  # only prometheus configured

        assert result.final_response == "Recovered."
        assert len(result.tool_steps) == 1
        err_step = result.tool_steps[0]
        assert err_step.status == "error"
        assert err_step.tool == "datadog"
        assert "not configured" in (err_step.error or "")
        # The error is surfaced back to the model.
        tool_result_msg = result.messages[2]["content"][0]
        assert tool_result_msg["is_error"] is True

    def test_adapter_exception_surfaces_as_is_error_and_loop_continues(self):
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text",
                                    text="Prometheus failed — escalate.")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakeFailingAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))

        assert result.final_response == "Prometheus failed — escalate."
        assert result.tool_steps[0].status == "error"
        # Dispatcher's failure path produces one error observation.
        assert any(o.status == "error" for o in result.observations)
        tool_result_msg = result.messages[2]["content"][0]
        assert tool_result_msg["is_error"] is True

    def test_max_iterations_caps_runaway_loop(self):
        # Script returns tool_use forever; loop must bail at the cap.
        always_tool_use = _FakeMessage(
            content=[_FakeBlock(
                type="tool_use", id="t",
                name="prometheus_investigate",
                input={"service": "payments"},
            )],
            stop_reason="tool_use",
        )
        client = _FakeAnthropicClient([always_tool_use] * 10)
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
            max_iterations=3,
        )
        result = loop.run(_agent_input("prometheus"))

        assert result.iterations_run == 3
        assert result.stop_reason == "max_iterations"
        assert len(result.tool_steps) == 3
        # Exactly three LLM calls were made — no over-run.
        assert len(client.messages.calls) == 3

    def test_anthropic_client_error_is_captured(self):
        class _BoomClient:
            class messages:  # noqa: N801 — mimic SDK shape
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("api down")

        loop = ToolUseLoop(
            client=_BoomClient(),
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))

        assert result.stop_reason == "error"
        assert "api down" in result.final_response
        assert result.iterations_run == 1
        assert result.tool_steps == []

    def test_schemas_are_passed_to_anthropic_call(self):
        client = _FakeAnthropicClient([
            _FakeMessage(content=[_FakeBlock(type="text", text="done")],
                         stop_reason="end_turn")
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
            model="claude-sonnet-4-6",
        )
        loop.run(_agent_input("prometheus", "loki"))

        call = client.messages.calls[0]
        assert call["model"] == "claude-sonnet-4-6"
        assert call["system"]  # default system prompt present
        names = [t["name"] for t in call["tools"]]
        assert names == ["prometheus_investigate", "loki_investigate"]

    def test_llm_supplied_service_override_reaches_adapter(self):
        prom = _FakePromAdapter(base_url="http://prom.local")
        # Inject a single-instance adapter so we can inspect its last_kwargs.
        adapters = {"prometheus": lambda base_url, auth_token=None: prom}
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "checkout", "time_range": "1h"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="done")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(client=client, dispatcher=ToolDispatcher(adapters))
        loop.run(_agent_input("prometheus"))  # default service=payments

        # The LLM's per-call override must win over the AgentInput default.
        assert prom.last_kwargs["service"] == "checkout"
        assert prom.last_kwargs["time_range"] == "1h"


# ──────────────────────────────────────────────────────────────────────── #
# Step 26 — progressive callback hooks
# ──────────────────────────────────────────────────────────────────────── #
class TestProgressiveCallbacks:
    """Pin the on_event callback contract added in Step 26."""

    def _capture_run(self, scripted, adapters, *, max_iterations=4):
        client = _FakeAnthropicClient(scripted)
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher(adapters),
            max_iterations=max_iterations,
        )
        events: list[dict[str, Any]] = []
        result = loop.run(
            _agent_input(*adapters.keys()),
            on_event=lambda ev: events.append(ev),
        )
        return events, result

    def test_no_callback_means_no_emission_and_no_crash(self):
        # Default behaviour: on_event=None must not raise and must not
        # change the final result. Use the canonical happy-path script.
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="done")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        result = loop.run(_agent_input("prometheus"))  # no on_event

        assert result.final_response == "done"
        assert result.iterations_run == 2

    def test_event_order_for_single_tool_call(self):
        events, result = self._capture_run(
            scripted=[
                _FakeMessage(
                    content=[
                        _FakeBlock(type="text", text="Checking metrics."),
                        _FakeBlock(
                            type="tool_use", id="t1",
                            name="prometheus_investigate",
                            input={"service": "payments",
                                   "query_focus": "5xx",
                                   "reason": "user asked"},
                        ),
                    ],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(type="text", text="All good.")],
                    stop_reason="end_turn",
                ),
            ],
            adapters={"prometheus": _FakePromAdapter},
        )

        types = [e["type"] for e in events]
        # Expected sequence:
        #   llm_call_start (it=0)
        #   llm_call_end   (it=0, stop_reason=tool_use)
        #   tool_start
        #   tool_result
        #   observation
        #   llm_call_start (it=1)
        #   llm_call_end   (it=1, stop_reason=end_turn)
        assert types == [
            "llm_call_start",
            "llm_call_end",
            "tool_start",
            "tool_result",
            "observation",
            "llm_call_start",
            "llm_call_end",
        ]
        assert result.iterations_run == 2

    def test_tool_start_carries_llm_input_metadata(self):
        events, _ = self._capture_run(
            scripted=[
                _FakeMessage(
                    content=[_FakeBlock(
                        type="tool_use", id="abc",
                        name="prometheus_investigate",
                        input={"service": "checkout",
                               "time_range": "1h",
                               "query_focus": "p99 latency",
                               "reason": "user asked"},
                    )],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(type="text", text="done")],
                    stop_reason="end_turn",
                ),
            ],
            adapters={"prometheus": _FakePromAdapter},
        )
        ts = next(e for e in events if e["type"] == "tool_start")
        assert ts["tool"] == "prometheus"
        assert ts["iteration"] == 0
        assert ts["index"] == 0
        assert ts["query_focus"] == "p99 latency"
        assert ts["reason"] == "user asked"
        assert ts["service"] == "checkout"
        assert ts["time_range"] == "1h"
        assert ts["tool_use_id"] == "abc"
        assert ts["label"].lower().startswith("querying")

    def test_tool_start_emitted_before_dispatch(self):
        # Wire an adapter that records the relative order of its
        # invocation vs the tool_start callback. The callback must fire
        # FIRST (otherwise progressive UI would appear delayed).
        seen: list[str] = []

        class _OrderingAdapter:
            def __init__(self, base_url, auth_token=None):
                self.base_url = base_url

            def investigate(self, service, time_range, message, plan=None):
                seen.append("dispatch")
                return [{"source": "prometheus", "signal": "metrics",
                         "finding": "ok", "status": "ok", "raw": {}}]

        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="done")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _OrderingAdapter}),
        )

        def _cb(ev):
            if ev["type"] == "tool_start":
                seen.append("tool_start")
            elif ev["type"] == "tool_result":
                seen.append("tool_result")

        loop.run(_agent_input("prometheus"), on_event=_cb)

        # Required ordering: tool_start → dispatch → tool_result
        assert seen == ["tool_start", "dispatch", "tool_result"]

    def test_observation_event_emitted_per_finding(self):
        class _MultiFindAdapter:
            def __init__(self, base_url, auth_token=None):
                self.base_url = base_url

            def investigate(self, service, time_range, message, plan=None):
                return [
                    {"source": "prometheus", "signal": "metrics",
                     "finding": f"finding-{i}", "status": "ok", "raw": {}}
                    for i in range(3)
                ]

        events, _ = self._capture_run(
            scripted=[
                _FakeMessage(
                    content=[_FakeBlock(
                        type="tool_use", id="t1",
                        name="prometheus_investigate",
                        input={"service": "payments"},
                    )],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(type="text", text="done")],
                    stop_reason="end_turn",
                ),
            ],
            adapters={"prometheus": _MultiFindAdapter},
        )
        obs_events = [e for e in events if e["type"] == "observation"]
        assert len(obs_events) == 3
        assert [e["finding"] for e in obs_events] == [
            "finding-0", "finding-1", "finding-2",
        ]
        # The matching tool_result must report the correct count.
        tr = next(e for e in events if e["type"] == "tool_result")
        assert tr["findings_count"] == 3

    def test_unknown_tool_emits_error_tool_result_without_dispatch(self):
        events, _ = self._capture_run(
            scripted=[
                _FakeMessage(
                    content=[_FakeBlock(
                        type="tool_use", id="t1",
                        name="datadog_investigate",  # not configured
                        input={"service": "payments"},
                    )],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(type="text", text="recovered")],
                    stop_reason="end_turn",
                ),
            ],
            adapters={"prometheus": _FakePromAdapter},
        )
        tr = next(e for e in events if e["type"] == "tool_result")
        assert tr["status"] == "error"
        assert tr["findings_count"] == 0
        assert tr["tool"] == "datadog"
        assert "not configured" in tr.get("error", "")
        # No observation event should be emitted for the unknown tool.
        assert not any(e["type"] == "observation" for e in events)

    def test_loop_error_event_on_anthropic_failure(self):
        class _BoomClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("api down")

        loop = ToolUseLoop(
            client=_BoomClient(),
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )
        events: list[dict[str, Any]] = []
        result = loop.run(
            _agent_input("prometheus"),
            on_event=lambda ev: events.append(ev),
        )

        types = [e["type"] for e in events]
        assert types == ["llm_call_start", "loop_error"]
        err = events[-1]
        assert err["iteration"] == 0
        assert "api down" in err["error"]
        assert result.stop_reason == "error"

    def test_callback_exception_does_not_crash_loop(self, caplog):
        # A noisy/buggy consumer must not be able to break the loop.
        client = _FakeAnthropicClient([
            _FakeMessage(
                content=[_FakeBlock(
                    type="tool_use", id="t1",
                    name="prometheus_investigate",
                    input={"service": "payments"},
                )],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="done")],
                stop_reason="end_turn",
            ),
        ])
        loop = ToolUseLoop(
            client=client,
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        )

        def _bad_cb(ev):
            raise RuntimeError("consumer blew up")

        result = loop.run(_agent_input("prometheus"), on_event=_bad_cb)
        # Loop completed despite the broken consumer.
        assert result.final_response == "done"
        assert result.iterations_run == 2

    def test_iteration_index_matches_run_count(self):
        events, result = self._capture_run(
            scripted=[
                _FakeMessage(
                    content=[_FakeBlock(
                        type="tool_use", id="t1",
                        name="prometheus_investigate",
                        input={"service": "payments"},
                    )],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(
                        type="tool_use", id="t2",
                        name="loki_investigate",
                        input={"service": "payments"},
                    )],
                    stop_reason="tool_use",
                ),
                _FakeMessage(
                    content=[_FakeBlock(type="text", text="done")],
                    stop_reason="end_turn",
                ),
            ],
            adapters={"prometheus": _FakePromAdapter, "loki": _FakeLokiAdapter},
        )
        starts = [e for e in events if e["type"] == "llm_call_start"]
        ends = [e for e in events if e["type"] == "llm_call_end"]
        assert [s["iteration"] for s in starts] == [0, 1, 2]
        assert [e["iteration"] for e in ends] == [0, 1, 2]
        # The last llm_call_end carries the terminal stop_reason.
        assert ends[-1]["stop_reason"] == "end_turn"
        assert result.iterations_run == 3

