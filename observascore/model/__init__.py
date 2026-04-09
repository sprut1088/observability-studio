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
    semantic_type: Optional[str] = None  # latency | error | saturation | traffic | business
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
class ExtractionSummary:
    """High-level counts for the report header."""
    prometheus_targets: int = 0
    prometheus_targets_up: int = 0
    prometheus_alert_rules: int = 0
    prometheus_recording_rules: int = 0
    prometheus_metrics_sampled: int = 0
    grafana_dashboards: int = 0
    grafana_folders: int = 0
    grafana_datasources: int = 0
    grafana_alert_rules: int = 0
    loki_labels: int = 0
    loki_streams_sampled: int = 0
    jaeger_services: int = 0
    jaeger_operations: int = 0
    extraction_errors: list[str] = field(default_factory=list)


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

    summary: ExtractionSummary = field(default_factory=ExtractionSummary)

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation."""
        from dataclasses import asdict
        return asdict(self)
