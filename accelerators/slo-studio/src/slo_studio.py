from __future__ import annotations

from pathlib import Path
from typing import Any

from coverage_matrix import build_slo_coverage_matrix
from existing_slo_detector import detect_existing_slos
from jaeger_client import JaegerClient
from models import ServiceProfile, SignalEvidence
from multi_source_evidence import build_tool_inventory, collect_multi_source_evidence
from prometheus_client import PrometheusClient
from recommendation_ranker import split_recommendations
from repo_profiler import profile_repo
from report_writer import write_reports
from service_behavior import (
    analyze_service_behavior,
    canonical_service_name,
    discover_services_from_prometheus,
    is_application_service,
)
from sloth_generator import generate_sloth_yaml
from slo_intelligence import recommend_slos


class SLOStudio:
    def __init__(
        self,
        tools: list[dict[str, Any]],
        application: str | None,
        service: str | None,
        environment: str | None,
        primary_journey: str | None,
        criticality: str,
        objective_style: str,
        lookback_days: int,
        window_days: int,
        include_yaml: bool,
        repo_path: str | None,
        repo_url: str | None,
        output_dir: Path,
    ):
        self.tools = tools or []
        self.application = application
        self.service = canonical_service_name(service) if service else None
        self.environment = environment
        self.primary_journey = primary_journey
        self.criticality = criticality
        self.objective_style = objective_style
        self.lookback_days = int(lookback_days or 30)
        self.window_days = int(window_days or 30)
        self.include_yaml = include_yaml
        self.repo_path = repo_path
        self.repo_url = repo_url
        self.output_dir = output_dir

    def run(self) -> dict[str, Any]:
        prom = self._get_prometheus_client()
        jaeger = self._get_jaeger_client()
        tool_inventory = build_tool_inventory(self.tools)

        rules: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        services: list[str] = []
        collection_errors: list[dict[str, str]] = []

        if prom:
            try:
                rules = prom.rules()
            except Exception as exc:
                collection_errors.append({"source": "prometheus", "operation": "rules", "error": str(exc)})

            try:
                alerts = prom.alerts()
            except Exception as exc:
                collection_errors.append({"source": "prometheus", "operation": "alerts", "error": str(exc)})

            try:
                services.extend(discover_services_from_prometheus(prom, self.lookback_days))
            except Exception as exc:
                collection_errors.append({"source": "prometheus", "operation": "service discovery", "error": str(exc)})

        if jaeger:
            try:
                services.extend(jaeger.services())
            except Exception as exc:
                collection_errors.append({"source": "jaeger", "operation": "service discovery", "error": str(exc)})

        services = self._normalize_services(services)

        if self.service:
            if self.service in services:
                services = [self.service]
            else:
                services = [self.service]

        existing_slos = detect_existing_slos(rules, alerts)
        repo_profile = profile_repo(self.repo_path)
        profiles: list[ServiceProfile] = []

        for svc in services:
            profile = ServiceProfile(name=svc, criticality=self._service_criticality(svc))

            if self.primary_journey:
                profile.user_journeys.append(self.primary_journey)

            self._apply_repo_profile(profile, repo_profile)

            if prom:
                try:
                    profile.evidence.extend(analyze_service_behavior(prom, svc, self.lookback_days))
                except Exception as exc:
                    collection_errors.append({"source": "prometheus", "operation": f"behavior analysis for {svc}", "error": str(exc)})

            try:
                profile.evidence.extend(collect_multi_source_evidence(self.tools, svc, self.lookback_days))
            except Exception as exc:
                collection_errors.append({"source": "multi_source", "operation": f"evidence collection for {svc}", "error": str(exc)})

            self._hydrate_profile_from_evidence(profile)
            profile.user_journeys.extend(self._infer_journeys(profile))
            profile.user_journeys = sorted(set(profile.user_journeys))

            profiles.append(profile)

        recommendations, findings = recommend_slos(
            profiles=profiles,
            existing_slos=existing_slos,
            objective_style=self.objective_style,
            window_days=self.window_days,
        )

        top_recommendations, production_ready_recommendations, candidate_recommendations = split_recommendations(
            recommendations,
            production_confidence_threshold=0.70,
        )

        slo_coverage_matrix = build_slo_coverage_matrix(
            profiles=profiles,
            existing_slos=existing_slos,
            recommendations=production_ready_recommendations + candidate_recommendations,
        )

        sloth_yaml = generate_sloth_yaml(production_ready_recommendations) if self.include_yaml else ""

        summary = {
            "application": self.application,
            "environment": self.environment,
            "service_count": len(profiles),
            "existing_slo_count": len(existing_slos),
            "recommended_slo_count": len(recommendations),
            "top_recommendation_count": len(top_recommendations),
            "production_ready_slo_count": len(production_ready_recommendations),
            "candidate_slo_count": len(candidate_recommendations),
            "finding_count": len(findings),
            "coverage_gap_count": len([row for row in slo_coverage_matrix if row.get("status") in {"recommended", "missing_telemetry"}]),
            "evidence_count": sum(len(profile.evidence) for profile in profiles),
            "lookback_days": self.lookback_days,
            "window_days": self.window_days,
            "collection_error_count": len(collection_errors),
            "tools_used": [item["name"] for item in tool_inventory],
        }

        context = {
            "summary": summary,
            "tool_inventory": tool_inventory,
            "profiles": profiles,
            "existing_slos": existing_slos,
            "slo_coverage_matrix": slo_coverage_matrix,
            "top_recommendations": top_recommendations,
            "production_ready_recommendations": production_ready_recommendations,
            "candidate_recommendations": candidate_recommendations,
            "recommendations": recommendations,
            "findings": findings,
            "repo_profile": repo_profile,
            "collection_errors": collection_errors,
            "sloth_yaml": sloth_yaml,
        }

        write_reports(self.output_dir, context, sloth_yaml)
        return context

    def _normalize_services(self, services: list[str]) -> list[str]:
        normalized: set[str] = set()

        for svc in services:
            canonical = canonical_service_name(svc)
            if canonical and is_application_service(canonical):
                normalized.add(canonical)

        return sorted(normalized)

    def _apply_repo_profile(self, profile: ServiceProfile, repo_profile: dict[str, Any]) -> None:
        if not repo_profile.get("available"):
            return

        profile.entrypoints = repo_profile.get("entrypoints", [])[:20]
        profile.dependencies = repo_profile.get("dependencies", [])[:20]
        profile.evidence.append(
            SignalEvidence(
                source="repo",
                signal_type="service_profile",
                service=profile.name,
                title="Repository profile available",
                value=(
                    f"{repo_profile.get('files_scanned', 0)} files scanned; "
                    f"languages={', '.join(repo_profile.get('languages', []))}"
                ),
                interpretation="Repository context was used to enrich ownership, dependency, and entrypoint context.",
                confidence=0.65,
                raw=repo_profile,
            )
        )

    def _hydrate_profile_from_evidence(self, profile: ServiceProfile) -> None:
        for ev in profile.evidence:
            if ev.signal_type == "trace_operations" and ev.raw:
                operations = ev.raw.get("operations") or []
                if isinstance(operations, list):
                    profile.operations = [str(item) for item in operations[:30]]

            if ev.signal_type == "service_map" and ev.raw:
                dependencies = ev.raw.get("dependencies") or []
                if isinstance(dependencies, list):
                    profile.dependencies.extend([str(item) for item in dependencies[:20]])

        profile.dependencies = sorted(set(profile.dependencies))[:30]

    def _tool_name(self, tool: dict[str, Any]) -> str:
        return str(tool.get("name") or tool.get("tool") or tool.get("tool_name") or "").lower()

    def _tool_url(self, tool: dict[str, Any]) -> str:
        return str(tool.get("url") or tool.get("base_url") or tool.get("baseUrl") or "").rstrip("/")

    def _get_prometheus_client(self) -> PrometheusClient | None:
        for tool in self.tools:
            name = self._tool_name(tool)
            url = self._tool_url(tool)
            if "prometheus" in name and url:
                return PrometheusClient(url=url)
        return None

    def _get_jaeger_client(self) -> JaegerClient | None:
        for tool in self.tools:
            name = self._tool_name(tool)
            url = self._tool_url(tool)
            if ("jaeger" in name or "tempo" in name) and url:
                return JaegerClient(url=url)
        return None

    def _service_criticality(self, service: str) -> str:
        requested = (self.criticality or "").lower()
        if requested in {"critical", "high", "medium", "low"}:
            return requested

        service_lower = service.lower()
        if any(token in service_lower for token in ["checkout", "payment", "cart", "frontend", "gateway", "proxy"]):
            return "high"
        if any(token in service_lower for token in ["recommendation", "ad", "image", "email"]):
            return "low"
        return "medium"

    def _infer_journeys(self, profile: ServiceProfile) -> list[str]:
        journeys: set[str] = set()
        combined = " ".join(profile.operations + profile.entrypoints + profile.dependencies + [profile.name]).lower()

        if "checkout" in combined:
            journeys.add("checkout")
        if "payment" in combined:
            journeys.add("payment")
        if "cart" in combined:
            journeys.add("cart")
        if "login" in combined or "auth" in combined:
            journeys.add("login")
        if "search" in combined:
            journeys.add("search")
        if "recommend" in combined:
            journeys.add("recommendation")

        return sorted(journeys)
