from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from prometheus_client import PrometheusClient
from recommender import detect_existing_slos, recommend_slos, build_findings
from sloth_generator import generate_sloth_yaml
from report_writer import write_reports


class SLOStudio:
    def __init__(
        self,
        tools: list[dict[str, Any]],
        service: str | None,
        environment: str | None,
        objective: float,
        window_days: int,
        output_dir: Path,
    ):
        self.tools = tools
        self.service = service
        self.environment = environment
        self.objective = objective
        self.window_days = window_days
        self.output_dir = output_dir

    def run(self) -> dict[str, Any]:
        prometheus = self._get_prometheus_client()

        rules = prometheus.rules() if prometheus else []
        alerts = prometheus.alerts() if prometheus else []
        services = self._discover_services(prometheus)

        if self.service:
            services = [svc for svc in services if svc == self.service]

        existing_slos = detect_existing_slos(rules, alerts)
        recommendations = recommend_slos(services, existing_slos, self.objective)
        findings = build_findings(services, existing_slos, recommendations)
        sloth_yaml = generate_sloth_yaml(recommendations)

        context = {
            "services": services,
            "existing_slos": existing_slos,
            "recommendations": [asdict(item) for item in recommendations],
            "findings": [asdict(item) for item in findings],
            "sloth_yaml": sloth_yaml,
            "summary": {
                "service_count": len(services),
                "existing_slo_count": len(existing_slos),
                "recommended_slo_count": len(recommendations),
                "finding_count": len(findings),
            },
        }

        write_reports(self.output_dir, context, sloth_yaml)

        return context

    def _get_prometheus_client(self) -> PrometheusClient | None:
        for tool in self.tools:
            name = (tool.get("name") or tool.get("tool") or "").lower()
            url = tool.get("url") or ""

            if "prometheus" in name and url:
                return PrometheusClient(url=url)

        return None

    def _discover_services(self, prometheus: PrometheusClient | None) -> list[str]:
        if prometheus is None:
            return []

        candidates: set[str] = set()

        for label in ["service", "service_name", "job", "app", "application"]:
            try:
                for value in prometheus.label_values(label):
                    if value and not value.startswith("prometheus"):
                        candidates.add(value)
            except Exception:
                continue

        return sorted(candidates)