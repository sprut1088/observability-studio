"""Step 20 — single-pass short-circuit + loop_summary SSE event.

Two guarantees verified here:

1. ``AyosaAgent.run`` no longer calls ``_reflect`` inside the loop on the
   final-permitted iteration. For ``max_iterations == 1`` this means
   reflection runs exactly once (the post-loop ``final_reflections`` call
   that feeds the synthesizer), not twice.
2. ``stream_agent_chat`` emits a canonical ``loop_summary`` event right
   before ``done`` carrying ``iterations_run``, ``max_iterations``,
   ``replanned`` and ``replan_reason`` — so the UI and persistence layer
   don't have to scrape it out of ``final_snapshot``.
"""

from __future__ import annotations

import asyncio

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_stream import stream_agent_chat
from accelerators.ayosa.models import AyosaChatRequest, AyosaToolConfig


class _FakePromAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": "ok",
                "status": "ok",
                "raw": {},
            }
        ]


def _req() -> AyosaChatRequest:
    return AyosaChatRequest(
        message="any anomalies in payments?",
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


# ── 1. Short-circuit ─────────────────────────────────────────────── #

def test_single_pass_skips_in_loop_reflection():
    """``_reflect`` should run exactly once when ``max_iterations == 1``.

    Pre-Step-20 it ran twice (once inside the for-loop, once post-loop).
    """
    agent = AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=1,
    )

    calls = {"n": 0}
    original = agent._reflect

    def _counting_reflect(plan, observations):
        calls["n"] += 1
        return original(plan, observations)

    agent._reflect = _counting_reflect  # type: ignore[method-assign]

    from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

    agent_input = AgentInput(
        message="any anomalies in payments?",
        service="payments",
        time_range="15m",
        tools=[AgentToolConfig(tool="prometheus", base_url="http://prom.local")],
        session_id="t-step20",
    )
    result = agent.run(agent_input)
    assert result.iterations == 1
    assert calls["n"] == 1, f"reflection ran {calls['n']} times; expected 1 for single-pass"


def test_multi_pass_still_reflects_each_iteration():
    """Sanity: when the cap allows re-plan, reflection still fires per-iter."""
    agent = AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=3,
    )
    calls = {"n": 0}
    original = agent._reflect

    def _counting_reflect(plan, observations):
        calls["n"] += 1
        return original(plan, observations)

    agent._reflect = _counting_reflect  # type: ignore[method-assign]

    from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

    agent_input = AgentInput(
        message="any anomalies in payments?",
        service="payments",
        time_range="15m",
        tools=[AgentToolConfig(tool="prometheus", base_url="http://prom.local")],
        session_id="t-step20-multi",
    )
    agent.run(agent_input)
    # The agent re-plans only when there are gaps; with a single fake tool that
    # returns ok signals, the deterministic re-planner finds nothing to add and
    # breaks early. We just need >= 1 reflection (no regression).
    assert calls["n"] >= 1


# ── 2. loop_summary SSE event ────────────────────────────────────── #

def test_stream_emits_loop_summary_before_done():
    agent = AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=2,
    )
    events = asyncio.run(_collect(stream_agent_chat(_req(), agent=agent)))

    types = [e["type"] for e in events]
    assert "loop_summary" in types, f"missing loop_summary; got {types}"
    assert "done" in types
    assert types.index("loop_summary") < types.index("done"), (
        "loop_summary must precede done so consumers can record it before stream end"
    )


def test_loop_summary_payload_shape():
    agent = AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=4,
    )
    events = asyncio.run(_collect(stream_agent_chat(_req(), agent=agent)))
    summary = next(e for e in events if e["type"] == "loop_summary")

    assert summary["iterations_run"] >= 1
    assert summary["max_iterations"] == 4
    assert summary["replanned"] is (summary["iterations_run"] > 1)
    # replan_reason is None when no re-plan happened; otherwise a non-empty str.
    if summary["replanned"]:
        assert isinstance(summary["replan_reason"], str) and summary["replan_reason"]
    else:
        assert summary["replan_reason"] is None
