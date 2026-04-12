"""Dynatrace-specific rule check implementations.

Evaluates Dynatrace setup quality — entity coverage, SLO adoption, Davis AI
alerting configuration, notification integrations, Synthetic monitoring,
RUM (Real User Monitoring), log management, and custom metric governance.

Checks read from estate.summary.dynatrace_* fields and estate collections
filtered by source_tool == "dynatrace".
"""
from __future__ import annotations

from observascore.model import ObservabilityEstate
from observascore.rules.engine import register


def _dt_services(estate: ObservabilityEstate):
    return [s for s in estate.services if s.source_tool == "dynatrace"]


def _dt_slos(estate: ObservabilityEstate):
    return [r for r in estate.recording_rules if r.source_tool == "dynatrace"]


def _dt_dashboards(estate: ObservabilityEstate):
    return [d for d in estate.dashboards if d.source_tool == "dynatrace"]


# =============================================================================
# DT-001  Signal coverage — entity coverage gaps
# =============================================================================

@register("DT-001")
def dt_001_entity_coverage(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    services = estate.summary.dynatrace_services
    hosts = estate.summary.dynatrace_hosts
    apps = estate.summary.dynatrace_applications
    if services == 0 and hosts == 0 and apps == 0:
        return [{
            "description": (
                "Dynatrace has no monitored entities (services, hosts, or applications). "
                "OneAgent is likely not deployed or the API token lacks entity read permissions. "
                "Without entity discovery, no APM, infrastructure, or user-experience data is flowing."
            ),
            "evidence": [],
        }]
    # Warn if services are monitored but no hosts (agent-only APM without infra)
    if services > 0 and hosts == 0:
        return [{
            "description": (
                f"Dynatrace monitors {services} service(s) but no hosts. "
                "Infrastructure monitoring (OneAgent on hosts) is absent. "
                "Resource saturation causing service slowdowns cannot be correlated "
                "to host-level causes without host-level entity monitoring."
            ),
            "evidence": [f"{services} services, 0 hosts"],
        }]
    return []


# =============================================================================
# DT-002  SLO maturity — no SLOs defined
# =============================================================================

@register("DT-002")
def dt_002_no_slos(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    slos = _dt_slos(estate)
    if len(slos) == 0:
        return [{
            "description": (
                "No Dynatrace SLOs are defined. "
                "Dynatrace has native SLO support with built-in burn-rate calculation, "
                "SLO-based dashboards, and SLO-driven alerting profiles. "
                "Without SLOs, the team is operating on infrastructure metrics "
                "rather than user-facing reliability targets."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-003  Alert quality — no custom alerting profiles
# =============================================================================

@register("DT-003")
def dt_003_default_alerting_only(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    custom_profiles = estate.summary.dynatrace_alerting_profiles
    if estate.summary.dynatrace_services > 0 and custom_profiles == 0:
        return [{
            "description": (
                "Only the Dynatrace default alerting profile is in use. "
                "The default profile notifies on all problems to all integrations — "
                "causing alert storms during incidents. Custom alerting profiles "
                "are required to route by severity, impact level, entity type, "
                "and management zone (team/service ownership)."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-004  Alert quality — no notification integrations
# =============================================================================

@register("DT-004")
def dt_004_no_notification_integrations(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    integrations = estate.summary.dynatrace_notification_integrations
    if estate.summary.dynatrace_services > 0 and integrations == 0:
        return [{
            "description": (
                "No Dynatrace notification integrations configured "
                "(PagerDuty, OpsGenie, Slack, ServiceNow, xMatters, etc.). "
                "Davis AI problems are detected but no on-call escalation path exists. "
                "Problems will be visible in the Dynatrace UI but no responder is paged."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-005  Modern tooling — no Synthetic monitors
# =============================================================================

@register("DT-005")
def dt_005_no_synthetic_monitors(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    if estate.summary.dynatrace_services > 0 and estate.summary.dynatrace_synthetics == 0:
        return [{
            "description": (
                "No Dynatrace Synthetic monitors configured. "
                "Dynatrace Synthetics support browser clickpaths (real user journey simulation), "
                "HTTP monitors (API availability), and multi-step sequences. "
                "Without them, outside-in availability and performance are unmonitored — "
                "Dynatrace only sees the system from the inside."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-006  Modern tooling — no Real User Monitoring
# =============================================================================

@register("DT-006")
def dt_006_no_rum(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    # RUM is detected via APPLICATION entity type in Dynatrace
    if estate.summary.dynatrace_services > 0 and not estate.summary.dynatrace_has_rum:
        return [{
            "description": (
                "Dynatrace Real User Monitoring (RUM / Digital Experience Monitoring) "
                "is not configured. No web or mobile application entities were found. "
                "RUM provides browser waterfall, Apdex scores, user session replay, "
                "and crash analytics — the last mile of observability from the user's "
                "perspective that APM alone cannot cover."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-007  Signal coverage — Log Management not configured
# =============================================================================

@register("DT-007")
def dt_007_no_log_management(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    if estate.summary.dynatrace_services > 0 and not estate.summary.dynatrace_has_log_management:
        return [{
            "description": (
                "Dynatrace Log Management (Log Monitoring v2 / DQL) is not configured. "
                "Without log ingestion, Davis AI cannot correlate log anomalies with "
                "performance problems, and log-based problem detection is unavailable. "
                "Dynatrace Logs is the tightest log-to-trace correlation in the stack."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-008  Incident response — no custom dashboards
# =============================================================================

@register("DT-008")
def dt_008_no_custom_dashboards(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    custom_dashboards = _dt_dashboards(estate)
    if estate.summary.dynatrace_services > 0 and len(custom_dashboards) == 0:
        return [{
            "description": (
                "No custom Dynatrace dashboards found. "
                "Built-in Dynatrace views are comprehensive but team-specific and "
                "executive-facing dashboards require custom configuration. "
                "Without custom dashboards, on-call responders rely on fragmented "
                "default views during incidents — increasing time-to-diagnose."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# DT-009  Governance — open Davis AI problems not acted on
# =============================================================================

@register("DT-009")
def dt_009_open_problems(estate: ObservabilityEstate) -> list[dict]:
    if "dynatrace" not in estate.configured_tools:
        return []
    open_problems = estate.summary.dynatrace_problems_open
    if open_problems > 10:
        return [{
            "description": (
                f"{open_problems} Davis AI problems are currently open. "
                "A large number of unacknowledged open problems indicates either "
                "alert fatigue (too many low-quality alerting profiles) or that "
                "incidents are not being worked or closed — both maturity concerns."
            ),
            "evidence": [f"{open_problems} open problems detected"],
        }]
    return []
