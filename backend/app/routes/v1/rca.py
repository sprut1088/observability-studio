from fastapi import APIRouter, HTTPException
from backend.app.models.rca import RCARequest, RCAResponse
from backend.app.services.rca_service import run_rca

router = APIRouter()


@router.post(
    "/rca",
    response_model=RCAResponse,
    summary="Run automated Root Cause Analysis",
    description=(
        "Collects signals from all provided observability tools (Prometheus, Grafana, "
        "Jaeger, OpenSearch), correlates anomalies, detects service cascade, and "
        "generates a Claude-powered RCA report. Returns a download_url for the HTML report."
    ),
)
async def run_rca_analysis(req: RCARequest) -> RCAResponse:
    if not req.tools:
        raise HTTPException(status_code=422, detail="At least one tool must be provided.")
    try:
        result = await run_rca(req)
        return RCAResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
