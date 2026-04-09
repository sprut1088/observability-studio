"""Report generator — produces HTML and JSON reports."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from observascore.engine import MaturityResult
from observascore.model import ObservabilityEstate

logger = logging.getLogger(__name__)


class ReportGenerator:
    def __init__(self, template_dir: Path | None = None):
        if template_dir is None:
            template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html"]),
        )

    def generate(
        self,
        estate: ObservabilityEstate,
        result: MaturityResult,
        output_dir: Path,
        config: dict[str, Any],
    ) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        # JSON report
        json_path = output_dir / "observascore-report.json"
        report_data = {
            "metadata": {
                "client": estate.client_name,
                "environment": estate.environment,
                "generated_at": estate.timestamp,
                "tool_version": "0.1.0",
            },
            "summary": {
                "prometheus_targets": estate.summary.prometheus_targets,
                "prometheus_targets_up": estate.summary.prometheus_targets_up,
                "prometheus_alert_rules": estate.summary.prometheus_alert_rules,
                "prometheus_recording_rules": estate.summary.prometheus_recording_rules,
                "prometheus_metrics_sampled": estate.summary.prometheus_metrics_sampled,
                "grafana_dashboards": estate.summary.grafana_dashboards,
                "grafana_folders": estate.summary.grafana_folders,
                "grafana_datasources": estate.summary.grafana_datasources,
                "loki_labels": estate.summary.loki_labels,
                "jaeger_services": estate.summary.jaeger_services,
                "extraction_errors": estate.summary.extraction_errors,
            },
            "result": result.to_dict(),
        }
        with open(json_path, "w") as f:
            json.dump(report_data, f, indent=2, default=str)
        logger.info("JSON report: %s", json_path)

        # HTML report
        html_path = output_dir / "observascore-report.html"
        template = self.env.get_template("report.html")

        findings_by_severity: dict[str, list] = {
            "critical": [], "high": [], "medium": [], "low": [], "info": []
        }
        for f in result.findings:
            findings_by_severity.setdefault(f.severity, []).append(f.to_dict())

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        backlog = sorted(
            [f.to_dict() for f in result.findings],
            key=lambda f: (severity_order.get(f["severity"], 99), -f["weight"]),
        )
        quick_wins = [b for b in backlog if b["severity"] in ("low", "medium")][:10]
        strategic = [b for b in backlog if b["severity"] in ("critical", "high")][:10]

        html = template.render(
            client_name=estate.client_name,
            environment=estate.environment,
            generated_at=estate.timestamp,
            title=config.get("report", {}).get("title", "Observability & SRE Maturity Assessment"),
            summary=report_data["summary"],
            result=result,
            findings_by_severity=findings_by_severity,
            backlog=backlog,
            quick_wins=quick_wins,
            strategic=strategic,
            dashboards=estate.dashboards[:50],
            alert_rules=estate.alert_rules[:50],
            datasources=estate.datasources,
        )
        with open(html_path, "w") as f:
            f.write(html)
        logger.info("HTML report: %s", html_path)

        return {"html": html_path, "json": json_path}
