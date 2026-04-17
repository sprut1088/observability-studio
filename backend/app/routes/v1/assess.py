from fastapi import APIRouter, HTTPException
from backend.app.models.assessment import AssessmentRequest, AssessmentResponse
from backend.app.services.scoring_service import run_scoring

router = APIRouter()


@router.post(
    "/assess",
    response_model=AssessmentResponse,
    summary="Run an observability maturity assessment",
    description=(
        "Executes the full scoring pipeline for a single tool source. "
        "When use_ai=false the deterministic rules engine is used; when "
        "use_ai=true the output is enriched by an LLM gap-analysis pass. "
        "Returns a download_url pointing to the generated HTML report."
    ),
)
async def run_assessment(req: AssessmentRequest) -> AssessmentResponse:
    try:
        return await run_scoring(req)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
