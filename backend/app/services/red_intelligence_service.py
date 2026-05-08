from __future__ import annotations

import logging
from pathlib import Path

from observascore.cli import run_extraction
from observascore.insights.red_panel_intelligence import (
    analyze_red_panel_intelligence,
    write_red_panel_intelligence_outputs,
)

logger = logging.getLogger(__name__)


def run_red_intelligence(
    config_path: Path,
    output_dir: Path,
    application_name: str,
    environment: str,
    canonical_services: list[str],
    auto_discover_services: bool,
) -> dict[str, str]:
    logger.info("Running RED Panel Intelligence for config: %s", config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    estate = run_extraction(config_path, emit_console=False)
    logger.info("Extraction complete. Dashboards discovered: %d", len(estate.dashboards))

    result = analyze_red_panel_intelligence(
        estate,
        application_name=application_name,
        environment=environment,
        canonical_services=canonical_services,
        auto_discover_services=auto_discover_services,
    )
    outputs = write_red_panel_intelligence_outputs(result=result, output_dir=output_dir)

    return {
        "html": str(outputs["html"]),
        "json": str(outputs["json"]),
    }
