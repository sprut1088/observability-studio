"""LLM-based tool selector — Step 7.

Replaces the deterministic ``select_tools_for_signals`` registry walk with
an LLM call that uses the JSON-Schema tool contracts introduced in
Step 5. The LLM is shown:

* the user message,
* the chosen intent (from the intent router),
* the service / time-range context,
* the list of configured tools (enum-constrained in the schema),
* the canonical required signals for the intent.

It returns a list of tool names plus a short reasoning string. The
caller validates each name against the configured set; anything else
forces a fall back to the deterministic planner — same safety contract
as ``intent_router_llm``.

Design rules:

* **Pure function** (``select_tools_llm``) — easy to mock in tests.
* **Strict allowlist** — emitted tool names are intersected with the
  configured tools; unknown names are dropped silently and a warning is
  logged.
* **Never raises** — any provider / parsing failure returns ``None`` so
  the deterministic planner takes over.
* **Cheap** — small system prompt, ~400 token response budget,
  temperature 0.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from accelerators.ayosa.agent.tool_registry import validate_tool_call

logger = logging.getLogger(__name__)


TOOL_SELECTOR_TOOL_NAME = "select_tools"
TOOL_SELECTOR_TOOL_DESCRIPTION = (
    "Choose the minimum set of configured observability tools needed to "
    "answer the user's question. For each chosen tool, emit a `tool_calls` "
    "entry containing the tool name (from the `enum` list) plus the "
    "arguments the dispatcher should use: optional `service`, optional "
    "`time_range` (e.g. '15m', '1h'), optional `query` (raw PromQL / LogQL "
    "/ SPL / TraceQL / KQL), and a required one-sentence `reason`. Use "
    "this tool exactly once."
)


def _build_input_schema(configured_names: list[str]) -> dict:
    """JSON-Schema constraining ``tool_calls[*].name`` to the configured set.

    Step 9: each call carries its own ``service`` / ``time_range`` / ``query``
    / ``reason`` so the LLM can target each adapter with its own arguments
    (e.g. one PromQL query for prometheus, one LogQL query for loki).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tool_calls": {
                "type": "array",
                "description": (
                    "Per-tool invocation list. Empty when no "
                    "observability lookup is required."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": configured_names,
                            "description": "Configured tool name.",
                        },
                        "service": {
                            "type": ["string", "null"],
                            "description": (
                                "Service to scope the query to. Null to "
                                "use the planner default."
                            ),
                        },
                        "time_range": {
                            "type": ["string", "null"],
                            "description": (
                                "Lookback window like '15m', '1h', '24h'. "
                                "Null = use planner default."
                            ),
                        },
                        "query": {
                            "type": ["string", "null"],
                            "description": (
                                "Raw query expression in the tool's native "
                                "language (PromQL, LogQL, SPL, TraceQL, KQL, "
                                "…). Null when no specific filter is needed."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "One-sentence justification for selecting "
                                "this tool with these arguments."
                            ),
                        },
                    },
                    "required": ["name", "reason"],
                },
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "One short sentence summarising the overall selection."
                ),
            },
        },
        "required": ["tool_calls", "reasoning"],
    }


def anthropic_tool_selector_tool(configured_names: list[str]) -> dict:
    return {
        "name": TOOL_SELECTOR_TOOL_NAME,
        "description": TOOL_SELECTOR_TOOL_DESCRIPTION,
        "input_schema": _build_input_schema(configured_names),
    }


def openai_tool_selector_tool(configured_names: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": TOOL_SELECTOR_TOOL_NAME,
            "description": TOOL_SELECTOR_TOOL_DESCRIPTION,
            "parameters": _build_input_schema(configured_names),
        },
    }


_SYSTEM_PROMPT = """You are an observability tool selector.
Choose the minimum set of configured tools needed to answer the question.
Prefer tools that cover the required signal types. Do not pick tools that
are not in the configured set. Return strict JSON via the tool call."""


@dataclass(frozen=True)
class SelectorResult:
    """Outcome of one LLM tool-selection call.

    ``tool_args`` (Step 9) maps each selected tool name to its validated
    argument dict (``service``, ``time_range``, ``query``, ``reason``).
    Tools that the LLM picked without per-tool args still appear in
    ``tool_names`` with an empty ``{}`` in ``tool_args``.
    """
    tool_names: list[str]
    reasoning: str
    provider: str
    model: str
    tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def select_tools_llm(
    *,
    message: str,
    intent: str,
    configured_tools: Iterable[str],
    required_signals: Iterable[str],
    llm_config: Any,
    service: Optional[str] = None,
    time_range: Optional[str] = None,
) -> Optional[SelectorResult]:
    """LLM-driven tool selection. Returns ``None`` on any failure path.

    The caller (Planner) must treat ``None`` as "use the deterministic
    selector instead".
    """
    if not message or not message.strip():
        return None
    if llm_config is None:
        return None

    configured = [
        (c or "").strip().lower() for c in configured_tools
        if (c or "").strip()
    ]
    if not configured:
        # Nothing to choose from — let the deterministic path handle the
        # "no tools" plan shape.
        return None

    cfg = _coerce_config(llm_config)
    if not cfg.get("enabled"):
        return None

    provider = (cfg.get("provider") or "anthropic").strip().lower()
    user_msg = _build_user_message(
        message=message,
        intent=intent,
        configured=configured,
        required_signals=list(required_signals),
        service=service,
        time_range=time_range,
    )

    try:
        if provider == "anthropic":
            raw = _call_anthropic(user_msg, cfg, configured)
        elif provider in ("azure", "azure_openai"):
            raw = _call_openai_compatible(user_msg, cfg, configured, azure=True)
        elif provider == "openrouter":
            raw = _call_openai_compatible(user_msg, cfg, configured, azure=False)
        else:
            logger.warning("select_tools_llm: unsupported provider %r", provider)
            return None
    except Exception as exc:  # noqa: BLE001 — must never propagate
        logger.warning("select_tools_llm: provider call failed: %s", exc)
        return None

    parsed = _parse_response(raw)
    if not parsed:
        return None

    allowed = set(configured)
    cleaned: list[str] = []
    dropped: list[str] = []
    tool_args: dict[str, dict[str, Any]] = {}

    raw_calls = parsed.get("tool_calls")
    if isinstance(raw_calls, list):
        # Step 9 rich path — each entry is a per-tool invocation dict.
        for entry in raw_calls:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            nl = name.strip().lower()
            if not nl or nl not in allowed:
                if nl:
                    dropped.append(nl)
                continue
            args = {
                k: entry.get(k)
                for k in ("service", "time_range", "query", "reason")
                if k in entry
            }
            errors = validate_tool_call(nl, args)
            if errors:
                logger.info(
                    "select_tools_llm: dropped invalid tool_call name=%s errors=%s",
                    nl, errors,
                )
                continue
            if nl in tool_args:
                # Duplicate — keep the first valid one, stable order.
                continue
            cleaned.append(nl)
            # Strip ``reason`` from args carried to the dispatcher; keep it
            # only on ``selection_meta`` and on the carried plan.tool_args so
            # adapters that accept ``plan`` can read it.
            tool_args[nl] = {
                "service": args.get("service"),
                "time_range": args.get("time_range"),
                "query": args.get("query"),
                "reason": str(args.get("reason") or "")[:280],
            }
    else:
        # Backwards-compat: legacy ``tool_names`` response (Step 7 shape).
        raw_names = parsed.get("tool_names") or []
        if not isinstance(raw_names, list):
            return None
        for n in raw_names:
            if not isinstance(n, str):
                continue
            nl = n.strip().lower()
            if nl in allowed and nl not in cleaned:
                cleaned.append(nl)
                tool_args[nl] = {}
            elif nl:
                dropped.append(nl)

    if dropped:
        logger.info(
            "select_tools_llm: dropped unknown tool names %r (configured=%r)",
            dropped, configured,
        )

    return SelectorResult(
        tool_names=cleaned,
        reasoning=str(parsed.get("reasoning") or "")[:300],
        provider=provider,
        model=str(cfg.get("model") or ""),
        tool_args=tool_args,
    )


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def _build_user_message(
    *,
    message: str,
    intent: str,
    configured: list[str],
    required_signals: list[str],
    service: Optional[str],
    time_range: Optional[str],
) -> str:
    return (
        f"User question: \"\"\"{message.strip()}\"\"\"\n\n"
        f"Classified intent: {intent or 'unknown'}\n"
        f"Service context: {service or 'none'}\n"
        f"Time range: {time_range or 'default'}\n"
        f"Required signal types: {required_signals or 'none'}\n"
        f"Configured tools (pick from these only): {configured}\n\n"
        "Pick the minimum tools needed. If no tools are needed, return an "
        "empty list."
    )


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #
def _call_anthropic(user_msg: str, cfg: dict, configured: list[str]) -> str:
    import anthropic  # type: ignore

    api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("missing Anthropic api_key")
    client = anthropic.Anthropic(api_key=api_key)
    model = cfg.get("model") or "claude-sonnet-4-6"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            tools=[anthropic_tool_selector_tool(configured)],
            tool_choice={"type": "tool", "name": TOOL_SELECTOR_TOOL_NAME},
            messages=[{"role": "user", "content": user_msg}],
        )
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if block_type != "tool_use":
                continue
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return json.dumps(inp)
            if isinstance(inp, str):
                return inp
    except TypeError:
        # SDK predates `tools=` — no safe fallback for tool selection;
        # return empty and let caller use deterministic planner.
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.info("select_tools_llm: anthropic tool-call failed (%s)", exc)
        return ""
    return ""


def _call_openai_compatible(
    user_msg: str, cfg: dict, configured: list[str], *, azure: bool,
) -> str:
    if azure:
        from openai import AzureOpenAI  # type: ignore
        api_key = cfg.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY", "")
        endpoint = (
            cfg.get("azure_endpoint")
            or cfg.get("api_base")
            or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        )
        if not api_key or not endpoint:
            raise RuntimeError("missing Azure OpenAI api_key / endpoint")
        client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=cfg.get("api_version", "2024-02-01"),
        )
        model = cfg.get("azure_deployment") or cfg.get("model") or "gpt-4o"
    else:
        from openai import OpenAI  # type: ignore
        api_key = cfg.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("missing OpenRouter api_key")
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        model = (
            cfg.get("openrouter_model")
            or cfg.get("model")
            or "anthropic/claude-3.5-sonnet"
        )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        tools=[openai_tool_selector_tool(configured)],
        tool_choice={
            "type": "function",
            "function": {"name": TOOL_SELECTOR_TOOL_NAME},
        },
        max_tokens=400,
        temperature=0,
    )
    choice = response.choices[0].message
    tool_calls = getattr(choice, "tool_calls", None) or []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        args = getattr(fn, "arguments", None) if fn is not None else None
        if isinstance(args, str) and args.strip():
            return args
    return choice.content or ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_response(raw_text: str) -> Optional[dict]:
    if not raw_text:
        return None
    try:
        v = json.loads(raw_text)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def _coerce_config(cfg: Any) -> dict:
    """Accept either a Pydantic model or a plain dict."""
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    # Pydantic v2
    dump = getattr(cfg, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001
            pass
    # Generic object with attributes
    out: dict[str, Any] = {}
    for k in (
        "enabled", "provider", "api_key", "model",
        "azure_endpoint", "azure_deployment", "openrouter_model",
        "api_version", "api_base",
    ):
        if hasattr(cfg, k):
            out[k] = getattr(cfg, k)
    return out


__all__ = [
    "TOOL_SELECTOR_TOOL_NAME",
    "TOOL_SELECTOR_TOOL_DESCRIPTION",
    "SelectorResult",
    "anthropic_tool_selector_tool",
    "openai_tool_selector_tool",
    "select_tools_llm",
]
