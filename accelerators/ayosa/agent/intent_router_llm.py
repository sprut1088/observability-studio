"""LLM-based intent router.

Replaces the brittle keyword classifier when an LLM is configured. Calls
the same providers supported by `AyosaAIAnalyst` (Anthropic / Azure
OpenAI / OpenRouter) with a tightly-constrained JSON output that maps
the user's message to one of the canonical AYOSA intents.

Design contract:
  - **Pure function** (`route_intent_llm`) — easy to mock in tests.
  - **Strict allowlist** — the returned intent is verified against
    `CANONICAL_INTENTS`. Anything else → returns ``None`` so the caller
    falls back to the keyword classifier.
  - **Never raises** — any provider / parsing failure returns ``None``.
  - **Cheap** — small system prompt, ~250 tokens response budget,
    temperature 0.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Canonical intents that the planner / signal-mapper already understands.
# Keep in sync with `_INTENT_SIGNALS` in accelerators/ayosa/service.py.
CANONICAL_INTENTS: tuple[str, ...] = (
    "current_time",
    "environment_health",
    "service_health",
    "healthy_services_list",
    "service_stability_ranking",
    "latency_issues",
    "latest_error",
    "error_investigation",
    "active_alerts",
    "trace_lookup",
    "dashboard_lookup",
    "general_observability_question",
)


_INTENT_GLOSSARY = """\
Canonical intents (pick EXACTLY one):
- current_time: user asks for the current server time or date.
- environment_health: overall platform/system health across all services.
- service_health: health of a specific named service.
- healthy_services_list: enumerate services that look healthy.
- service_stability_ranking: rank services by error rate / list services under a threshold (e.g. "less than 1% errors").
- latency_issues: latency, p95/p99, slow responses, throughput.
- latest_error: the single most recent error / failure / exception.
- error_investigation: investigate an error pattern (logs + metrics + alerts).
- active_alerts: list firing alerts / incidents.
- trace_lookup: look up traces or spans.
- dashboard_lookup: find a Grafana / observability dashboard.
- general_observability_question: anything else observability-related.
"""

_SYSTEM_PROMPT = """You are an intent router for an observability assistant.
Map the user's question to ONE canonical intent and return strict JSON.
Never invent intents. Never include prose outside the JSON object."""

_RESPONSE_SCHEMA = {
    "intent": "string: one of the canonical intent names",
    "confidence": "number: 0.0..1.0",
    "service": "string|null: service name if explicitly mentioned, else null",
    "time_range": "string|null: '15m'|'1h'|'24h'... if explicitly mentioned, else null",
    "reasoning": "string: one short sentence justifying the intent choice",
}

# Step 5: JSON-Schema contract used by native LLM tool-calling.
# Anthropic exposes it via the `tools=[{name,input_schema,...}]` parameter
# with `tool_choice={"type":"tool","name":"select_intent"}`. OpenAI /
# Azure / OpenRouter expose the same schema via
# `tools=[{type:"function",function:{...}}]` plus `tool_choice` forcing
# the function. The contract is identical — the LLM cannot return an
# intent outside `CANONICAL_INTENTS` because the schema enforces it.
INTENT_ROUTER_TOOL_NAME = "select_intent"
INTENT_ROUTER_TOOL_DESCRIPTION = (
    "Pick exactly one canonical AYOSA intent for the user's observability "
    "question. Use this tool once and only once."
)
INTENT_ROUTER_INPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": list(CANONICAL_INTENTS),
            "description": "The chosen canonical intent.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Confidence in the chosen intent (0..1).",
        },
        "service": {
            "type": ["string", "null"],
            "description": "Service name if explicitly mentioned, else null.",
        },
        "time_range": {
            "type": ["string", "null"],
            "description": "Lookback window '15m'|'1h'|'24h'… if mentioned, else null.",
        },
        "reasoning": {
            "type": "string",
            "description": "One short sentence justifying the intent choice.",
        },
    },
    "required": ["intent", "confidence", "reasoning"],
}


def anthropic_intent_router_tool() -> dict:
    """Return the Anthropic Messages-API `tools[*]` entry for intent routing."""
    return {
        "name": INTENT_ROUTER_TOOL_NAME,
        "description": INTENT_ROUTER_TOOL_DESCRIPTION,
        "input_schema": INTENT_ROUTER_INPUT_SCHEMA,
    }


def openai_intent_router_tool() -> dict:
    """Return the OpenAI-compatible function-calling spec for intent routing."""
    return {
        "type": "function",
        "function": {
            "name": INTENT_ROUTER_TOOL_NAME,
            "description": INTENT_ROUTER_TOOL_DESCRIPTION,
            "parameters": INTENT_ROUTER_INPUT_SCHEMA,
        },
    }


@dataclass(frozen=True)
class RouterResult:
    """Outcome of one LLM intent-routing call."""
    intent: str
    confidence: float
    service: Optional[str]
    time_range: Optional[str]
    reasoning: str
    provider: str
    model: str


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def route_intent_llm(
    message: str,
    llm_config: Any,
    *,
    service_hint: Optional[str] = None,
) -> Optional[RouterResult]:
    """Classify `message` using the configured LLM.

    Returns ``None`` if the LLM is disabled, misconfigured, the call
    fails, or the returned intent is not in `CANONICAL_INTENTS`. The
    caller MUST fall back to the keyword classifier in that case.
    """
    if not message or not message.strip():
        return None
    if llm_config is None:
        return None

    cfg = _coerce_config(llm_config)
    if not cfg.get("enabled"):
        return None

    provider = (cfg.get("provider") or "anthropic").strip().lower()

    try:
        if provider == "anthropic":
            raw = _call_anthropic(message, cfg, service_hint)
        elif provider in ("azure", "azure_openai"):
            raw = _call_openai_compatible(message, cfg, service_hint, azure=True)
        elif provider == "openrouter":
            raw = _call_openai_compatible(message, cfg, service_hint, azure=False)
        else:
            logger.warning("intent_router_llm: unsupported provider %r", provider)
            return None
    except Exception as exc:  # noqa: BLE001 — must never propagate
        logger.warning("intent_router_llm: provider call failed: %s", exc)
        return None

    parsed = _parse_response(raw)
    if not parsed:
        return None

    intent = (parsed.get("intent") or "").strip()
    if intent not in CANONICAL_INTENTS:
        logger.info(
            "intent_router_llm: LLM returned non-canonical intent %r — falling back",
            intent,
        )
        return None

    confidence = _coerce_confidence(parsed.get("confidence"))
    return RouterResult(
        intent=intent,
        confidence=confidence,
        service=_coerce_optional_str(parsed.get("service")) or service_hint,
        time_range=_coerce_optional_str(parsed.get("time_range")),
        reasoning=str(parsed.get("reasoning") or "")[:300],
        provider=provider,
        model=str(cfg.get("model") or ""),
    )


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #
def _build_user_message(message: str, service_hint: Optional[str]) -> str:
    return f"""{_INTENT_GLOSSARY}

User message:
\"\"\"{message.strip()}\"\"\"

Service context hint (may be empty): {service_hint or "none"}

Respond ONLY with a JSON object matching this schema (no markdown, no prose):
{json.dumps(_RESPONSE_SCHEMA, indent=2)}
"""


def _call_anthropic(message: str, cfg: dict, service_hint: Optional[str]) -> str:
    import anthropic  # type: ignore

    api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("missing Anthropic api_key")
    client = anthropic.Anthropic(api_key=api_key)
    model = cfg.get("model") or "claude-sonnet-4-6"

    # Step 5: prefer native tool-calling — the schema enum forces a valid
    # intent. Fall back to JSON-prompt mode if the installed SDK is older.
    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            tools=[anthropic_intent_router_tool()],
            tool_choice={"type": "tool", "name": INTENT_ROUTER_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": _build_user_message(message, service_hint),
                }
            ],
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
        # No tool_use block — fall through to text path below.
    except TypeError:
        # SDK predates `tools=` kwarg — use legacy JSON-prompt path.
        pass
    except Exception as exc:  # noqa: BLE001 — single retry via prompt mode
        logger.info("intent_router_llm: tool-calling failed (%s); using prompt mode", exc)

    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(message, service_hint)}],
    )
    try:
        return response.content[0].text
    except Exception:
        return str(response)


def _call_openai_compatible(
    message: str, cfg: dict, service_hint: Optional[str], *, azure: bool
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
        model = cfg.get("openrouter_model") or cfg.get("model") or "anthropic/claude-3.5-sonnet"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(message, service_hint)},
        ],
        tools=[openai_intent_router_tool()],
        tool_choice={
            "type": "function",
            "function": {"name": INTENT_ROUTER_TOOL_NAME},
        },
        max_tokens=300,
        temperature=0,
    )
    choice = response.choices[0].message
    tool_calls = getattr(choice, "tool_calls", None) or []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        args = getattr(fn, "arguments", None) if fn is not None else None
        if isinstance(args, str) and args.strip():
            return args
    # Tool-calling didn't yield a structured call — fall back to message text.
    return choice.content or ""


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw_text: str) -> Optional[dict]:
    if not raw_text:
        return None
    text = raw_text.strip()
    # Strip markdown code fences if the model added them.
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines) - 1 if lines and lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()
    # Try direct parse, then fall back to first JSON object substring.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _coerce_config(llm_config: Any) -> dict:
    if isinstance(llm_config, dict):
        return llm_config
    out: dict = {}
    for key in (
        "enabled", "provider", "api_key", "model",
        "azure_endpoint", "azure_deployment", "openrouter_model",
    ):
        if hasattr(llm_config, key):
            out[key] = getattr(llm_config, key)
    return out


def _coerce_confidence(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "n/a"):
        return None
    return s


__all__ = [
    "CANONICAL_INTENTS",
    "INTENT_ROUTER_INPUT_SCHEMA",
    "INTENT_ROUTER_TOOL_DESCRIPTION",
    "INTENT_ROUTER_TOOL_NAME",
    "RouterResult",
    "anthropic_intent_router_tool",
    "openai_intent_router_tool",
    "route_intent_llm",
]
