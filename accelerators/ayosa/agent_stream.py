"""Streaming variant of the AYOSA agent loop.

Emits progressive Server-Sent-Event payloads while the agent's
Plan → Act → Observe → Reflect → Respond loop runs. This module is the
*only* streaming layer for `agent_mode=True`; the legacy
`AyosaService.investigate_stream` is kept untouched for `agent_mode=False`
(backwards compatibility).

Event types (in nominal emission order):
    session_start   — handshake; carries session_id
    intent          — classified intent
    plan            — full Plan payload (no secrets)
    tool_start      — one per dispatched tool, before the adapter call
    tool_result     — one per dispatched tool, after the adapter returns
    observation     — one per finding produced
    chart           — placeholder; emitted only if the future chart
                       collection step yields any
    timeline_event  — one per observation that carries a timestamp
    llm_chunk       — streamed LLM tokens (only when AI is enabled)
    final_snapshot  — answer + snapshot + suggested actions
    done            — terminator; carries confidence + mode
    error           — fatal; ends the stream

Security: `serialize_sse_event` strips any secret-shaped keys (auth_token,
api_key, password, token, secret, bearer) at any nesting depth before
encoding. This is enforced by tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.intent_classifier import classify_intent
from accelerators.ayosa.agent_bridge import (
    _agent_result_to_chat_response,
    _to_agent_input,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# SSE serialization
# ──────────────────────────────────────────────────────────────────────── #
_SECRET_KEYS: frozenset[str] = frozenset({
    "auth_token",
    "api_key",
    "apikey",
    "password",
    "token",
    "secret",
    "bearer",
    "authorization",
})


def _strip_secrets(value: Any) -> Any:
    """Recursively redact secret-shaped keys; leave shape intact."""
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if k.lower() in _SECRET_KEYS else _strip_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    if isinstance(value, tuple):
        return [_strip_secrets(v) for v in value]
    return value


def serialize_sse_event(event_type: str, payload: dict[str, Any]) -> str:
    """Encode one SSE frame: `data: {...}\\n\\n` with secrets stripped.

    The wire format is one JSON object per frame containing both the
    event type and its payload — UI consumers parse the JSON rather than
    relying on SSE's `event:` field, which simplifies client code.
    """
    safe_payload = _strip_secrets(payload)
    body = {"type": event_type, **safe_payload}
    try:
        encoded = json.dumps(body, default=str, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning("SSE encode failed for %s: %s", event_type, exc)
        encoded = json.dumps({"type": "error", "message": f"serialize failed: {exc}"})
    return f"data: {encoded}\n\n"


# ──────────────────────────────────────────────────────────────────────── #
# Main streaming generator
# ──────────────────────────────────────────────────────────────────────── #
async def stream_agent_chat(
    request: Any,
    *,
    agent: AyosaAgent | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield event dicts in real time as the agent loop progresses.

    Each yielded value is a dict shaped `{type: str, ...}`. Callers
    wrap with `serialize_sse_event(ev.pop("type"), ev)` for SSE.
    """
    agent = agent or AyosaAgent()
    agent_input = _to_agent_input(request)
    session_id = getattr(request, "session_id", None) or agent_input.session_id

    try:
        # ── session_start ─────────────────────────────────────────── #
        yield {
            "type": "session_start",
            "session_id": session_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": agent_input.message,
            "service": agent_input.service,
            "time_range": agent_input.time_range,
        }

        # ── intent ────────────────────────────────────────────────── #
        intent = classify_intent(agent_input.message)
        yield {"type": "intent", "intent": intent}

        # ── plan ──────────────────────────────────────────────────── #
        plan = agent.planner.build(agent_input, intent)
        yield {"type": "plan", "plan": plan.model_dump()}

        # ── tool dispatch loop ────────────────────────────────────── #
        selected = set(plan.selected_tools)
        observations: list[Any] = []
        tool_steps: list[Any] = []

        for idx, tool in enumerate(agent_input.tools):
            tool_key = tool.tool.lower().strip()
            label = f"Querying {tool.tool}"

            if tool_key not in selected:
                tool_steps.append(
                    _step(idx, tool.tool, label, "skipped", None)
                )
                # Skipped tools don't get a tool_start; keep the stream tight.
                continue

            # tool_start
            yield {
                "type": "tool_start",
                "index": idx,
                "tool": tool.tool,
                "label": label,
            }

            try:
                tool_obs = await asyncio.to_thread(
                    agent.dispatcher._invoke_adapter, tool, agent_input, plan
                )
                observations.extend(tool_obs)
                tool_steps.append(_step(idx, tool.tool, label, "done", None))

                # tool_result (success)
                yield {
                    "type": "tool_result",
                    "index": idx,
                    "tool": tool.tool,
                    "status": "done",
                    "findings_count": len(tool_obs),
                }

                # one `observation` per finding + timeline_event for those with ts
                for obs in tool_obs:
                    yield {
                        "type": "observation",
                        "source": obs.source,
                        "signal": obs.signal,
                        "finding": obs.finding,
                        "query": obs.query,
                        "status": obs.status,
                    }
                    ts = _extract_timestamp(obs)
                    if ts:
                        yield {
                            "type": "timeline_event",
                            "timestamp": ts,
                            "source": obs.source,
                            "event": (obs.finding or "")[:200],
                            "severity": _extract_severity(obs),
                        }
            except Exception as exc:  # noqa: BLE001 — req #5: continue on tool failure
                logger.warning("Streaming tool %s failed: %s", tool.tool, exc)
                tool_steps.append(_step(idx, tool.tool, label, "error", str(exc)))
                # Synthetic error observation so the final snapshot is consistent
                from accelerators.ayosa.agent.schemas import Observation
                err_obs = Observation(
                    source=tool.tool,
                    signal="unknown",
                    finding=f"Adapter failed: {exc}",
                    status="error",
                )
                observations.append(err_obs)
                yield {
                    "type": "tool_result",
                    "index": idx,
                    "tool": tool.tool,
                    "status": "error",
                    "error": str(exc),
                }
                # then keep going with the next tool

        # ── reflect + synthesize (deterministic, no LLM yet) ──────── #
        from accelerators.ayosa.agent import reflect_on_observations

        reflections = reflect_on_observations(plan, observations)
        # Build a plain-text deterministic result without invoking LLM
        # (LLM goes through its own streaming branch below).
        det_input = agent_input.model_copy(deep=True)
        if det_input.llm:
            det_input.llm = det_input.llm.model_copy(update={"enabled": False})
        result = agent.synthesizer.synthesize(
            agent_input=det_input,
            plan=plan,
            tool_steps=tool_steps,
            observations=observations,
            reflections=reflections,
        )

        # ── llm_chunk stream (if AI enabled and we have evidence) ── #
        llm_payload: dict[str, Any] | None = None
        if (
            agent_input.llm
            and agent_input.llm.enabled
            and any(o.status == "ok" for o in observations)
        ):
            async for chunk_event in _stream_llm(agent_input.llm, result):
                if chunk_event["type"] == "llm_chunk":
                    yield chunk_event
                elif chunk_event["type"] == "llm_done":
                    llm_payload = chunk_event.get("data")
        if llm_payload is not None:
            result.llm_analysis = llm_payload
            result.llm_used = True
            summary = (llm_payload.get("executive_summary") or "").strip()
            if summary:
                result.final_response = summary

        # ── final_snapshot — full chat-response dict ──────────────── #
        chat_response = _agent_result_to_chat_response(result, request)
        yield {"type": "final_snapshot", "data": chat_response}

        # ── done ──────────────────────────────────────────────────── #
        yield {
            "type": "done",
            "mode": "agent",
            "confidence": result.confidence,
            "session_id": session_id,
        }
    except Exception as exc:  # noqa: BLE001 — never let the stream raise
        logger.error("Agent stream failed: %s", exc, exc_info=True)
        yield {"type": "error", "message": str(exc)}


# ──────────────────────────────────────────────────────────────────────── #
# FastAPI-friendly text wrapper
# ──────────────────────────────────────────────────────────────────────── #
async def stream_agent_chat_sse(
    request: Any,
    *,
    agent: AyosaAgent | None = None,
) -> AsyncGenerator[str, None]:
    """Wrap `stream_agent_chat` events as SSE text frames."""
    async for ev in stream_agent_chat(request, agent=agent):
        ev_type = ev.pop("type", "message")
        yield serialize_sse_event(ev_type, ev)


# ──────────────────────────────────────────────────────────────────────── #
# Internal helpers
# ──────────────────────────────────────────────────────────────────────── #
def _step(idx: int, tool: str, label: str, status: str, error: str | None):
    from accelerators.ayosa.agent.schemas import ToolStep

    return ToolStep(index=idx, tool=tool, label=label, status=status, error=error)


def _extract_timestamp(obs: Any) -> str | None:
    raw = obs.raw if isinstance(obs.raw, dict) else {}
    ts = raw.get("timestamp") or raw.get("time") or raw.get("@timestamp")
    return str(ts) if ts is not None else None


def _extract_severity(obs: Any) -> str | None:
    raw = obs.raw if isinstance(obs.raw, dict) else {}
    sev = raw.get("severity") or raw.get("level")
    return str(sev) if sev is not None else None


async def _stream_llm(
    llm_cfg: Any, result: Any
) -> AsyncGenerator[dict[str, Any], None]:
    """Bridge `AyosaAIAnalyst.analyze_focused_streaming` to async events."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_chunk(text: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "llm_chunk", "text": text}
        )

    def sync_work() -> None:
        try:
            from accelerators.ayosa.llm.analyst import AyosaAIAnalyst
            from accelerators.ayosa.agent.synthesizer import _build_llm_context

            analyst = AyosaAIAnalyst({
                "provider": llm_cfg.provider or "anthropic",
                "api_key": llm_cfg.api_key,
                "model": llm_cfg.model,
                "azure_endpoint": llm_cfg.azure_endpoint,
                "azure_deployment": llm_cfg.azure_deployment,
                "openrouter_model": llm_cfg.openrouter_model,
            })
            context = _build_llm_context(result)
            if hasattr(analyst, "analyze_focused_streaming"):
                data = analyst.analyze_focused_streaming(context, on_chunk)
            else:
                data = analyst.analyze_focused(context)
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "llm_done", "data": data}
            )
        except Exception as exc:  # noqa: BLE001 — LLM failure is non-fatal
            logger.warning("Agent stream LLM failed: %s", exc)
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "llm_done", "data": None}
            )

    thread = threading.Thread(target=sync_work, daemon=True)
    thread.start()

    while True:
        ev = await queue.get()
        yield ev
        if ev["type"] == "llm_done":
            break


__all__ = [
    "serialize_sse_event",
    "stream_agent_chat",
    "stream_agent_chat_sse",
]
