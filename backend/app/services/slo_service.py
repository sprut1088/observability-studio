from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SLO_SRC = ROOT / "accelerators" / "slo-studio" / "src"

if str(SLO_SRC) not in sys.path:
    sys.path.insert(0, str(SLO_SRC))

from slo_studio import SLOStudio  # type: ignore


async def run_slo_studio(request_data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request_data, dict):
        request_data = request_data.model_dump() if hasattr(request_data, "model_dump") else request_data.dict()

    runtime_id = uuid.uuid4().hex
    runtime_dir = ROOT / "runtime" / runtime_id
    runtime_dir.mkdir(parents=True, exist_ok=True)

    studio = SLOStudio(
        tools=request_data.get("tools", []),
        application=request_data.get("application"),
        service=request_data.get("service"),
        environment=request_data.get("environment"),
        primary_journey=request_data.get("primary_journey"),
        criticality=request_data.get("criticality", "balanced"),
        objective_style=request_data.get("objective_style", "balanced"),
        lookback_days=int(request_data.get("lookback_days") or 7),
        window_days=int(request_data.get("window_days") or 30),
        include_yaml=bool(request_data.get("include_yaml", True)),
        repo_path=request_data.get("repo_path"),
        repo_url=request_data.get("repo_url"),
        output_dir=runtime_dir,
    )

    result = studio.run()

    return {
        "success": True,
        "report_url": f"/api/preview/runtime/{runtime_id}/reports/slo-studio-report.html",
        "json_url": f"/api/preview/runtime/{runtime_id}/reports/slo-studio-report.json",
        "yaml_url": f"/api/preview/runtime/{runtime_id}/reports/sloth-slos.yaml",
        "summary": result.get("summary", {}),
    }