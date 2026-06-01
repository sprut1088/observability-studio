"""ObsCo (Observability Copilot) chat route.

POST /api/v1/obsco/chat — answer questions about configured observability tools.
Thin handler: delegates all work to `accelerators.obsco.service.answer_question`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from accelerators.obsco.service import answer_question
from backend.app.models.obsco import ObsCoChatRequest, ObsCoChatResponse

router = APIRouter(prefix="/obsco", tags=["ObsCo"])


@router.post(
    "/chat",
    response_model=ObsCoChatResponse,
    summary="Ask ObsCo a question about your observability tools",
    description=(
        "ObsCo is a lightweight Q&A copilot. It detects which tools your "
        "question is about, intersects them with the tools you have "
        "configured, and returns grounded facts (purpose, common endpoints, "
        "queries, troubleshooting tips, docs). If an Anthropic API key is "
        "supplied, the answer is enhanced by Claude grounded in the same facts."
    ),
)
async def chat(req: ObsCoChatRequest) -> ObsCoChatResponse:
    if not (req.message or "").strip():
        raise HTTPException(status_code=422, detail="Message is required.")
    try:
        result = await answer_question(req)
        return ObsCoChatResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
