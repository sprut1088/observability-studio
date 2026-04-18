"""Trend-based rule check implementations.

These checks evaluate the observability estate against 2024-2025 industry trends
and modern best practices that go beyond the core signal/alert/dashboard quality checks.

Dimensions covered:
  otel_adoption        — OpenTelemetry Collector, OTel-native tracing, semantic conventions
  modern_tooling       — Continuous profiling, synthetic monitoring, service mesh telemetry
  security_observability — Security signals, audit logs, network security telemetry
"""
from __future__ import annotations

from observascore.model import ObservabilityEstate, SignalType
from observascore.rules.engine import register


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------

def _metric_names(estate: ObservabilityEstate) -> list[str]:
    return [
        s.identifier.lower()
        for s in estate.signals
        if s.signal_type == SignalType.METRIC
    ]


def _log_labels(estate: ObservabilityEstate) -> list[str]:
    return [
        s.identifier.lower()
        for s in estate.signals
        if s.signal_type == SignalType.LOG
    ]


def _scrape_jobs(estate: ObservabilityEstate) -> list[str]:
    return [t.job.lower() for t in estate.scrape_targets]


def _has_tool(estate: ObservabilityEstate, tool: str) -> bool:
    return tool in estate.configured_tools


# =============================================================================
# OpenTelemetry Adoption
# =============================================================================

@register("OTEL-001")
def otel_001_collector_absent(estate: ObservabilityEstate) -> list[dict]:
    """Flag if no OTel Collector is configured in the pipeline."""
    if _has_tool(estate, "otel_collector"):
        return []
    # Also check if any scrape jobs hint at an OTel collector
    jobs = _scrape_jobs(estate)
    if any("otelcol" in j or "opentelemetry" in j for j in jobs):
        return []
    return [{
        "description": (
            "No OpenTelemetry Collector detected. The OTel Collector is the "
            "CNCF-graduated vendor-neutral pipeline for metrics, logs, and traces. "
            "Without it, telemetry is tightly coupled to individual backend SDKs."
        ),
        "evidence": [],
    }]


@register("OTEL-002")
def otel_002_otel_native_tracing(estate: ObservabilityEstate) -> list[dict]:
    """Flag if traces are from Jaeger (OTLP-deprecated) but Tempo is absent."""
    has_jaeger = _has_tool(estate, "jaeger")
    has_tempo = _has_tool(estate, "tempo") or any(
        s.source_tool == "tempo" for s in estate.signals
    )
    jaeger_traces = any(s.source_tool == "jaeger" for s in estate.signals)
    if has_jaeger and jaeger_traces and not has_tempo:
        return [{
            "description": (
                "Tracing backend is Jaeger without Grafana Tempo. "
                "Jaeger is moving toward deprecation of its native protocol in favour of OTLP. "
                "Tempo is the OTel-native tracing backend with tighter Grafana integration, "
                "exemplar support, and TraceQL query language."
            ),
            "evidence": [f"Jaeger services: {', '.join(s.name for s in estate.services[:5])}"],
        }]
    return []


@register("OTEL-003")
def otel_003_semantic_conventions(estate: ObservabilityEstate) -> list[dict]:
    """Check for OpenTelemetry semantic convention usage in signals."""
    # OTel semantic conventions produce labels/identifiers like service.name,
    # http.method, db.system, etc. Check metric names and labels.
    otel_indicators = [
        "http_server_duration",      # OTel HTTP server metrics
        "http_client_duration",
        "rpc_server_duration",       # OTel RPC metrics
        "db_client_connections",     # OTel DB metrics
        "process_runtime",           # OTel process metrics
        "system_cpu",                # OTel system metrics
    ]
    metrics = _metric_names(estate)
    has_otel_metrics = any(
        any(ind in m for ind in otel_indicators) for m in metrics
    )
    # Check for OTel semantic labels on existing signals
    has_service_name_label = any(
        "service.name" in s.labels or "service_name" in s.labels
        for s in estate.signals
        if s.labels
    )
    has_otel_jobs = any("otel" in j or "opentelemetry" in j for j in _scrape_jobs(estate))
    if has_otel_metrics or has_service_name_label or has_otel_jobs:
        return []
    return [{
        "description": (
            "No OpenTelemetry semantic convention signals detected. "
            "OTel semantic conventions (service.name, http.method, db.system, etc.) "
            "enable automatic correlation, out-of-the-box dashboards, and vendor-neutral "
            "instrumentation. Current instrumentation appears to use ad-hoc or vendor-specific conventions."
        ),
        "evidence": [],
    }]


# =============================================================================
# Modern Tooling
# =============================================================================

@register("MODERN-001")
def modern_001_continuous_profiling(estate: ObservabilityEstate) -> list[dict]:
    """Flag absence of always-on continuous profiling."""
    metrics = _metric_names(estate)
    jobs = _scrape_jobs(estate)
    profiling_indicators = [
        "pyroscope", "parca", "pprof", "profiling", "flame",
        "grafana_pyroscope", "continuous_profil",
    ]
    has_profiling = (
        any(kw in m for m in metrics for kw in profiling_indicators)
        or any(kw in j for j in jobs for kw in profiling_indicators)
        or any(s.signal_type == SignalType.PROFILE for s in estate.signals)
    )
    if has_profiling:
        return []
    return [{
        "description": (
            "No continuous profiling tool detected (Grafana Pyroscope, Parca, or equivalent). "
            "Continuous profiling adds the fourth pillar of observability alongside metrics, logs, "
            "and traces. It enables always-on CPU/memory flame graphs, goroutine leak detection, "
            "and production performance regression visibility without manual overhead."
        ),
        "evidence": [],
    }]


@register("MODERN-002")
def modern_002_synthetic_monitoring(estate: ObservabilityEstate) -> list[dict]:
    """Flag absence of synthetic/active monitoring."""
    jobs = _scrape_jobs(estate)
    metrics = _metric_names(estate)
    synthetic_indicators = [
        "blackbox_exporter", "blackbox", "probe_", "synthetic",
        "k6", "checkly", "pingdom", "uptime",
    ]
    has_synthetic = (
        any(kw in j for j in jobs for kw in synthetic_indicators)
        or any(kw in m for m in metrics for kw in synthetic_indicators)
    )
    if has_synthetic:
        return []
    return [{
        "description": (
            "No synthetic or active monitoring detected. Synthetic monitoring validates "
            "user-facing endpoints from the outside-in, catching issues that internal "
            "metrics miss (CDN failures, DNS issues, geo-specific outages). "
            "Prometheus Blackbox Exporter or Grafana Synthetic Monitoring are simple starting points."
        ),
        "evidence": [],
    }]


@register("MODERN-003")
def modern_003_service_mesh_telemetry(estate: ObservabilityEstate) -> list[dict]:
    """Flag absence of service mesh or eBPF network-level telemetry."""
    metrics = _metric_names(estate)
    jobs = _scrape_jobs(estate)
    mesh_indicators = [
        "envoy", "istio", "linkerd", "cilium", "consul_connect",
        "ebpf", "pixie", "hubble", "odigos", "service_mesh",
    ]
    has_mesh = (
        any(kw in m for m in metrics for kw in mesh_indicators)
        or any(kw in j for j in jobs for kw in mesh_indicators)
    )
    if has_mesh:
        return []
    return [{
        "description": (
            "No service mesh or eBPF network-level telemetry detected. "
            "Without a service mesh (Istio, Linkerd, Cilium) or eBPF-based observability (Pixie, Odigos), "
            "network-level latency, connection errors, and east-west traffic patterns are invisible. "
            "This creates blind spots in microservices dependency debugging."
        ),
        "evidence": [],
    }]


@register("MODERN-004")
def modern_004_alertmanager_integration(estate: ObservabilityEstate) -> list[dict]:
    """Flag if AlertManager is missing or has no escalation integrations."""
    has_am = _has_tool(estate, "alertmanager")
    if not has_am:
        return [{
            "description": (
                "AlertManager is not configured. Without AlertManager, alert routing, "
                "deduplication, grouping, inhibition, and on-call escalation are absent. "
                "Raw Prometheus alerts without routing leads to alert storms and missed pages."
            ),
            "evidence": [],
        }]
    # Check for incident management integrations
    escalation_tools = {"pagerduty", "opsgenie", "victorops"}
    configured_integrations = set(estate.summary.alertmanager_integrations or [])
    if not (escalation_tools & configured_integrations):
        return [{
            "description": (
                "AlertManager is present but has no incident management integration "
                "(PagerDuty, OpsGenie, VictorOps). Without escalation tooling, on-call "
                "engineers rely on Slack/email which lack acknowledgement, escalation "
                "policies, on-call schedules, and MTTR tracking."
            ),
            "evidence": [f"Configured integrations: {list(configured_integrations) or ['none detected']}"],
        }]
    return []


# =============================================================================
# Security Observability
# =============================================================================

@register("SEC-001")
def sec_001_security_signals_absent(estate: ObservabilityEstate) -> list[dict]:
    """Flag absence of runtime security or audit observability signals."""
    metrics = _metric_names(estate)
    log_labels = _log_labels(estate)
    jobs = _scrape_jobs(estate)
    security_indicators = [
        "falco", "tetragon", "audit", "seccomp", "apparmor",
        "vulnerability", "cve", "intrusion", "threat", "security_",
        "runtime_security",
    ]
    has_security = (
        any(kw in m for m in metrics for kw in security_indicators)
        or any(kw in l for l in log_labels for kw in security_indicators)
        or any(kw in j for j in jobs for kw in security_indicators)
    )
    if has_security:
        return []
    return [{
        "description": (
            "No runtime security or audit observability signals detected. "
            "Security observability (Falco alerts, Kubernetes audit logs, runtime syscall "
            "anomalies via Tetragon) is absent from the estate. In regulated environments, "
            "this creates compliance gaps and leaves the team blind to runtime threats."
        ),
        "evidence": [],
    }]


@register("SEC-002")
def sec_002_audit_log_collection(estate: ObservabilityEstate) -> list[dict]:
    """Flag if Kubernetes or cloud audit logs are not being collected."""
    log_labels = _log_labels(estate)
    audit_indicators = ["audit", "k8s_audit", "kubernetes_audit", "apiserver_audit", "cloudtrail"]
    has_audit = any(kw in l for l in log_labels for kw in audit_indicators)
    if has_audit:
        return []
    return [{
        "description": (
            "No Kubernetes API server or cloud audit log collection detected. "
            "Audit logs are required for compliance (SOC2, PCI-DSS, HIPAA) and incident forensics. "
            "Without them, answering 'who did what and when' during an incident is impossible."
        ),
        "evidence": [],
    }]


# =============================================================================
# Business Observability
# =============================================================================

@register("BIZ-001")
def biz_001_business_kpi_metrics(estate: ObservabilityEstate) -> list[dict]:
    """Flag if no business-level KPI metrics are present."""
    has_business = any(s.semantic_type == "business" for s in estate.signals)
    if has_business:
        return []
    metrics = _metric_names(estate)
    business_indicators = [
        "revenue", "conversion", "checkout", "order_", "payment_",
        "cart_", "signup", "activation", "retention", "churn",
        "booking", "transaction_", "feature_flag", "experiment_",
    ]
    has_biz_metrics = any(kw in m for m in metrics for kw in business_indicators)
    if has_biz_metrics:
        return []
    return [{
        "description": (
            "No business KPI metrics detected in the estate. Without custom business metrics "
            "(order rate, payment success rate, conversion funnel, revenue per minute), "
            "the SRE team cannot answer 'is the business impacted by this incident?' "
            "during on-call response. Business metrics are also the foundation of meaningful SLIs."
        ),
        "evidence": [],
    }]


@register("BIZ-002")
def biz_002_dora_metrics(estate: ObservabilityEstate) -> list[dict]:
    """Flag if DORA-style delivery metrics are not instrumented."""
    metrics = _metric_names(estate)
    dora_indicators = [
        "deployment_frequency", "deploy_frequency", "lead_time",
        "change_failure_rate", "mttr", "mean_time_to_recover",
        "deployment_count", "release_count",
    ]
    has_dora = any(kw in m for m in metrics for kw in dora_indicators)
    if has_dora:
        return []
    return [{
        "description": (
            "No DORA metrics instrumentation detected (deployment frequency, lead time for changes, "
            "change failure rate, MTTR). DORA metrics are the industry standard for measuring "
            "engineering delivery performance. Without them, the team has no data-driven baseline "
            "for improvement initiatives or leadership reporting."
        ),
        "evidence": [],
    }]
