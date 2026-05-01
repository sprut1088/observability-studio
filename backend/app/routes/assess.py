from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from backend.app.schemas import RunRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.assessor import run_assessment

router = APIRouter()

BASE_URL = "http://10.235.21.132:8000"
RUNTIME_DIR = Path("runtime")


def _build_runtime_urls(file_path: Path) -> tuple[str, str]:
    try:
        rel = file_path.resolve().relative_to(RUNTIME_DIR.resolve())
        rel_path = rel.as_posix()
        return (
            f"{BASE_URL}/api/preview/runtime/{rel_path}",
            f"{BASE_URL}/api/download/runtime/{rel_path}",
        )
    except ValueError:
        legacy_path = file_path.as_posix()
        return (
            f"{BASE_URL}/api/download/{legacy_path}",
            f"{BASE_URL}/api/download/{legacy_path}",
        )


@router.post("/assess", response_model=RunResponse)
def assess(req: RunRequest):
    try:
        run_id = uuid4().hex
        base_dir = RUNTIME_DIR / run_id
        config_path = build_runtime_config(req.model_dump(), base_dir)
        reports_dir = base_dir / "reports"
        outputs = run_assessment(config_path, reports_dir, req.ai.enabled if req.ai else False)

        html_path = Path(outputs["html"])
        preview_url, download_url = _build_runtime_urls(html_path)

        json_url = None
        json_path_str = outputs.get("json")
        if json_path_str:
            _, json_url = _build_runtime_urls(Path(json_path_str))

        return RunResponse(
            success=True,
            message="Assessment completed",
            preview_url=preview_url,
            download_url=download_url,
            json_url=json_url,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
