"""AYOSA Agent Backbone.

Additive layer on top of the existing AYOSA accelerator. Implements an
explicit Plan -> Act -> Observe -> Reflect -> Respond loop while reusing
the existing primitives:

  * `build_ayosa_plan` / intent classifier from `accelerators.ayosa.service`
  * `ADAPTERS` registry from `accelerators.ayosa.registry`
  * `AyosaAIAnalyst` from `accelerators.ayosa.llm.analyst`

The agent does NOT replace `AyosaService`. Existing /api/ayosa/chat and
/api/ayosa/chat/stream endpoints continue to work unchanged.
"""

from __future__ import annotations

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentResult,
    Observation,
    Plan,
    ReflectionNote,
    ToolStep,
    ConversationTurn,
)
from accelerators.ayosa.agent.intent_classifier import classify_intent
from accelerators.ayosa.agent.planner import Planner
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent.context_manager import ContextManager
from accelerators.ayosa.agent.synthesizer import Synthesizer

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AyosaAgent:
    """Orchestrator implementing Plan -> Act -> Observe -> Reflect -> Respond.

    Backward-compatible: never imported by the existing router/service path.
    Consumers wire it up explicitly when they want agentic behaviour.
    """

    def __init__(
        self,
        planner: Planner | None = None,
        dispatcher: ToolDispatcher | None = None,
        synthesizer: Synthesizer | None = None,
        context: ContextManager | None = None,
    ) -> None:
        self.planner = planner or Planner()
        self.dispatcher = dispatcher or ToolDispatcher()
        self.synthesizer = synthesizer or Synthesizer()
        self.context = context or ContextManager()

    # ------------------------------------------------------------------ #
    # Sync entry point
    # ------------------------------------------------------------------ #
    def run(self, agent_input: AgentInput) -> AgentResult:
        """Run the full loop synchronously."""
        intent = classify_intent(agent_input.message)
        plan = self.planner.build(agent_input, intent)

        tool_steps, observations = self.dispatcher.dispatch(plan, agent_input)
        reflections = self._reflect(plan, observations)

        result = self.synthesizer.synthesize(
            agent_input=agent_input,
            plan=plan,
            tool_steps=tool_steps,
            observations=observations,
            reflections=reflections,
        )

        # Persist turn so subsequent runs in the same session can use history.
        self.context.append(
            session_id=agent_input.session_id,
            turn=ConversationTurn(
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_message=agent_input.message,
                intent=intent,
                plan_summary=plan.explanation,
                answer=result.final_response,
            ),
        )
        return result

    # ------------------------------------------------------------------ #
    # Async entry point (offloads sync dispatcher to thread pool)
    # ------------------------------------------------------------------ #
    async def arun(self, agent_input: AgentInput) -> AgentResult:
        return await asyncio.to_thread(self.run, agent_input)

    # ------------------------------------------------------------------ #
    # Reflect — pure function over plan + observations
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reflect(plan: Plan, observations: list[Observation]) -> list[ReflectionNote]:
        return reflect_on_observations(plan, observations)


# ──────────────────────────────────────────────────────────────────────── #
# Pure function — exported for unit testing
# ──────────────────────────────────────────────────────────────────────── #
def reflect_on_observations(
    plan: Plan, observations: list[Observation]
) -> list[ReflectionNote]:
    """Inspect observations and produce deterministic reflection notes.

    A reflection captures *evidence sufficiency* per required signal:
      * missing      — signal required but no observation produced it
      * empty        — signal observed but yielded no findings
      * partial      — at least one ok finding, but errors also present
      * sufficient   — at least one ok finding, no errors
    """
    notes: list[ReflectionNote] = []
    by_signal: dict[str, list[Observation]] = {}
    for obs in observations:
        by_signal.setdefault(obs.signal, []).append(obs)

    for signal in plan.required_signals:
        sig_obs = by_signal.get(signal, [])
        if not sig_obs:
            notes.append(
                ReflectionNote(
                    signal=signal,
                    status="missing",
                    note=f"Required signal '{signal}' was not collected.",
                )
            )
            continue
        ok = [o for o in sig_obs if o.status == "ok"]
        err = [o for o in sig_obs if o.status == "error"]
        if not ok and not err:
            notes.append(
                ReflectionNote(
                    signal=signal,
                    status="empty",
                    note=f"'{signal}' tools returned no findings.",
                )
            )
        elif ok and err:
            notes.append(
                ReflectionNote(
                    signal=signal,
                    status="partial",
                    note=(
                        f"'{signal}' has {len(ok)} successful and "
                        f"{len(err)} failing observations."
                    ),
                )
            )
        elif ok:
            notes.append(
                ReflectionNote(
                    signal=signal,
                    status="sufficient",
                    note=f"'{signal}' has {len(ok)} successful observations.",
                )
            )
        elif err:
            notes.append(
                ReflectionNote(
                    signal=signal,
                    status="empty",
                    note=f"'{signal}' tools all failed ({len(err)} errors).",
                )
            )
    return notes


__all__ = [
    "AyosaAgent",
    "AgentInput",
    "AgentResult",
    "Observation",
    "Plan",
    "ReflectionNote",
    "ToolStep",
    "ConversationTurn",
    "classify_intent",
    "Planner",
    "ToolDispatcher",
    "ContextManager",
    "Synthesizer",
    "reflect_on_observations",
]
