from fastapi import APIRouter

from accelerators.ayosa.models import (
    AyosaChatRequest,
    AyosaChatResponse,
)
from accelerators.ayosa.service import AyosaService

router = APIRouter()


@router.post("/chat", response_model=AyosaChatResponse)
def chat(request: AyosaChatRequest):
    return AyosaService().investigate(request)


@router.post("/runbook")
def generate_runbook(request: AyosaChatRequest):
    result = AyosaService().investigate(request)
    runbook = AyosaService().generate_runbook(result)

    return {
        "service": result.get("service"),
        "generated_runbook": runbook,
        "confidence": result.get("confidence"),
    }