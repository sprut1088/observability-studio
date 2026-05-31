from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from accelerators.ayosa.models import (
    AyosaChatRequest,
    AyosaChatResponse,
)
from accelerators.ayosa.service import AyosaService

router = APIRouter()


@router.post("/chat", response_model=AyosaChatResponse)
def chat(request: AyosaChatRequest):
    return AyosaService().investigate(request)


@router.post("/chat/stream")
async def chat_stream(request: AyosaChatRequest):
    """SSE endpoint — emits step/llm_chunk/result events so the UI can show
    live tool-query progress and streaming LLM text while the answer is built."""

    async def _generate():
        try:
            async for event in AyosaService().investigate_stream(request):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        _generate(),
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