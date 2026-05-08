from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from backend.app.schemas import RedIntelligenceRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.red_intelligence_service import run_red_intelligence

logger = logging.getLogger(__name__)
router = APIRouter()

BASE_URL = "http://10.235.21.132:8001"
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


@router.post("/red-intelligence", response_model=RunResponse)
def red_intelligence(req: RedIntelligenceRequest) -> RunResponse:
    try:
        run_id = uuid4().hex
        base_dir = RUNTIME_DIR / run_id
        app_name = req.application_name or (req.client.name if req.client else "RED Intelligence Hub")
        environment = req.environment or (req.client.environment if req.client else "hub")
        config_payload = {
            "client": {"name": app_name, "environment": environment},
            "tools": [tool.model_dump() for tool in req.tools],
            "ai": (req.ai.model_dump() if req.ai else {"enabled": False}),
        }

        config_path = build_runtime_config(config_payload, base_dir)
        reports_dir = base_dir / "red-intelligence"

        outputs = run_red_intelligence(
            config_path=config_path,
            output_dir=reports_dir,
            application_name=app_name,
            environment=environment,
            canonical_services=req.canonical_services,
            auto_discover_services=req.auto_discover_services,
        )

        html_path = Path(outputs["html"])
        preview_url, download_url = _build_runtime_urls(html_path)

        json_url = None
        json_path_str = outputs.get("json")
        if json_path_str:
            _, json_url = _build_runtime_urls(Path(json_path_str))

        return RunResponse(
            success=True,
            message="RED Panel Intelligence completed",
            preview_url=preview_url,
            download_url=download_url,
            json_url=json_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RED Panel Intelligence route failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
