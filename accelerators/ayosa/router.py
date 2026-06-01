from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from accelerators.ayosa.models import (
    AyosaChatRequest,
    AyosaChatResponse,
)
from accelerators.ayosa.service import AyosaService
from accelerators.ayosa.agent_bridge import run_agent_chat
from accelerators.ayosa.agent.session_store import get_default_store
from accelerators.ayosa.agent_stream import (
    serialize_sse_event,
    stream_agent_chat,
)

router = APIRouter()


@router.post("/chat", response_model=AyosaChatResponse)
def chat(request: AyosaChatRequest):
    if request.agent_mode:
        return run_agent_chat(request)
    result = AyosaService().investigate(request)
    # Ensure the deterministic path always advertises its mode and
    # echoes back a session_id so clients can chain follow-ups.
    if isinstance(result, dict):
        result.setdefault("mode", "deterministic")
        result.setdefault("tool_steps", [])
        result.setdefault("observations", result.get("evidence", []))
        sid = (request.session_id or "").strip() or get_default_store().new_session_id()
        result["session_id"] = sid
    return result


@router.post("/chat/stream")
async def chat_stream(request: AyosaChatRequest):
    """SSE endpoint — emits step/llm_chunk/result events so the UI can show
    live tool-query progress and streaming LLM text while the answer is built.

    When `agent_mode=True`, streams the new agent event vocabulary
    (session_start, intent, plan, tool_start, tool_result, observation,
    chart, timeline_event, llm_chunk, final_snapshot, done, error).
    Otherwise streams the legacy AyosaService events (plan/step/llm_chunk/
    result/error) — fully backwards-compatible.
    """

    async def _generate_agent():
        try:
            async for ev in stream_agent_chat(request):
                ev_type = ev.pop("type", "message")
                yield serialize_sse_event(ev_type, ev)
        except Exception as exc:  # noqa: BLE001
            yield serialize_sse_event("error", {"message": str(exc)})

    async def _generate_legacy():
        try:
            async for event in AyosaService().investigate_stream(request):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    generator = _generate_agent() if request.agent_mode else _generate_legacy()
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/runbook")
def generate_runbook(request: AyosaChatRequest):
    result = AyosaService().investigate(request)
    runbook = AyosaService().generate_runbook(result)

    return {
        "service": result.get("service"),
        "generated_runbook": runbook,
        "confidence": result.get("confidence"),
    }