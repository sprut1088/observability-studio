from fastapi import APIRouter

from accelerators.ayosa.models import AyosaChatRequest, AyosaChatResponse
from accelerators.ayosa.service import AyosaService

#router = APIRouter(prefix="/api/ayosa", tags=["AYOSA"])

router = APIRouter()

@router.post("/chat", response_model=AyosaChatResponse)
def chat(request: AyosaChatRequest):
    return AyosaService().investigate(request)