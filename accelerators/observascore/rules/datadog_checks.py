"""Datadog-specific rule check implementations.

Evaluates Datadog setup quality across monitors, SLOs, Synthetics, APM,
Log Management, Security Monitoring, Service Catalog, and tag governance.

Checks read from estate.summary.datadog_* fields and from estate collections
filtered by source_tool == "datadog".
"""
from __future__ import annotations

from observascore.model import ObservabilityEstate
from observascore.rules.engine import register


def _dd_monitors(estate: ObservabilityEstate):
    return [a for a in estate.alert_rules if a.source_tool == "datadog"]


def _dd_dashboards(estate: ObservabilityEstate):
    return [d for d in estate.dashboards if d.source_tool == "datadog"]


def _dd_slos(estate: ObservabilityEstate):
    return [r for r in estate.recording_rules if r.source_tool == "datadog"]


# =============================================================================
# DD-001  SLO maturity — no SLOs configured
# =============================================================================

@register("DD-001")
def dd_001_no_slos(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    slos = _dd_slos(estate)
    if len(slos) == 0:
        return [{
            "description": (
                "No Datadog SLOs are configured. "
                "Datadog has native SLO support (metric-based and monitor-based). "
                "Without SLOs, error budgets, burn-rate monitors, and SLO-based dashboards "
                "cannot be created. SLOs are the foundation of data-driven reliability work."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-002  Alert quality — monitors without notification handles
# =============================================================================

@register("DD-002")
def dd_002_monitors_without_notifications(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    monitors = _dd_monitors(estate)
    if not monitors:
        return []
    total = len(monitors)
    with_notif = estate.summary.datadog_monitors_with_notifications
    no_notif = total - with_notif
    pct_silent = 100 * no_notif / total
    if pct_silent > 40 and no_notif > 5:
        return [{
            "description": (
                f"{no_notif}/{total} Datadog monitors ({pct_silent:.0f}%) have no notification "
                "handles (@pagerduty, @slack-channel, @opsgenie, etc.) in their message body. "
                "Silent monitors fire but nobody is paged — the classic 'orphaned alert' problem."
            ),
            "evidence": [m.name for m in monitors if not m.runbook_url and
                         not m.annotations.get("notify_handles")][:10],
        }]
    return []


# =============================================================================
# DD-003  Modern tooling — no Synthetics configured
# =============================================================================

@register("DD-003")
def dd_003_no_synthetics(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    if estate.summary.datadog_synthetics == 0:
        return [{
            "description": (
                "No Datadog Synthetic tests configured. "
                "Datadog Synthetics provide browser tests (user journey simulation), "
                "API tests (endpoint health), and multi-step tests (checkout flows). "
                "Without Synthetics, outages that affect real users but not internal metrics "
                "— such as CDN or DNS failures — go undetected."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-004  Signal coverage — APM not enabled
# =============================================================================

@register("DD-004")
def dd_004_apm_not_enabled(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    if not estate.summary.datadog_has_apm:
        return [{
            "description": (
                "Datadog APM (distributed tracing) does not appear to be enabled. "
                "Without APM, there are no distributed traces, service maps, or "
                "trace-to-log correlation. Request latency root-cause analysis requires "
                "manually correlating metrics and logs — much slower MTTR."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-005  Signal coverage — Log Management not configured
# =============================================================================

@register("DD-005")
def dd_005_no_log_management(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    if not estate.summary.datadog_has_log_management:
        return [{
            "description": (
                "Datadog Log Management is not configured (no log indexes found). "
                "Without log indexes, log data is either not ingested or not retained "
                "with search capability. Logs-to-traces correlation, log-based monitors, "
                "and log anomaly detection are all unavailable."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-006  Security observability — Security Monitoring not enabled
# =============================================================================

@register("DD-006")
def dd_006_no_security_monitoring(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    if not estate.summary.datadog_has_security_monitoring:
        return [{
            "description": (
                "Datadog Security Monitoring (SIEM / Cloud Security Posture Management) "
                "is not enabled. Without it, security threats, compliance violations, "
                "and runtime anomalies detected in logs and infrastructure are not "
                "correlated or alerted on within the observability platform."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-007  Governance — Service Catalog not populated
# =============================================================================

@register("DD-007")
def dd_007_no_service_catalog(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    if not estate.summary.datadog_has_service_catalog:
        return [{
            "description": (
                "Datadog Service Catalog is not populated. "
                "The Service Catalog is the central registry for service ownership, "
                "runbook links, on-call rotations, SLOs, and dependency maps. "
                "Without it, incident responders must hunt for ownership and context "
                "during outages — directly increasing MTTR."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DD-008  Governance — dashboards without ownership tags
# =============================================================================

@register("DD-008")
def dd_008_dashboard_tag_coverage(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    dashboards = _dd_dashboards(estate)
    if not dashboards:
        return []
    untagged = [d for d in dashboards if not d.tags]
    pct = 100 * len(untagged) / len(dashboards)
    if pct > 60 and len(untagged) > 3:
        return [{
            "description": (
                f"{len(untagged)}/{len(dashboards)} Datadog dashboards ({pct:.0f}%) have no tags. "
                "Tags on Datadog dashboards are the primary ownership and discovery mechanism. "
                "Untagged dashboards are orphaned — no team, service, or environment context."
            ),
            "evidence": [d.title for d in untagged[:10]],
        }]
    return []


# =============================================================================
# DD-009  Incident response — no dashboards
# =============================================================================

@register("DD-009")
def dd_009_no_dashboards(estate: ObservabilityEstate) -> list[dict]:
    if "datadog" not in estate.configured_tools:
        return []
    dashboards = _dd_dashboards(estate)
    monitors = _dd_monitors(estate)
    if len(monitors) > 0 and len(dashboards) == 0:
        return [{
            "description": (
                f"{len(monitors)} Datadog monitors are configured but no dashboards exist. "
                "Monitors fire alerts but responders have no visual context for investigation. "
                "Every on-call-relevant service should have a corresponding dashboard."
            ),
            "evidence": [],
        }]
    return []
