"""Common Observability Model (COM).

Vendor-neutral representation of extracted configuration and telemetry.
All adapters normalize into these dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SignalType(str, Enum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    EVENT = "event"
    PROFILE = "profile"


class AlertClassification(str, Enum):
    SYMPTOM = "symptom"
    CAUSE = "cause"
    COMPOSITE = "composite"
    BURN_RATE = "burn_rate"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Signal:
    source_tool: str
    identifier: str
    signal_type: SignalType
    cardinality_estimate: Optional[int] = None
    semantic_type: Optional[str] = None  # latency | error | saturation | traffic | business | security
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class AlertRule:
    source_tool: str
    name: str
    expression: str
    severity: Optional[str] = None
    classification: AlertClassification = AlertClassification.UNKNOWN
    for_duration: Optional[str] = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    runbook_url: Optional[str] = None
    group: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordingRule:
    source_tool: str
    name: str
    expression: str
    group: Optional[str] = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class DashboardPanel:
    title: str
    panel_type: str
    queries: list[str] = field(default_factory=list)
    unit: Optional[str] = None
    has_thresholds: bool = False
    has_legend: bool = True


@dataclass
class Dashboard:
    source_tool: str
    uid: str
    title: str
    folder: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    panels: list[DashboardPanel] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    has_templating: bool = False
    last_modified: Optional[str] = None
    owner: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapeTarget:
    source_tool: str
    job: str
    instance: str
    health: str  # up | down | unknown
    last_scrape_error: Optional[str] = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Service:
    name: str
    source_tool: str
    operations: list[str] = field(default_factory=list)
    tier: Optional[str] = None
    owner: Optional[str] = None


@dataclass
class Datasource:
    source_tool: str
    name: str
    ds_type: str
    url: Optional[str] = None
    is_default: bool = False
    reachable: Optional[bool] = None


@dataclass
class AlertReceiver:
    """Represents an AlertManager notification receiver."""
    name: str
    receiver_types: list[str] = field(default_factory=list)  # slack, pagerduty, email, opsgenie, etc.


@dataclass
class ExtractionSummary:
    """High-level counts for the report header."""
    # Prometheus
    prometheus_targets: int = 0
    prometheus_targets_up: int = 0
    prometheus_alert_rules: int = 0
    prometheus_recording_rules: int = 0
    prometheus_metrics_sampled: int = 0
    # Grafana
    grafana_dashboards: int = 0
    grafana_folders: int = 0
    grafana_datasources: int = 0
    grafana_alert_rules: int = 0
    # Loki
    loki_labels: int = 0
    loki_streams_sampled: int = 0
    # Jaeger
    jaeger_services: int = 0
    jaeger_operations: int = 0
    # AlertManager
    alertmanager_receivers: int = 0
    alertmanager_silences: int = 0
    alertmanager_integrations: list[str] = field(default_factory=list)
    # Tempo
    tempo_services: int = 0
    # Elasticsearch
    elasticsearch_indices: int = 0
    elasticsearch_data_streams: int = 0
    # OTel Collector
    otel_receivers: list[str] = field(default_factory=list)
    otel_exporters: list[str] = field(default_factory=list)
    otel_pipelines: int = 0
    # AppDynamics
    appdynamics_applications: int = 0
    appdynamics_tiers: int = 0
    appdynamics_health_rules: int = 0
    appdynamics_business_transactions: int = 0
    appdynamics_has_eum: bool = False
    appdynamics_has_sim: bool = False        # Server Infrastructure Monitoring
    appdynamics_has_db_monitoring: bool = False
    appdynamics_apps_with_baselines: int = 0
    # Datadog
    datadog_monitors: int = 0
    datadog_monitors_with_notifications: int = 0
    datadog_dashboards: int = 0
    datadog_hosts: int = 0
    datadog_slos: int = 0
    datadog_synthetics: int = 0
    datadog_has_apm: bool = False
    datadog_has_log_management: bool = False
    datadog_has_security_monitoring: bool = False
    datadog_has_service_catalog: bool = False
    # Dynatrace
    dynatrace_services: int = 0
    dynatrace_hosts: int = 0
    dynatrace_applications: int = 0
    dynatrace_problems_open: int = 0
    dynatrace_slos: int = 0
    dynatrace_synthetics: int = 0
    dynatrace_alerting_profiles: int = 0
    dynatrace_notification_integrations: int = 0
    dynatrace_has_log_management: bool = False
    dynatrace_has_rum: bool = False
    # General
    extraction_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AI Analysis model
# ---------------------------------------------------------------------------

@dataclass
class AIInsight:
    """A single gap or finding produced by the AI analyst."""
    category: str   # "technical_gap" | "functional_gap" | "strength"
    title: str
    description: str
    severity: str   # "critical" | "high" | "medium" | "low" | "info"
    recommendation: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "evidence": self.evidence,
        }


@dataclass
class TrendAlignment:
    """Assessment of alignment with a specific modern observability trend."""
    trend: str
    status: str       # "adopted" | "partial" | "absent"
    impact: str       # "high" | "medium" | "low"
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend": self.trend,
            "status": self.status,
            "impact": self.impact,
            "description": self.description,
        }


@dataclass
class AIAnalysis:
    """Full AI-generated analysis of the observability estate."""
    narrative: str                                  # Executive narrative
    technical_gaps: list[AIInsight]
    functional_gaps: list[AIInsight]
    trend_alignments: list[TrendAlignment]
    prioritized_recommendations: list[str]
    trend_score: float                              # 0-100: how modern the stack is
    strengths: list[str]
    model_used: str
    generated_at: str
    error: Optional[str] = None                    # Populated if LLM call failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrative": self.narrative,
            "technical_gaps": [g.to_dict() for g in self.technical_gaps],
            "functional_gaps": [g.to_dict() for g in self.functional_gaps],
            "trend_alignments": [t.to_dict() for t in self.trend_alignments],
            "prioritized_recommendations": self.prioritized_recommendations,
            "trend_score": round(self.trend_score, 1),
            "strengths": self.strengths,
            "model_used": self.model_used,
            "generated_at": self.generated_at,
            "error": self.error,
        }


@dataclass
class ObservabilityEstate:
    """The full normalized picture of a client's observability estate."""
    client_name: str
    environment: str
    timestamp: str

    alert_rules: list[AlertRule] = field(default_factory=list)
    recording_rules: list[RecordingRule] = field(default_factory=list)
    dashboards: list[Dashboard] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    scrape_targets: list[ScrapeTarget] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    datasources: list[Datasource] = field(default_factory=list)
    alert_receivers: list[AlertReceiver] = field(default_factory=list)

    # Tracks which tool names were configured (even if extraction failed)
    configured_tools: list[str] = field(default_factory=list)

    summary: ExtractionSummary = field(default_factory=ExtractionSummary)
    ai_analysis: Optional[AIAnalysis] = None

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation."""
        from dataclasses import asdict
        return asdict(self)
