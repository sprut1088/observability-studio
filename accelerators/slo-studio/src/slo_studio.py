from __future__ import annotations

from pathlib import Path
from typing import Any

from existing_slo_detector import detect_existing_slos
from jaeger_client import JaegerClient
from models import ServiceProfile, SignalEvidence
from prometheus_client import PrometheusClient
from repo_profiler import profile_repo
from report_writer import write_reports
from sloth_generator import generate_sloth_yaml
from slo_intelligence import recommend_slos
from recommendation_ranker import split_recommendations
from trend_analyzer import analyze_service_trends, discover_services, canonical_service_name


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
        self.tools = tools
        self.application = application
        self.service = service
        self.environment = environment
        self.primary_journey = primary_journey
        self.criticality = criticality
        self.objective_style = objective_style
        self.lookback_days = lookback_days
        self.window_days = window_days
        self.include_yaml = include_yaml
        self.repo_path = repo_path
        self.repo_url = repo_url
        self.output_dir = output_dir

    def run(self) -> dict[str, Any]:
        prom = self._get_prometheus_client()
        jaeger = self._get_jaeger_client()

        rules: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        existing_slos: list[dict[str, Any]] = []
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
                services = discover_services(prom)
            except Exception as exc:
                collection_errors.append({"source": "prometheus", "operation": "service discovery", "error": str(exc)})

        if jaeger:
            try:
                for svc in jaeger.services():
                    if svc not in services:
                        services.append(svc)
            except Exception as exc:
                collection_errors.append({"source": "jaeger", "operation": "service discovery", "error": str(exc)})

        services = sorted(set([canonical_service_name(svc) for svc in services if svc]))

        if self.service:
            services = [svc for svc in services if svc == self.service]

        existing_slos = detect_existing_slos(rules, alerts)

        repo_profile = profile_repo(self.repo_path)
        profiles: list[ServiceProfile] = []

        for svc in services:
            svc = canonical_service_name(svc)
            profile = ServiceProfile(name=svc, criticality=self._service_criticality(svc))

            if self.primary_journey:
                profile.user_journeys.append(self.primary_journey)

            if repo_profile.get("available"):
                profile.entrypoints = repo_profile.get("entrypoints", [])[:20]
                profile.dependencies = repo_profile.get("dependencies", [])[:20]
                profile.evidence.append(
                    SignalEvidence(
                        source="repo",
                        signal_type="service_profile",
                        service=svc,
                        title="Repository profile available",
                        value=f"{repo_profile.get('files_scanned', 0)} files scanned; languages={', '.join(repo_profile.get('languages', []))}",
                        interpretation="Repository context was used to enrich SLO recommendations.",
                        confidence=0.65,
                        raw=repo_profile,
                    )
                )

            if prom:
                try:
                    profile.evidence.extend(analyze_service_trends(prom, svc, self.lookback_days))
                except Exception as exc:
                    collection_errors.append({"source": "prometheus", "operation": f"trend analysis for {svc}", "error": str(exc)})

            if jaeger:
                try:
                    ops = jaeger.operations(svc)
                    profile.operations = ops[:30]
                    if ops:
                        profile.evidence.append(
                            SignalEvidence(
                                source="jaeger",
                                signal_type="trace_operations",
                                service=svc,
                                title="Trace operations discovered",
                                value=", ".join(ops[:10]),
                                interpretation="Trace operations help identify user journeys and operation-level SLO candidates.",
                                confidence=0.75,
                            )
                        )
                except Exception as exc:
                    collection_errors.append({"source": "jaeger", "operation": f"operations for {svc}", "error": str(exc)})

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

        sloth_yaml = generate_sloth_yaml(production_ready_recommendations) if self.include_yaml else ""

        summary = {
            "application": self.application,
            "service_count": len(profiles),
            "existing_slo_count": len(existing_slos),
            "recommended_slo_count": len(recommendations),
            "top_recommendation_count": len(top_recommendations),
            "production_ready_slo_count": len(production_ready_recommendations),
            "candidate_slo_count": len(candidate_recommendations),
            "finding_count": len(findings),
            "evidence_count": sum(len(profile.evidence) for profile in profiles),
            "lookback_days": self.lookback_days,
            "window_days": self.window_days,
            "collection_error_count": len(collection_errors),
        }

        context = {
            "summary": summary,
            "profiles": profiles,
            "existing_slos": existing_slos,
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
            if "jaeger" in name and url:
                return JaegerClient(url=url)
        return None

    def _service_criticality(self, service: str) -> str:
        if self.criticality in {"critical", "high", "medium", "low"}:
            return self.criticality

        service_lower = service.lower()
        if any(token in service_lower for token in ["checkout", "payment", "cart", "frontend", "gateway"]):
            return "high"
        if any(token in service_lower for token in ["recommendation", "ad", "image"]):
            return "low"
        return "medium"

    def _infer_journeys(self, profile: ServiceProfile) -> list[str]:
        journeys: set[str] = set()

        combined = " ".join(profile.operations + profile.entrypoints + [profile.name]).lower()

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

        return sorted(journeys)