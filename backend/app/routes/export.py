from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from backend.app.schemas import RunRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.exporter import run_export

router = APIRouter()

@router.post("/export", response_model=RunResponse)
def export_excel(req: RunRequest):
    try:
        run_id = uuid4().hex
        base_dir = Path("runtime") / run_id
        config_path = build_runtime_config(req.model_dump(), base_dir)
        file_path = run_export(config_path, base_dir / "exports")

        return RunResponse(
            success=True,
            message="Excel export completed",
            download_url=f"http://10.235.21.132:8000/api/download/{file_path.as_posix()}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
