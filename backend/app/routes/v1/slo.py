from fastapi import APIRouter

from backend.app.models.slo import SLOStudioRequest, SLOStudioResponse
from backend.app.services.slo_service import run_slo_studio

router = APIRouter()


@router.post("/slo-studio", response_model=SLOStudioResponse)
async def run_slo_studio_analysis(req: SLOStudioRequest) -> SLOStudioResponse:
    try:
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        result = await run_slo_studio(payload)
        return SLOStudioResponse(**result)
    except Exception as exc:
        return SLOStudioResponse(success=False, error=str(exc))