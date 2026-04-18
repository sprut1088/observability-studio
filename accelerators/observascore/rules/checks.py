"""Rule check implementations.

Each function inspects the ObservabilityEstate and returns a list of
violation dicts. Each dict may contain 'description' and 'evidence'.
An empty list means "passed".
"""
from __future__ import annotations

import re

from observascore.model import ObservabilityEstate, AlertClassification
from observascore.rules.engine import register


# =============================================================================
# Signal Coverage
# =============================================================================

@register("SIG-001")
def sig_001_metrics_present(estate: ObservabilityEstate) -> list[dict]:
    """Fire if no metrics were found at all."""
    metric_count = sum(1 for s in estate.signals if s.signal_type.value == "metric")
    if metric_count == 0:
        return [{"description": "No metrics collected from Prometheus", "evidence": []}]
    return []


@register("SIG-002")
def sig_002_logs_present(estate: ObservabilityEstate) -> list[dict]:
    log_count = sum(1 for s in estate.signals if s.signal_type.value == "log")
    if log_count == 0:
        return [{"description": "No log signals detected (Loki empty or not reachable)", "evidence": []}]
    return []


@register("SIG-003")
def sig_003_traces_present(estate: ObservabilityEstate) -> list[dict]:
    trace_count = sum(1 for s in estate.signals if s.signal_type.value == "trace")
    if trace_count == 0:
        return [{"description": "No trace signals detected (Jaeger empty or not reachable)", "evidence": []}]
    return []


@register("SIG-004")
def sig_004_scrape_health(estate: ObservabilityEstate) -> list[dict]:
    """Flag down targets."""
    down = [t for t in estate.scrape_targets if t.health != "up"]
    if not down:
        return []
    ev = [f"{t.job}/{t.instance} — {t.last_scrape_error or 'down'}" for t in down[:10]]
    return [{
        "description": f"{len(down)} scrape target(s) unhealthy out of {len(estate.scrape_targets)}",
        "evidence": ev,
    }]


# =============================================================================
# Golden Signals
# =============================================================================

@register("GOLD-001")
def gold_001_latency_metrics(estate: ObservabilityEstate) -> list[dict]:
    """At least some latency metrics should exist."""
    latency = [s for s in estate.signals if s.semantic_type == "latency"]
    if len(latency) < 1:
        return [{"description": "No latency metrics found across the estate", "evidence": []}]
    return []


@register("GOLD-002")
def gold_002_error_metrics(estate: ObservabilityEstate) -> list[dict]:
    errors = [s for s in estate.signals if s.semantic_type == "error"]
    if len(errors) < 1:
        return [{"description": "No error-rate metrics found across the estate", "evidence": []}]
    return []


@register("GOLD-003")
def gold_003_traffic_metrics(estate: ObservabilityEstate) -> list[dict]:
    traffic = [s for s in estate.signals if s.semantic_type == "traffic"]
    if len(traffic) < 1:
        return [{"description": "No traffic/throughput metrics found", "evidence": []}]
    return []


@register("GOLD-004")
def gold_004_saturation_metrics(estate: ObservabilityEstate) -> list[dict]:
    sat = [s for s in estate.signals if s.semantic_type == "saturation"]
    if len(sat) < 1:
        return [{"description": "No saturation metrics (CPU/memory/pools) found", "evidence": []}]
    return []


# =============================================================================
# SLO Maturity
# =============================================================================

@register("SLO-001")
def slo_001_burn_rate_alerts_exist(estate: ObservabilityEstate) -> list[dict]:
    """Multi-window burn-rate alerts are the SLO maturity marker."""
    burn_rate_alerts = [
        a for a in estate.alert_rules
        if a.classification == AlertClassification.BURN_RATE
    ]
    if not burn_rate_alerts:
        return [{
            "description": "No SLO burn-rate alerts detected. SLO-driven alerting absent.",
            "evidence": [],
        }]
    return []


@register("SLO-002")
def slo_002_recording_rules_for_slo(estate: ObservabilityEstate) -> list[dict]:
    """Recording rules with slo/sli prefixes indicate SLO-as-code pattern."""
    slo_records = [
        r for r in estate.recording_rules
        if re.search(r"slo|sli|error[_\-]?budget", r.name, re.IGNORECASE)
    ]
    if not slo_records:
        return [{
            "description": "No SLO/SLI recording rules detected — SLO-as-code pattern not in use",
            "evidence": [],
        }]
    return []


# =============================================================================
# Alert Quality
# =============================================================================

@register("ALERT-001")
def alert_001_runbook_coverage(estate: ObservabilityEstate) -> list[dict]:
    """Every alert should have a runbook URL."""
    if not estate.alert_rules:
        return []
    missing = [a for a in estate.alert_rules if not a.runbook_url]
    if not missing:
        return []
    pct = 100 * len(missing) / len(estate.alert_rules)
    ev = [f"{a.source_tool}: {a.name}" for a in missing[:10]]
    return [{
        "description": f"{len(missing)}/{len(estate.alert_rules)} alerts ({pct:.0f}%) lack a runbook annotation",
        "evidence": ev,
    }]


@register("ALERT-002")
def alert_002_severity_label(estate: ObservabilityEstate) -> list[dict]:
    if not estate.alert_rules:
        return []
    missing = [a for a in estate.alert_rules if not a.severity]
    if not missing:
        return []
    ev = [f"{a.source_tool}: {a.name}" for a in missing[:10]]
    return [{
        "description": f"{len(missing)} alert(s) have no severity label, breaking routing & triage",
        "evidence": ev,
    }]


@register("ALERT-003")
def alert_003_for_clause(estate: ObservabilityEstate) -> list[dict]:
    """Prometheus alerts should have a `for:` clause to reduce flap."""
    prom_alerts = [a for a in estate.alert_rules if a.source_tool == "prometheus"]
    if not prom_alerts:
        return []
    no_for = [
        a for a in prom_alerts
        if not a.for_duration or a.for_duration in ("0s", "0", "", "0m")
    ]
    if not no_for:
        return []
    ev = [a.name for a in no_for[:10]]
    return [{
        "description": f"{len(no_for)} Prometheus alert(s) fire immediately with no 'for:' clause, risking flap",
        "evidence": ev,
    }]


@register("ALERT-004")
def alert_004_symptom_vs_cause(estate: ObservabilityEstate) -> list[dict]:
    """Too many cause-based, too few symptom-based alerts indicates immature alerting."""
    if not estate.alert_rules:
        return []
    symptom = sum(1 for a in estate.alert_rules if a.classification == AlertClassification.SYMPTOM)
    cause = sum(1 for a in estate.alert_rules if a.classification == AlertClassification.CAUSE)
    total = len(estate.alert_rules)
    if cause > symptom and cause > 3:
        return [{
            "description": (
                f"Alert portfolio is cause-heavy: {cause} cause-based vs {symptom} symptom-based "
                f"(of {total}). Prefer user-facing symptoms for paging alerts."
            ),
            "evidence": [],
        }]
    return []


@register("ALERT-005")
def alert_005_description_annotation(estate: ObservabilityEstate) -> list[dict]:
    """Alerts should have a description/summary annotation."""
    if not estate.alert_rules:
        return []
    missing = [
        a for a in estate.alert_rules
        if not (a.annotations.get("description") or a.annotations.get("summary"))
    ]
    if not missing:
        return []
    ev = [a.name for a in missing[:10]]
    return [{
        "description": f"{len(missing)} alert(s) lack a description/summary annotation",
        "evidence": ev,
    }]


# =============================================================================
# Incident Response
# =============================================================================

@register("INC-001")
def inc_001_dashboards_present(estate: ObservabilityEstate) -> list[dict]:
    if not estate.dashboards:
        return [{"description": "No Grafana dashboards found — responders have no visual context", "evidence": []}]
    return []


@register("INC-002")
def inc_002_dashboard_folder_structure(estate: ObservabilityEstate) -> list[dict]:
    """Dashboards all in 'General' folder = poor organization."""
    if not estate.dashboards:
        return []
    general = [
        d for d in estate.dashboards
        if not d.folder or d.folder.lower() in ("general", "")
    ]
    if len(general) > 5 and len(general) / len(estate.dashboards) > 0.5:
        return [{
            "description": (
                f"{len(general)}/{len(estate.dashboards)} dashboards live in 'General' folder. "
                "Folder structure is absent or flat."
            ),
            "evidence": [d.title for d in general[:10]],
        }]
    return []


@register("INC-003")
def inc_003_dashboard_templating(estate: ObservabilityEstate) -> list[dict]:
    """Dashboards without variables are usually duplicated per-env/per-service."""
    if not estate.dashboards:
        return []
    no_vars = [d for d in estate.dashboards if not d.has_templating]
    if len(no_vars) / max(len(estate.dashboards), 1) > 0.7:
        return [{
            "description": (
                f"{len(no_vars)}/{len(estate.dashboards)} dashboards have no template variables. "
                "Indicates dashboard duplication rather than parameterization."
            ),
            "evidence": [d.title for d in no_vars[:10]],
        }]
    return []


@register("INC-004")
def inc_004_panel_units(estate: ObservabilityEstate) -> list[dict]:
    """Panels without units confuse responders."""
    if not estate.dashboards:
        return []
    total_panels = 0
    no_unit = 0
    examples: list[str] = []
    for d in estate.dashboards:
        for p in d.panels:
            total_panels += 1
            if not p.unit or p.unit in ("none", "short"):
                no_unit += 1
                if len(examples) < 10:
                    examples.append(f"{d.title} / {p.title}")
    if total_panels == 0:
        return []
    pct = 100 * no_unit / total_panels
    if pct > 40:
        return [{
            "description": f"{no_unit}/{total_panels} panels ({pct:.0f}%) have no unit configured",
            "evidence": examples,
        }]
    return []


# =============================================================================
# Automation
# =============================================================================

@register("AUTO-001")
def auto_001_recording_rules_exist(estate: ObservabilityEstate) -> list[dict]:
    """Any recording rules at all indicates some optimization discipline."""
    if not estate.recording_rules:
        return [{
            "description": "No Prometheus recording rules defined — expensive queries run ad-hoc",
            "evidence": [],
        }]
    return []


@register("AUTO-002")
def auto_002_correlation_readiness(estate: ObservabilityEstate) -> list[dict]:
    """Check if common correlation labels exist across signals."""
    prom_has_service_label = any(
        "service" in s.labels for s in estate.signals
    )
    loki_has_service = "service" in [s.identifier for s in estate.signals if s.signal_type.value == "log"] or \
                       "service_name" in [s.identifier for s in estate.signals if s.signal_type.value == "log"]
    jaeger_has_services = any(s.signal_type.value == "trace" for s in estate.signals)
    if not (prom_has_service_label or loki_has_service) or not jaeger_has_services:
        return [{
            "description": (
                "Correlation readiness weak: no shared 'service' identifier across metrics/logs/traces. "
                "Metrics-to-logs-to-traces navigation will be manual."
            ),
            "evidence": [],
        }]
    return []


# =============================================================================
# Governance
# =============================================================================

@register("GOV-001")
def gov_001_datasource_sprawl(estate: ObservabilityEstate) -> list[dict]:
    if len(estate.datasources) > 10:
        return [{
            "description": f"{len(estate.datasources)} Grafana datasources configured — possible sprawl",
            "evidence": [f"{d.name} ({d.ds_type})" for d in estate.datasources[:15]],
        }]
    return []


@register("GOV-002")
def gov_002_dashboard_ownership(estate: ObservabilityEstate) -> list[dict]:
    if not estate.dashboards:
        return []
    tagged = [d for d in estate.dashboards if d.tags]
    pct_untagged = 100 * (len(estate.dashboards) - len(tagged)) / len(estate.dashboards)
    if pct_untagged > 60:
        return [{
            "description": f"{pct_untagged:.0f}% of dashboards have no tags — ownership unclear",
            "evidence": [d.title for d in estate.dashboards if not d.tags][:10],
        }]
    return []


@register("GOV-003")
def gov_003_alert_grouping(estate: ObservabilityEstate) -> list[dict]:
    """Alerts should be grouped logically — solo alerts in their own groups suggests ad-hoc creation."""
    prom_alerts = [a for a in estate.alert_rules if a.source_tool == "prometheus"]
    if not prom_alerts:
        return []
    groups: dict[str, int] = {}
    for a in prom_alerts:
        g = a.group or "default"
        groups[g] = groups.get(g, 0) + 1
    singleton_groups = [g for g, c in groups.items() if c == 1]
    if len(singleton_groups) > 5 and len(singleton_groups) / max(len(groups), 1) > 0.5:
        return [{
            "description": (
                f"{len(singleton_groups)}/{len(groups)} Prometheus alert groups contain a single rule. "
                "Alert organization appears ad-hoc rather than structured by domain."
            ),
            "evidence": singleton_groups[:10],
        }]
    return []
