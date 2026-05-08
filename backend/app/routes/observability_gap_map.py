from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from backend.app.schemas import ObservabilityGapMapRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.gap_map_service import run_observability_gap_map
from observascore.insights.observability_gap_map import ApplicationContext

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


@router.post("/observability-gap-map", response_model=RunResponse)
def observability_gap_map(req: ObservabilityGapMapRequest) -> RunResponse:
    try:
        run_id = uuid4().hex
        base_dir = RUNTIME_DIR / run_id
        config_path = build_runtime_config(req.model_dump(), base_dir)
        reports_dir = base_dir / "observability-gap-map"

        app_name = req.client.name
        app_env = req.client.environment
        app_services: list[str] = []
        include_auto_discovered = False

        if req.application is not None:
            app_name = req.application.name or app_name
            app_env = req.application.environment or app_env
            app_services = req.application.services or []
            include_auto_discovered = req.application.include_auto_discovered

        application_context = ApplicationContext(
            name=app_name,
            environment=app_env,
            services=app_services,
            include_auto_discovered=include_auto_discovered,
        )

        outputs = run_observability_gap_map(
            config_path=config_path,
            output_dir=reports_dir,
            application_context=application_context,
        )

        html_path = Path(outputs["html"])
        preview_url, download_url = _build_runtime_urls(html_path)

        json_url = None
        json_path_str = outputs.get("json")
        if json_path_str:
            _, json_url = _build_runtime_urls(Path(json_path_str))

        return RunResponse(
            success=True,
            message="Observability Gap Map generated successfully",
            preview_url=preview_url,
            download_url=download_url,
            json_url=json_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Observability Gap Map route failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
