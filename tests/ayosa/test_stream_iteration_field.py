"""Step 15 — assert SSE streaming events carry ``iteration`` so the UI can
render the live multi-pass trajectory grouped by re-plan boundary.

We don't test the multi-iteration replan path here (that's covered by the
iterative re-planner tests); we only assert the field is present and is
the expected integer for the first pass.
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


def test_tool_start_and_tool_result_carry_iteration():
    agent = AyosaAgent(dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}))
    events = asyncio.run(_collect(stream_agent_chat(_req(), agent=agent)))

    starts = [e for e in events if e["type"] == "tool_start"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert starts, "expected at least one tool_start event"
    assert results, "expected at least one tool_result event"
    for ev in starts + results:
        assert "iteration" in ev, f"event missing iteration: {ev}"
        assert isinstance(ev["iteration"], int)
        assert ev["iteration"] == 0  # first (and only) pass for this fixture
