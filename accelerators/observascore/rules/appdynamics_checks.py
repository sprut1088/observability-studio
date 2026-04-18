"""AppDynamics-specific rule check implementations.

These checks evaluate the AppDynamics setup quality — agent coverage,
health-rule discipline, business transaction configuration, baselines,
and the presence of EUM / SIM / DB Monitoring.

Checks read from estate.summary.appdynamics_* fields (populated by the
AppDynamics adapter) and from estate.services / estate.alert_rules filtered
by source_tool == "appdynamics".
"""
from __future__ import annotations

from observascore.model import ObservabilityEstate
from observascore.rules.engine import register


def _apm_alerts(estate: ObservabilityEstate):
    return [a for a in estate.alert_rules if a.source_tool == "appdynamics"]


def _apm_services(estate: ObservabilityEstate):
    return [s for s in estate.services if s.source_tool == "appdynamics"]


# =============================================================================
# APM-001  Signal coverage — no applications monitored
# =============================================================================

@register("APM-001")
def apm_001_no_applications(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    total = estate.summary.appdynamics_applications
    if total == 0:
        return [{
            "description": (
                "No AppDynamics-monitored applications found. "
                "Ensure AppDynamics agents are deployed and applications are registered "
                "in the Controller. Without applications, no APM data is flowing."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-002  Signal coverage — tiers without agents
# =============================================================================

@register("APM-002")
def apm_002_tiers_without_agents(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    apps = estate.summary.appdynamics_applications
    tiers = estate.summary.appdynamics_tiers
    if apps > 0 and tiers == 0:
        return [{
            "description": (
                f"{apps} application(s) registered but no tiers with active agents detected. "
                "Application tiers without agents generate no APM telemetry — "
                "latency, error rates, and slow calls will be invisible."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-003  Golden signals — business transactions not configured
# =============================================================================

@register("APM-003")
def apm_003_no_business_transactions(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    bt_count = estate.summary.appdynamics_business_transactions
    if estate.summary.appdynamics_tiers > 0 and bt_count == 0:
        return [{
            "description": (
                "No Business Transactions (BTs) are configured. "
                "BTs are AppDynamics' primary unit for measuring application performance — "
                "they define the user-facing request flows for which latency, errors, and "
                "load are tracked. Without BTs, SLO reporting and anomaly detection are impossible."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-004  Alert quality — no health rules defined
# =============================================================================

@register("APM-004")
def apm_004_no_health_rules(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    rules = _apm_alerts(estate)
    if estate.summary.appdynamics_applications > 0 and len(rules) == 0:
        return [{
            "description": (
                "No AppDynamics Health Rules are defined. "
                "Health Rules are the primary alerting mechanism in AppDynamics. "
                "Without them, performance degradation and errors will go undetected "
                "until users report symptoms."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-005  Alert quality — health rules without Actions (no escalation)
# =============================================================================

@register("APM-005")
def apm_005_health_rules_no_actions(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    rules = _apm_alerts(estate)
    if not rules:
        return []
    # AppDynamics health rules store their notification state in raw data.
    # We check for absence of a configured action policy as a proxy.
    # If alert_receivers is empty and AppDynamics is the only alerting tool, flag it.
    if not estate.alert_receivers and len(rules) > 0:
        return [{
            "description": (
                f"{len(rules)} AppDynamics Health Rule(s) defined, but no notification "
                "Actions or escalation integrations (PagerDuty, OpsGenie, email) detected. "
                "Health rules that fire silently provide no on-call value."
            ),
            "evidence": [r.name for r in rules[:5]],
        }]
    return []


# =============================================================================
# APM-006  Automation — no dynamic baselines
# =============================================================================

@register("APM-006")
def apm_006_no_baselines(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    apps = estate.summary.appdynamics_applications
    with_baselines = estate.summary.appdynamics_apps_with_baselines
    if apps > 0 and with_baselines == 0:
        return [{
            "description": (
                "No AppDynamics dynamic baselines detected. "
                "Baselines enable auto-tuning health rules to normal application performance, "
                "dramatically reducing false-positive alerts during off-peak and peak periods. "
                "Without baselines, static thresholds inevitably cause alert storms or blind spots."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-007  Modern tooling — no End User Monitoring
# =============================================================================

@register("APM-007")
def apm_007_no_eum(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    if estate.summary.appdynamics_applications > 0 and not estate.summary.appdynamics_has_eum:
        return [{
            "description": (
                "AppDynamics End User Monitoring (EUM / Browser RUM / Mobile RUM) is not configured. "
                "Without EUM, the team has no visibility into real-user page-load times, "
                "AJAX call performance, JavaScript errors, or mobile app crashes — "
                "leaving user-experience SLIs blind."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-008  Signal coverage — Server Infrastructure Monitoring absent
# =============================================================================

@register("APM-008")
def apm_008_no_sim(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    if estate.summary.appdynamics_applications > 0 and not estate.summary.appdynamics_has_sim:
        return [{
            "description": (
                "AppDynamics Server Infrastructure Monitoring (SIM) is not enabled. "
                "SIM provides host-level CPU, memory, disk, and network metrics correlated "
                "with APM data. Without it, resource saturation causing application slowdowns "
                "cannot be linked to infrastructure causes."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# APM-009  Incident response — no custom dashboards
# =============================================================================

@register("APM-009")
def apm_009_no_dashboards(estate: ObservabilityEstate) -> list[dict]:
    if "appdynamics" not in estate.configured_tools:
        return []
    apm_dashboards = [d for d in estate.dashboards if d.source_tool == "appdynamics"]
    if estate.summary.appdynamics_applications > 0 and len(apm_dashboards) == 0:
        return [{
            "description": (
                "No custom AppDynamics dashboards defined. "
                "Default AppDynamics views are useful but fragmented per-application. "
                "Custom dashboards combining BT metrics, infrastructure health, and business KPIs "
                "are essential for on-call responders and executive briefings."
            ),
            "evidence": [],
        }]
    return []
