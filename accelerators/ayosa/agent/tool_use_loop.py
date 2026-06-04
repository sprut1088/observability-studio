"""Anthropic tool-use loop for AYOSA — Step 24.

This module is the first agentic primitive for AYOSA: instead of the
deterministic plan-then-execute pattern used by ``AyosaAgent.run``, the
``ToolUseLoop`` lets Claude decide which adapter to invoke next, runs it
via the existing :class:`ToolDispatcher`, feeds the result back, and
repeats until the model returns a final assistant message.

Design notes
------------
* **Additive.** Nothing here is imported by the legacy chat path,
  ``AyosaAgent.run``, ``agent_bridge`` or ``agent_stream``. Wiring
  happens in a follow-up step so this module can be reviewed and tested
  in isolation.
* **Adapter contract reused.** Each tool call is routed through
  :meth:`ToolDispatcher.dispatch` with a synthetic single-tool
  :class:`Plan`. The adapter receives the same kwargs it would in a
  normal investigation, including any LLM-supplied ``service`` /
  ``time_range`` / ``query`` overrides under ``plan.active_tool_args``.
* **Provider-scoped.** Only Anthropic's tool-use API is implemented
  here. OpenAI / Azure / OpenRouter equivalents can be slotted in later
  behind the same :class:`ToolUseLoop.run` surface — the dispatch and
  schema-building helpers are provider-agnostic.
* **Bounded.** ``max_iterations`` caps both runaway loops and runaway
  cost. The loop also bails on the first unrecoverable adapter error
  only after surfacing it back to the model as an ``is_error``
  tool_result so the LLM has a chance to recover (e.g. try a different
  tool).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentToolConfig,
    Observation,
    Plan,
    ToolStep,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# Static tool descriptions
#
# These descriptions are surfaced to the LLM as part of the Anthropic
# tool schema. They are intentionally terse — Claude reads dozens of
# tools in a single prompt and verbose descriptions degrade selection
# quality. Keep them under ~200 chars each.
# ──────────────────────────────────────────────────────────────────────── #
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "prometheus": (
        "Query Prometheus for metrics, alert rules and recording rules. "
        "Use for: latency, error rate, throughput, saturation, firing alerts."
    ),
    "grafana": (
        "Query Grafana for dashboards, panels, datasources and alert rules. "
        "Use for: existing curated views, alert provisioning, dashboard inventory."
    ),
    "alertmanager": (
        "Query Alertmanager for active and silenced alerts. "
        "Use for: which alerts are currently firing or silenced for a service."
    ),
    "loki": (
        "Query Loki for log lines and label cardinality. "
        "Use for: error log patterns, log volume spikes, log-derived metrics."
    ),
    "elasticsearch": (
        "Query Elasticsearch for indexed log documents. "
        "Use for: structured log search, term aggregations, time-bucketed counts."
    ),
    "opensearch": (
        "Query OpenSearch for indexed log documents. "
        "Use for: structured log search, term aggregations, time-bucketed counts."
    ),
    "splunk": (
        "Query Splunk for log events via SPL. "
        "Use for: log search, transaction reconstruction, audit trails."
    ),
    "jaeger": (
        "Query Jaeger for distributed traces and service dependencies. "
        "Use for: slow-trace investigation, span-level error attribution."
    ),
    "tempo": (
        "Query Tempo for distributed traces. "
        "Use for: trace lookup, service-graph aware latency investigation."
    ),
    "datadog": (
        "Query Datadog for metrics, logs, traces, monitors and SLOs. "
        "Use for: unified signal lookup when Datadog is the primary platform."
    ),
    "dynatrace": (
        "Query Dynatrace for metrics, problems, smartscape topology. "
        "Use for: APM problem feed, dependency graph, business-event impact."
    ),
    "appdynamics": (
        "Query AppDynamics for business transactions and health rules. "
        "Use for: BT-level latency / error / call-volume breakdown."
    ),
    "otelcollector": (
        "Query an OpenTelemetry Collector for pipeline health / receivers. "
        "Use for: confirming a collector is healthy and shipping signals."
    ),
}


# ──────────────────────────────────────────────────────────────────────── #
# Result dataclasses
# ──────────────────────────────────────────────────────────────────────── #
@dataclass
class ToolUseLoopResult:
    """Outcome of a single :meth:`ToolUseLoop.run` invocation.

    Shaped so it can be merged into :class:`AgentResult` later without
    losing any iteration / tool-call history.
    """

    final_response: str
    tool_steps: list[ToolStep] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    iterations_run: int = 0
    stop_reason: str = "end_turn"
    # Sequence of dicts: {"role", "content"} as fed to Anthropic.
    # Useful for debugging and for the future cost-tracker.
    messages: list[dict[str, Any]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────── #
# Pure helpers (testable in isolation)
# ──────────────────────────────────────────────────────────────────────── #
def _tool_name_to_anthropic(tool_name: str) -> str:
    """Map ``"prometheus"`` → ``"prometheus_investigate"``.

    Anthropic requires tool names match ``^[a-zA-Z0-9_-]{1,64}$``. We
    suffix every adapter with ``_investigate`` so the model sees a verb,
    not a noun.
    """
    safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in tool_name)
    return f"{safe.lower()}_investigate"


def _anthropic_to_tool_name(anthropic_name: str) -> str:
    """Reverse of :func:`_tool_name_to_anthropic` — strip the suffix."""
    suffix = "_investigate"
    if anthropic_name.endswith(suffix):
        return anthropic_name[: -len(suffix)]
    return anthropic_name


def build_anthropic_tool_schemas(
    tools: list[AgentToolConfig],
) -> list[dict[str, Any]]:
    """Build Anthropic tool schemas for every configured adapter.

    Deduplicates by tool *type* (lower-cased), so two Prometheus
    instances configured against different clusters still surface as one
    schema entry to the model — the dispatcher then routes to whichever
    instance matches the model's hints.
    """
    seen: set[str] = set()
    schemas: list[dict[str, Any]] = []

    for cfg in tools:
        key = (cfg.tool or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)

        description = _TOOL_DESCRIPTIONS.get(
            key,
            f"Query the {cfg.tool} observability tool for relevant signals.",
        )

        schemas.append(
            {
                "name": _tool_name_to_anthropic(key),
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": (
                                "The service / application name to investigate. "
                                "Required even if the user did not name one — "
                                "infer from context."
                            ),
                        },
                        "time_range": {
                            "type": "string",
                            "description": (
                                "Lookback window for the query, e.g. '15m', "
                                "'1h', '6h'. Default '30m' if unsure."
                            ),
                        },
                        "query_focus": {
                            "type": "string",
                            "description": (
                                "Concise hint about what to look for in this "
                                "specific call, e.g. 'p99 latency on /checkout', "
                                "'5xx errors by upstream'."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "One-line justification for why this tool is "
                                "the right next step. Recorded for the audit trail."
                            ),
                        },
                    },
                    "required": ["service"],
                },
            }
        )

    return schemas


def _build_synthetic_plan(
    agent_input: AgentInput,
    tool_name: str,
    tool_input: dict[str, Any],
) -> Plan:
    """Wrap one LLM tool call as a single-tool :class:`Plan`.

    This lets us reuse :meth:`ToolDispatcher.dispatch` unchanged so all
    the existing per-adapter argument handling (service/time_range/query
    overrides, ``plan.active_tool_args``, plan-aware adapter detection)
    keeps working without duplication.
    """
    key = tool_name.lower().strip()
    return Plan(
        intent="tool_use",
        service=tool_input.get("service") or agent_input.service,
        time_range=tool_input.get("time_range") or agent_input.time_range,
        selected_tools=[key],
        query_focus=tool_input.get("query_focus") or "",
        explanation=tool_input.get("reason") or "",
        tool_args={
            key: {
                "service": tool_input.get("service") or agent_input.service,
                "time_range": tool_input.get("time_range") or agent_input.time_range,
                "query": tool_input.get("query_focus"),
                "reason": tool_input.get("reason"),
            }
        },
        selection_meta={"mode": "tool_use_llm"},
    )


def _extract_text_blocks(content: Any) -> str:
    """Concatenate the ``text`` of every text block in an assistant message.

    Anthropic returns ``content`` as either a list of typed blocks or
    (for older SDK paths) a string. This helper handles both and ignores
    non-text blocks (e.g. ``tool_use``).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for block in content:
        # Support both attribute-style (SDK objects) and dict-style blocks.
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "text":
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text", "")
            if text:
                out.append(text)
    return "\n".join(out).strip()


def _extract_tool_use_blocks(content: Any) -> list[dict[str, Any]]:
    """Return ``[{id, name, input}, ...]`` for every tool_use block."""
    if not isinstance(content, list):
        return []
    calls: list[dict[str, Any]] = []
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "tool_use":
            continue
        calls.append(
            {
                "id": getattr(block, "id", None)
                or (block.get("id") if isinstance(block, dict) else None),
                "name": getattr(block, "name", None)
                or (block.get("name") if isinstance(block, dict) else None),
                "input": getattr(block, "input", None)
                or (block.get("input") if isinstance(block, dict) else {})
                or {},
            }
        )
    return calls


def _serialize_assistant_content(content: Any) -> list[dict[str, Any]]:
    """Convert an SDK content list back to the dict form Anthropic accepts on input.

    The conversation history we send back to Claude must use plain
    dicts; the SDK accepts both but the dict form is what we need for
    serialization (tests, persistence, audit trails).
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            out.append(block)
            continue
        block_type = getattr(block, "type", None)
        if block_type == "text":
            out.append({"type": "text", "text": getattr(block, "text", "")})
        elif block_type == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                }
            )
    return out


def _observations_to_tool_result(
    observations: list[Observation],
    error_message: str | None = None,
) -> str:
    """Render the dispatcher's :class:`Observation` list as a tool_result body.

    Anthropic wants ``content`` for a tool_result to be a string (or a
    list of blocks). We emit JSON so the model can reliably parse it on
    the next pass.
    """
    if error_message is not None:
        return json.dumps({"error": error_message})
    payload = [
        {
            "source": o.source,
            "signal": o.signal,
            "finding": o.finding,
            "status": o.status,
            "query": o.query,
        }
        for o in observations
    ]
    return json.dumps({"observations": payload}, default=str)


# ──────────────────────────────────────────────────────────────────────── #
# Tool-use loop
# ──────────────────────────────────────────────────────────────────────── #
_DEFAULT_SYSTEM_PROMPT = (
    "You are AYOSA, an expert Site Reliability Engineer running an "
    "interactive investigation. You have access to observability tools "
    "via the provided tool schemas. Decide which tool to call next "
    "based on the user's question and the evidence you have so far. "
    "Call tools one at a time, reason about each result before deciding "
    "the next call, and stop calling tools as soon as you have enough "
    "evidence to answer. When you stop, respond with a concise, "
    "evidence-grounded answer that names the specific tools and "
    "findings you used."
)


class ToolUseLoop:
    """Run an Anthropic tool-use conversation against AYOSA's adapters."""

    def __init__(
        self,
        *,
        client: Any,
        dispatcher: ToolDispatcher,
        model: str = "claude-sonnet-4-6",
        max_iterations: int = 6,
        max_tokens: int = 2048,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if client is None:
            raise ValueError("ToolUseLoop requires an Anthropic-compatible client.")
        if dispatcher is None:
            raise ValueError("ToolUseLoop requires a ToolDispatcher.")
        self.client = client
        self.dispatcher = dispatcher
        self.model = model
        # Hard floor of 1 mirrors AyosaAgent so callers can't accidentally
        # disable the loop by passing 0 or a negative value.
        self.max_iterations = max(1, int(max_iterations))
        self.max_tokens = int(max_tokens)
        self.system_prompt = system_prompt

    # ------------------------------------------------------------------ #
    def run(self, agent_input: AgentInput) -> ToolUseLoopResult:
        """Execute the tool-use loop until ``end_turn`` or the cap is hit."""
        schemas = build_anthropic_tool_schemas(agent_input.tools)
        tools_by_key: dict[str, AgentToolConfig] = {
            (t.tool or "").lower().strip(): t for t in agent_input.tools
        }

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": agent_input.message},
        ]
        all_steps: list[ToolStep] = []
        all_observations: list[Observation] = []
        final_text = ""
        stop_reason = "max_iterations"
        iterations_run = 0
        step_index = 0

        for iteration in range(self.max_iterations):
            iterations_run += 1
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=self.system_prompt,
                    tools=schemas,
                    messages=messages,
                )
            except Exception as exc:  # noqa: BLE001 — surface, don't crash
                logger.error("Anthropic tool-use call failed: %s", exc, exc_info=True)
                stop_reason = "error"
                final_text = f"LLM call failed: {exc}"
                break

            content = getattr(response, "content", None)
            resp_stop = getattr(response, "stop_reason", None) or "end_turn"

            # Always record the assistant message in the conversation
            # before we decide whether to dispatch tools.
            messages.append(
                {
                    "role": "assistant",
                    "content": _serialize_assistant_content(content),
                }
            )

            if resp_stop != "tool_use":
                # Final assistant message — collect text and stop.
                final_text = _extract_text_blocks(content)
                stop_reason = resp_stop
                break

            # ── Dispatch every tool_use block the model emitted ─────── #
            tool_calls = _extract_tool_use_blocks(content)
            if not tool_calls:
                # stop_reason said tool_use but no blocks present —
                # treat as terminal to avoid an infinite loop.
                final_text = _extract_text_blocks(content)
                stop_reason = "end_turn"
                break

            tool_results: list[dict[str, Any]] = []
            for call in tool_calls:
                api_name = call.get("name") or ""
                tool_key = _anthropic_to_tool_name(api_name)
                tool_input = call.get("input") or {}
                cfg = tools_by_key.get(tool_key)

                if cfg is None:
                    err = f"Tool '{tool_key}' is not configured for this run."
                    all_steps.append(
                        ToolStep(
                            index=step_index,
                            tool=tool_key,
                            label=f"Querying {tool_key}",
                            status="error",
                            error=err,
                            iteration=iteration,
                        )
                    )
                    step_index += 1
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.get("id") or "",
                            "is_error": True,
                            "content": _observations_to_tool_result([], err),
                        }
                    )
                    continue

                plan = _build_synthetic_plan(agent_input, tool_key, tool_input)
                # Build a single-tool AgentInput slice so dispatcher only
                # sees the relevant tool — keeps the synthetic plan's
                # ``selected_tools`` honest.
                single_input = agent_input.model_copy(update={"tools": [cfg]})
                steps, observations = self.dispatcher.dispatch(plan, single_input)

                # Re-stamp indices and iteration so the merged history
                # is consistent across the whole loop.
                for s in steps:
                    s.index = step_index
                    s.iteration = iteration
                    step_index += 1
                all_steps.extend(steps)
                all_observations.extend(observations)

                err_step = next((s for s in steps if s.status == "error"), None)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.get("id") or "",
                        "is_error": err_step is not None,
                        "content": _observations_to_tool_result(
                            observations,
                            err_step.error if err_step else None,
                        ),
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        return ToolUseLoopResult(
            final_response=final_text,
            tool_steps=all_steps,
            observations=all_observations,
            iterations_run=iterations_run,
            stop_reason=stop_reason,
            messages=messages,
        )


__all__ = [
    "ToolUseLoop",
    "ToolUseLoopResult",
    "build_anthropic_tool_schemas",
    "build_anthropic_client",
    "should_use_tool_use_loop",
    "synthesize_agent_result_from_loop",
]


# ──────────────────────────────────────────────────────────────────────── #
# Step 25 — wiring helpers
#
# These three helpers live here (rather than in agent_bridge or
# AyosaAgent) so the ToolUseLoop module owns every decision about
# *when* and *how* its loop is run. The agent/stream paths only need to
# call one of these to delegate cleanly.
# ──────────────────────────────────────────────────────────────────────── #
def should_use_tool_use_loop(agent_input: "AgentInput") -> bool:
    """Return True when the request is eligible for the tool-use loop.

    All four conditions must hold:
      * ``llm.enabled`` is True
      * ``llm.use_tool_use_loop`` is True
      * ``llm.provider`` is ``"anthropic"`` (or unset → defaults to anthropic)
      * ``llm.api_key`` is a non-empty string
      * At least one tool is configured (otherwise the loop has nothing to call)
    """
    llm = getattr(agent_input, "llm", None)
    if llm is None or not getattr(llm, "enabled", False):
        return False
    if not getattr(llm, "use_tool_use_loop", False):
        return False
    provider = (getattr(llm, "provider", None) or "anthropic").strip().lower()
    if provider != "anthropic":
        return False
    api_key = (getattr(llm, "api_key", None) or "").strip()
    if not api_key:
        return False
    tools = getattr(agent_input, "tools", None) or []
    return len(tools) > 0


def build_anthropic_client(agent_input: "AgentInput") -> Any | None:
    """Construct an ``anthropic.Anthropic`` client from the agent input.

    Returns ``None`` (and logs a warning) when the ``anthropic`` package
    is not installed or the API key is missing — callers fall back to
    the deterministic planner path.
    """
    llm = getattr(agent_input, "llm", None)
    if llm is None:
        return None
    api_key = (getattr(llm, "api_key", None) or "").strip()
    if not api_key:
        logger.warning("Tool-use loop requested but no Anthropic API key supplied.")
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        logger.warning(
            "Tool-use loop requested but the 'anthropic' package is not "
            "installed. Falling back to deterministic planner."
        )
        return None
    return anthropic.Anthropic(api_key=api_key)


def synthesize_agent_result_from_loop(
    *,
    loop_result: ToolUseLoopResult,
    agent_input: "AgentInput",
    intent: str,
    intent_meta: dict[str, Any] | None = None,
):
    """Convert a :class:`ToolUseLoopResult` into a full :class:`AgentResult`.

    Keeps the public ``AgentResult`` contract unchanged so all downstream
    consumers (synthesizer, chat-response mapper, persistence,
    ``loop_summary``, Compare panel) keep working without any branching
    on whether the run was tool-use or planner-driven.
    """
    # Imported lazily to avoid circular import with ``agent/__init__.py``.
    from accelerators.ayosa.agent.schemas import AgentResult, Plan

    selected = []
    for s in loop_result.tool_steps:
        key = (s.tool or "").lower().strip()
        if key and key not in selected:
            selected.append(key)

    plan = Plan(
        intent=intent or "tool_use",
        service=agent_input.service,
        time_range=agent_input.time_range,
        selected_tools=selected,
        explanation=(
            f"Tool-use loop selected {len(selected)} tool(s) over "
            f"{loop_result.iterations_run} LLM turn(s)."
        ),
        selection_meta={
            "mode": "tool_use_llm",
            "stop_reason": loop_result.stop_reason,
        },
    )

    result = AgentResult(
        intent=intent or "tool_use",
        plan=plan,
        tool_steps=list(loop_result.tool_steps),
        observations=list(loop_result.observations),
        reflections=[],  # tool-use loop self-terminates; no separate reflection phase
        final_response=loop_result.final_response,
        confidence=0.7 if loop_result.stop_reason == "end_turn" else 0.4,
        evidence=list(loop_result.observations),
        llm_used=True,
        llm_analysis={
            "mode": "tool_use_loop",
            "stop_reason": loop_result.stop_reason,
            "iterations_run": loop_result.iterations_run,
        },
    )
    result.intent_meta = intent_meta
    result.iterations = loop_result.iterations_run
    # The tool-use loop never re-plans in the deterministic sense, but
    # iterations > 1 means Claude made multiple decisions. Surface that
    # through the existing ``replan_reason`` field so the loop_summary
    # downstream still reads usefully.
    if loop_result.iterations_run > 1:
        result.replan_reason = (
            f"Tool-use loop: Claude made {loop_result.iterations_run} "
            f"decisions (stop_reason={loop_result.stop_reason})."
        )
    else:
        result.replan_reason = None
    return result
