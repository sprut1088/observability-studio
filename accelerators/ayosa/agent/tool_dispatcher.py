"""Tool dispatcher — Act + Observe phases.

Uses the existing `accelerators.ayosa.registry.ADAPTERS` map to dispatch
queries through the adapters that the AYOSA chat path already uses.
No adapter logic is duplicated here.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from accelerators.ayosa.registry import ADAPTERS
from accelerators.ayosa.agent.schemas import (
    AgentInput,
    Observation,
    Plan,
    ToolStep,
)

logger = logging.getLogger(__name__)


class ToolDispatcher:
    """Executes the plan's `selected_tools` against the AYOSA adapter registry."""

    def __init__(self, adapters: dict[str, type] | None = None) -> None:
        # Allow injection for tests; default to the real registry.
        self._adapters = adapters if adapters is not None else ADAPTERS

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def dispatch(
        self,
        plan: Plan,
        agent_input: AgentInput,
    ) -> tuple[list[ToolStep], list[Observation]]:
        """Run every selected tool and return (steps, observations).

        Tools not in `plan.selected_tools` are emitted as `skipped` steps
        without being invoked. Adapter exceptions are captured per-tool
        and surface as error observations so a single bad tool can't
        abort the whole investigation.
        """
        steps: list[ToolStep] = []
        observations: list[Observation] = []
        selected = set(plan.selected_tools)

        for idx, tool in enumerate(agent_input.tools):
            tool_key = tool.tool.lower().strip()
            label = f"Querying {tool.tool}"

            if tool_key not in selected:
                steps.append(
                    ToolStep(index=idx, tool=tool.tool, label=label, status="skipped")
                )
                continue

            step = ToolStep(index=idx, tool=tool.tool, label=label, status="running")
            steps.append(step)

            try:
                tool_obs = self._invoke_adapter(tool, agent_input, plan)
                observations.extend(tool_obs)
                step.status = "done"
            except Exception as exc:  # noqa: BLE001 — never abort agent on adapter failure
                logger.warning("Adapter %s failed: %s", tool.tool, exc)
                step.status = "error"
                step.error = str(exc)
                observations.append(
                    Observation(
                        source=tool.tool,
                        signal="unknown",
                        finding=f"Adapter failed: {exc}",
                        query=None,
                        status="error",
                        raw=None,
                    )
                )

        return steps, observations

    # ------------------------------------------------------------------ #
    # Internal — kept narrow so it's easy to mock in tests
    # ------------------------------------------------------------------ #
    def _invoke_adapter(
        self,
        tool: Any,
        agent_input: AgentInput,
        plan: Plan,
    ) -> list[Observation]:
        tool_key = tool.tool.lower().strip()
        adapter_cls = self._adapters.get(tool_key)
        if not adapter_cls:
            return [
                Observation(
                    source=tool.tool,
                    signal="unknown",
                    finding=f"No AYOSA adapter found for tool: {tool.tool}",
                    status="error",
                )
            ]

        adapter = adapter_cls(base_url=tool.base_url, auth_token=tool.auth_token)
        kwargs: dict[str, Any] = {
            "service": agent_input.service,
            "time_range": plan.time_range or agent_input.time_range,
            "message": agent_input.message,
        }

        # Pass `plan` only if the adapter's investigate() declares it (or **kwargs).
        if _adapter_accepts_plan(adapter):
            kwargs["plan"] = plan.model_dump()

        raw = adapter.investigate(**kwargs)
        return [normalise_observation(item) for item in (raw or [])]


# ──────────────────────────────────────────────────────────────────────── #
# Pure helpers — unit-testable
# ──────────────────────────────────────────────────────────────────────── #
def _adapter_accepts_plan(adapter: Any) -> bool:
    try:
        sig = inspect.signature(adapter.investigate)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "plan" in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def normalise_observation(item: Any) -> Observation:
    """Convert an adapter result (dict or Observation) into an Observation."""
    if isinstance(item, Observation):
        return item
    if not isinstance(item, dict):
        return Observation(
            source="unknown",
            signal="unknown",
            finding=str(item),
            status="ok",
        )
    return Observation(
        source=str(item.get("source", "unknown")),
        signal=str(item.get("signal", "unknown")),
        finding=str(item.get("finding", "")),
        query=item.get("query"),
        status=str(item.get("status", "ok")),
        raw=item.get("raw"),
    )


__all__ = ["ToolDispatcher", "normalise_observation"]
