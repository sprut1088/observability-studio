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
from accelerators.ayosa.agent.intent_classifier import (
    classify_intent,
    classify_intent_smart,
)
from accelerators.ayosa.agent.planner import Planner
from accelerators.ayosa.agent.replanner import (
    build_replan,
    derived_input_for_replan,
    replan_reason,
)
from accelerators.ayosa.agent.iterative_replanner import build_llm_replan
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
        max_iterations: int = 2,
    ) -> None:
        self.planner = planner or Planner()
        self.dispatcher = dispatcher or ToolDispatcher()
        self.synthesizer = synthesizer or Synthesizer()
        self.context = context or ContextManager()
        # Hard floor of 1 keeps single-pass semantics when callers
        # pass 0 or a negative value by accident.
        self.max_iterations = max(1, int(max_iterations))

    # ------------------------------------------------------------------ #
    # Sync entry point
    # ------------------------------------------------------------------ #
    def run(self, agent_input: AgentInput) -> AgentResult:
        """Run the full loop synchronously.

        Implements Plan → Act → Observe → [Re-plan if gaps] → Synthesize.
        Re-plan iterations are capped by ``self.max_iterations`` and only
        trigger when reflections show missing/empty required signals AND
        additional tools are configured that could cover them.
        """
        intent, intent_meta = classify_intent_smart(
            agent_input.message,
            llm_config=agent_input.llm,
            service_hint=agent_input.service,
        )
        plan = self.planner.build(agent_input, intent)

        all_steps: list[ToolStep] = []
        all_observations: list[Observation] = []
        dispatched: set[str] = set()
        iterations_run = 0
        replan_explanation: str | None = None

        current_plan = plan
        current_input = agent_input

        for iteration in range(self.max_iterations):
            iterations_run += 1
            steps, observations = self.dispatcher.dispatch(
                current_plan, current_input
            )
            for s in steps:
                s.iteration = iteration
                if s.status in ("done", "error"):
                    dispatched.add(s.tool.lower().strip())
            all_steps.extend(steps)
            all_observations.extend(observations)

            # Reflect against the ORIGINAL plan so required_signals
            # accounting stays canonical across iterations.
            reflections = self._reflect(plan, all_observations)

            if iteration + 1 >= self.max_iterations:
                break

            # Step 13: try the LLM-driven iterative re-plan first. It can
            # both pick previously-unused tools AND re-call an already
            # dispatched tool with a refined query. Falls back to the
            # deterministic gap-filling re-plan when the LLM is disabled
            # or returns nothing useful.
            next_plan = build_llm_replan(
                original_plan=plan,
                agent_input=agent_input,
                observations=all_observations,
                already_dispatched=dispatched,
            )
            if next_plan is None:
                next_plan = build_replan(
                    original_plan=plan,
                    agent_input=agent_input,
                    reflections=reflections,
                    already_dispatched=dispatched,
                )
            if next_plan is None:
                break

            mode = (next_plan.selection_meta or {}).get("mode", "deterministic")
            if mode == "llm_iterative":
                replan_explanation = (
                    f"LLM iterative re-plan: {', '.join(next_plan.selected_tools)}. "
                    f"{(next_plan.selection_meta or {}).get('reasoning', '')}"
                ).strip()
            else:
                replan_explanation = replan_reason(
                    reflections, next_plan.covered_signals
                )
            current_plan = next_plan
            current_input = derived_input_for_replan(
                agent_input, next_plan.selected_tools
            )

        final_reflections = self._reflect(plan, all_observations)

        result = self.synthesizer.synthesize(
            agent_input=agent_input,
            plan=plan,
            tool_steps=all_steps,
            observations=all_observations,
            reflections=final_reflections,
        )
        result.intent_meta = intent_meta
        result.iterations = iterations_run
        result.replan_reason = replan_explanation

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
