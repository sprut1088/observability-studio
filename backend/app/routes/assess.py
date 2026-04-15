from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from backend.app.schemas import RunRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.assessor import run_assessment

router = APIRouter()

@router.post("/assess", response_model=RunResponse)
def assess(req: RunRequest):
    try:
        run_id = uuid4().hex
        base_dir = Path("runtime") / run_id
        config_path = build_runtime_config(req.model_dump(), base_dir)
        outputs = run_assessment(config_path, base_dir / "reports", req.ai.enabled if req.ai else False)
        return RunResponse(
            success=True,
            message="Assessment completed",
            download_url=outputs["html"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))