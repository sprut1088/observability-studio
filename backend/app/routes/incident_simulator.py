from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from backend.app.schemas import IncidentSimulatorRequest, RunResponse
from backend.app.services.config_builder import build_runtime_config
from backend.app.services.incident_simulator_service import run_incident_simulation

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


@router.post("/incident-simulator", response_model=RunResponse)
def incident_simulator(req: IncidentSimulatorRequest) -> RunResponse:
    try:
        run_id = uuid4().hex
        base_dir = RUNTIME_DIR / run_id
        
        app_name = req.incident.application_name
        environment = req.incident.environment
        
        config_payload = {
            "client": {"name": app_name, "environment": environment},
            "tools": [tool.model_dump() for tool in req.tools],
            "ai": (req.ai.model_dump() if req.ai else {"enabled": False}),
        }

        config_path = build_runtime_config(config_payload, base_dir)
        reports_dir = base_dir / "incident-simulator"

        ai_config = None
        if req.ai and req.ai.enabled:
            ai_config = req.ai.model_dump()

        outputs = run_incident_simulation(
            config_path=config_path,
            output_dir=reports_dir,
            application_name=req.incident.application_name,
            environment=req.incident.environment,
            service_name=req.incident.service_name,
            incident_type=req.incident.incident_type,
            incident_description=req.incident.description or "",
            canonical_services=req.incident.canonical_services,
            ai_config=ai_config,
        )

        html_path = Path(outputs["html"])
        preview_url, download_url = _build_runtime_urls(html_path)

        json_url = None
        json_path_str = outputs.get("json")
        if json_path_str:
            _, json_url = _build_runtime_urls(Path(json_path_str))

        return RunResponse(
            success=True,
            message="Incident simulation completed successfully",
            preview_url=preview_url,
            download_url=download_url,
            json_url=json_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Incident simulator route failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
