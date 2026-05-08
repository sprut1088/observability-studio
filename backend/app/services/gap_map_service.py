from __future__ import annotations

import logging
from pathlib import Path

from observascore.cli import run_extraction
from observascore.insights.observability_gap_map import (
    ApplicationContext,
    analyze_observability_gap_map,
    write_observability_gap_map_outputs,
)

logger = logging.getLogger(__name__)


def run_observability_gap_map(
    config_path: Path,
    output_dir: Path,
    application_context: ApplicationContext,
) -> dict[str, str]:
    logger.info("Running Observability Gap Map for config: %s", config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    estate = run_extraction(config_path, emit_console=False)
    logger.info(
        "Extraction complete. Signals=%d dashboards=%d alerts=%d",
        len(estate.signals),
        len(estate.dashboards),
        len(estate.alert_rules),
    )

    result = analyze_observability_gap_map(estate, application_context=application_context)
    outputs = write_observability_gap_map_outputs(result=result, output_dir=output_dir)

    return {
        "html": str(outputs["html"]),
        "json": str(outputs["json"]),
    }
