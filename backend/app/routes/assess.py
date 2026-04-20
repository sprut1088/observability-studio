from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from backend.app.schemas import RunRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.assessor import run_assessment

router = APIRouter()

BASE_URL = "http://20.193.248.157:8000"
RUNTIME_DIR = Path("runtime")


@router.post("/assess", response_model=RunResponse)
def assess(req: RunRequest):
    try:
        run_id = uuid4().hex
        base_dir = RUNTIME_DIR / run_id
        config_path = build_runtime_config(req.model_dump(), base_dir)
        reports_dir = base_dir / "reports"
        outputs = run_assessment(config_path, reports_dir, req.ai.enabled if req.ai else False)

        html_path = Path(outputs["html"])

        # Build a path relative to RUNTIME_DIR so the download handler resolves it
        # correctly: GET /api/download/runtime/<rel_path>
        try:
            rel = html_path.resolve().relative_to(RUNTIME_DIR.resolve())
            download_url = f"{BASE_URL}/api/download/runtime/{rel.as_posix()}"
        except ValueError:
            # Fallback: use the absolute path segment after "runtime/"
            download_url = f"{BASE_URL}/api/download/{html_path.as_posix()}"

        return RunResponse(
            success=True,
            message="Assessment completed",
            download_url=download_url,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
