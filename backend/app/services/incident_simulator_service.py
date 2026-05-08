from __future__ import annotations

import logging
from pathlib import Path

from observascore.cli import run_extraction
from observascore.insights.incident_simulator import (
    enrich_with_ai,
    simulate_incident,
    write_incident_simulation_outputs,
    IncidentRequestContext,
)

logger = logging.getLogger(__name__)


def run_incident_simulation(
    config_path: Path,
    output_dir: Path,
    application_name: str,
    environment: str,
    service_name: str,
    incident_type: str,
    incident_description: str,
    canonical_services: list[str],
    ai_config: dict | None,
) -> dict[str, str]:
    logger.info(
        "Running incident simulation: app=%s service=%s incident_type=%s",
        application_name,
        service_name,
        incident_type,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    estate = run_extraction(config_path, emit_console=False)
    logger.info("Extraction complete. Dashboards=%d alerts=%d signals=%d", len(estate.dashboards), len(estate.alert_rules), len(estate.signals))

    context = IncidentRequestContext(
        application_name=application_name,
        environment=environment,
        service_name=service_name,
        incident_type=incident_type,
        description=incident_description,
        canonical_services=canonical_services,
    )

    result = simulate_incident(estate, context)

    if ai_config and ai_config.get("enabled"):
        logger.info("Enriching with AI...")
        ai_summary, ai_recommendations = enrich_with_ai(result.to_dict(), ai_config)
        result.ai_summary = ai_summary
        result.ai_recommendations = ai_recommendations

    outputs = write_incident_simulation_outputs(result=result, output_dir=output_dir)

    return {
        "html": str(outputs["html"]),
        "json": str(outputs["json"]),
    }
