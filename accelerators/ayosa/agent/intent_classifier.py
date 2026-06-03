"""Intent classification — keyword classifier with an optional LLM
upgrade.

The original `classify_intent(message)` is preserved byte-for-byte for
backwards compatibility. The new `classify_intent_smart(...)` is the
preferred entry point for agent code and adds:

  1. **Fast-path**: high-precision keyword matches (`current_time`,
     `service_stability_ranking`) bypass the LLM — unambiguous & cheap.
  2. **LLM route**: when `llm_config.enabled`, call `route_intent_llm`.
     Use its result only when the intent is in `CANONICAL_INTENTS`.
  3. **Fallback**: deterministic keyword classifier (existing behaviour).

Returns `(intent, metadata)` so callers (UI, telemetry, agent_stream
events) can surface which path produced the answer.
"""

from __future__ import annotations

from typing import Any, Optional

from accelerators.ayosa.service import _classify_intent_standalone
from accelerators.ayosa.agent.intent_router_llm import (
    CANONICAL_INTENTS,
    RouterResult,
    route_intent_llm,
)


# Keyword-classifier outputs we trust unconditionally — skip the LLM.
# These are very high-precision multi-word phrases (see service._INTENT_KEYWORDS).
_FAST_PATH_INTENTS: frozenset[str] = frozenset({
    "current_time",
    "service_stability_ranking",
})


def classify_intent(message: str) -> str:
    """Return the canonical intent name for the given user message.

    Pure deterministic keyword classifier. Identical behaviour to the
    existing service function. Kept for backwards compatibility.
    """
    return _classify_intent_standalone(message or "")


def classify_intent_smart(
    message: str,
    *,
    llm_config: Any = None,
    service_hint: Optional[str] = None,
) -> tuple[str, dict]:
    """Classify intent using fast-path → LLM → keyword fallback.

    Returns ``(intent, metadata)`` where metadata describes which path
    produced the result::

        {
            "source": "fast_path" | "llm" | "keyword" | "llm_fallback",
            "confidence": float,
            "llm_reasoning": str | None,
            "llm_provider": str | None,
            "llm_model": str | None,
            "service_hint": str | None,
            "time_hint": str | None,
        }

    `intent` is always a member of `CANONICAL_INTENTS`.
    """
    msg = (message or "").strip()
    base_intent = _classify_intent_standalone(msg)

    # 1. Fast-path
    if base_intent in _FAST_PATH_INTENTS:
        return base_intent, _meta(source="fast_path", confidence=1.0)

    # 2. LLM route (only when explicitly enabled)
    llm_enabled = _llm_enabled(llm_config)
    llm_result: RouterResult | None = None
    if llm_enabled:
        llm_result = route_intent_llm(msg, llm_config, service_hint=service_hint)

    if llm_result is not None:
        return llm_result.intent, _meta(
            source="llm",
            confidence=llm_result.confidence,
            llm_reasoning=llm_result.reasoning,
            llm_provider=llm_result.provider,
            llm_model=llm_result.model,
            service_hint=llm_result.service,
            time_hint=llm_result.time_range,
        )

    # 3. Keyword fallback
    source = "llm_fallback" if llm_enabled else "keyword"
    return base_intent, _meta(
        source=source,
        confidence=0.5 if source == "llm_fallback" else 0.7,
    )


def _llm_enabled(llm_config: Any) -> bool:
    if llm_config is None:
        return False
    if isinstance(llm_config, dict):
        return bool(llm_config.get("enabled"))
    return bool(getattr(llm_config, "enabled", False))


def _meta(
    *,
    source: str,
    confidence: float,
    llm_reasoning: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    service_hint: Optional[str] = None,
    time_hint: Optional[str] = None,
) -> dict:
    return {
        "source": source,
        "confidence": confidence,
        "llm_reasoning": llm_reasoning,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "service_hint": service_hint,
        "time_hint": time_hint,
    }


__all__ = [
    "CANONICAL_INTENTS",
    "classify_intent",
    "classify_intent_smart",
]
