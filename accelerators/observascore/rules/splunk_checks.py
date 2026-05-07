"""Splunk-specific rule check implementations."""

from __future__ import annotations

from observascore.model import ObservabilityEstate
from observascore.rules.engine import register


def _splunk_dashboards(estate: ObservabilityEstate):
    return [d for d in estate.dashboards if d.source_tool == "splunk"]


def _splunk_alerts(estate: ObservabilityEstate):
    return [a for a in estate.alert_rules if a.source_tool == "splunk"]


@register("SPL-001")
def spl_001_no_indexes(estate: ObservabilityEstate) -> list[dict]:
    if "splunk" not in estate.configured_tools:
        return []

    if estate.summary.splunk_indexes == 0:
        return [{
            "description": (
                "Splunk is configured, but no indexes were discovered. "
                "This usually means log ingestion is not visible, index permissions are missing, "
                "or the Splunk management API cannot read index metadata."
            ),
            "evidence": [],
        }]

    return []


@register("SPL-002")
def spl_002_no_alerts(estate: ObservabilityEstate) -> list[dict]:
    if "splunk" not in estate.configured_tools:
        return []

    alerts = _splunk_alerts(estate)

    if estate.summary.splunk_indexes > 0 and len(alerts) == 0:
        return [{
            "description": (
                "Splunk has log indexes, but no scheduled alerts or alert-like saved searches "
                "were discovered. Critical log patterns may not page or notify responders."
            ),
            "evidence": [],
        }]

    return []


@register("SPL-003")
def spl_003_no_dashboards(estate: ObservabilityEstate) -> list[dict]:
    if "splunk" not in estate.configured_tools:
        return []

    dashboards = _splunk_dashboards(estate)

    if estate.summary.splunk_indexes > 0 and len(dashboards) == 0:
        return [{
            "description": (
                "Splunk has indexed log data, but no dashboards/views were discovered. "
                "Responders may need to manually search logs during incidents instead of using "
                "prepared operational dashboards."
            ),
            "evidence": [],
        }]

    return []


@register("SPL-004")
def spl_004_hec_not_configured(estate: ObservabilityEstate) -> list[dict]:
    if "splunk" not in estate.configured_tools:
        return []

    if not estate.summary.splunk_hec_configured:
        return [{
            "description": (
                "Splunk HTTP Event Collector details were not provided. "
                "Without HEC validation, the assessment cannot confirm structured application "
                "log ingestion through Splunk HEC."
            ),
            "evidence": [],
        }]

    return []