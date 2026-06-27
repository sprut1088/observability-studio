from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import requests

from jaeger_client import JaegerClient
from models import SignalEvidence

try:
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
except Exception:
    pass


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("name") or tool.get("tool") or tool.get("tool_name") or "").lower()


def _tool_url(tool: dict[str, Any]) -> str:
    return str(tool.get("url") or tool.get("base_url") or tool.get("baseUrl") or "").rstrip("/")


def _find_tool(tools: list[dict[str, Any]], *names: str) -> dict[str, Any] | None:
    wanted = {name.lower() for name in names}

    for tool in tools:
        name = _tool_name(tool)
        if name in wanted or any(wanted_name in name for wanted_name in wanted):
            return tool

    return None


def _auth_token(tool: dict[str, Any] | None) -> str | None:
    if not tool:
        return None
    return (
        tool.get("auth_token")
        or tool.get("authToken")
        or tool.get("api_key")
        or tool.get("token")
        or tool.get("splunk_hec_token")
    )


def _auth_headers(tool: dict[str, Any] | None, splunk: bool = False) -> dict[str, str]:
    token = _auth_token(tool)
    if not token:
        return {}

    if splunk:
        return {"Authorization": f"Splunk {token}"}

    return {"Authorization": f"Bearer {token}"}


def _safe_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 8,
) -> tuple[bool, Any]:
    try:
        resp = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout, verify=False)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return True, resp.json()
        return True, resp.text
    except Exception as exc:
        return False, str(exc)


def _safe_post(
    url: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 12,
) -> tuple[bool, Any]:
    try:
        resp = requests.post(
            url,
            headers=headers or {},
            json=json_body,
            data=data,
            timeout=timeout,
            verify=False,
        )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return True, resp.json()

        return True, resp.text
    except Exception as exc:
        return False, str(exc)


def _warning(source: str, signal_type: str, service: str, title: str, value: str) -> SignalEvidence:
    return SignalEvidence(
        source=source,
        signal_type=signal_type,
        service=service,
        title=title,
        value=value,
        interpretation=(
            f"{source} was configured, but this evidence source could not be queried successfully. "
            "The SLO recommendation can still use other telemetry sources."
        ),
        confidence=0.20,
        raw={"warning": value},
    )


def collect_jaeger_evidence(tools: list[dict[str, Any]], service: str) -> list[SignalEvidence]:
    tool = _find_tool(tools, "jaeger", "tempo")
    if not tool:
        return []

    base = _tool_url(tool)
    if not base:
        return []

    evidence: list[SignalEvidence] = []

    try:
        client = JaegerClient(base)
        operations = client.operations(service)
        traces = client.traces(service, limit=30)

        dependencies: set[str] = set()
        span_count = 0
        error_span_count = 0

        for trace in traces or []:
            processes = trace.get("processes", {}) or {}
            process_services = {
                pid: proc.get("serviceName")
                for pid, proc in processes.items()
                if isinstance(proc, dict) and proc.get("serviceName")
            }

            spans = trace.get("spans", []) or []
            span_count += len(spans)
            span_by_id = {span.get("spanID"): span for span in spans if span.get("spanID")}

            for span in spans:
                tags = span.get("tags", []) or []
                if any(str(tag.get("key", "")).lower() == "error" and str(tag.get("value", "")).lower() == "true" for tag in tags):
                    error_span_count += 1

                current_service = process_services.get(span.get("processID"))
                if not current_service:
                    continue

                for ref in span.get("references", []) or []:
                    parent = span_by_id.get(ref.get("spanID"))
                    if not parent:
                        continue

                    parent_service = process_services.get(parent.get("processID"))
                    if parent_service and parent_service != current_service:
                        dependencies.add(f"{parent_service} -> {current_service}")

        if operations:
            evidence.append(
                SignalEvidence(
                    source="jaeger",
                    signal_type="trace_operations",
                    service=service,
                    title="Trace operations discovered",
                    value=", ".join(operations[:10]),
                    interpretation=(
                        "Trace operations confirm the service handles real request paths and help validate "
                        "operation-level SLO relevance."
                    ),
                    confidence=0.85,
                    raw={"operations": operations[:100]},
                )
            )

        if dependencies:
            evidence.append(
                SignalEvidence(
                    source="jaeger",
                    signal_type="service_map",
                    service=service,
                    title="Service dependency map discovered",
                    value=", ".join(sorted(dependencies)[:12]),
                    interpretation=(
                        "Jaeger service-map evidence identifies upstream/downstream dependencies and blast-radius context."
                    ),
                    confidence=0.85,
                    raw={"dependencies": sorted(dependencies), "span_count": span_count, "error_span_count": error_span_count},
                )
            )

        if span_count:
            evidence.append(
                SignalEvidence(
                    source="jaeger",
                    signal_type="trace_health_context",
                    service=service,
                    title="Trace health context",
                    value=f"sampled_traces={len(traces or [])}, spans={span_count}, error_spans={error_span_count}",
                    interpretation="Trace samples provide supporting context for dependency health and error visibility.",
                    confidence=0.70,
                    raw={"sampled_traces": len(traces or []), "span_count": span_count, "error_span_count": error_span_count},
                )
            )

    except Exception as exc:
        evidence.append(_warning("jaeger", "collection_warning", service, "Jaeger evidence unavailable", str(exc)))

    return evidence


def collect_alertmanager_evidence(tools: list[dict[str, Any]], service: str) -> list[SignalEvidence]:
    tool = _find_tool(tools, "alertmanager")
    if not tool:
        return []

    base = _tool_url(tool)
    if not base:
        return []

    url = urljoin(base + "/", "api/v2/alerts")
    ok, data = _safe_get(url)

    if not ok:
        return [_warning("alertmanager", "collection_warning", service, "Alertmanager evidence unavailable", str(data))]

    if not isinstance(data, list):
        return []

    matching_alerts: list[dict[str, Any]] = []
    slo_alerts: list[dict[str, Any]] = []

    for alert in data:
        labels = alert.get("labels", {}) or {}
        annotations = alert.get("annotations", {}) or {}
        text = json.dumps({"labels": labels, "annotations": annotations}).lower()

        if service.lower() in text:
            matching_alerts.append(alert)

        if service.lower() in text and ("slo" in text or "burn" in text or "errorbudget" in text):
            slo_alerts.append(alert)

    alert_names = [alert.get("labels", {}).get("alertname", "unknown") for alert in matching_alerts[:10]]

    return [
        SignalEvidence(
            source="alertmanager",
            signal_type="alert_context",
            service=service,
            title="Alertmanager alert context",
            value=f"active_matching_alerts={len(matching_alerts)}, slo_burn_alerts={len(slo_alerts)}",
            interpretation=(
                "Alertmanager context shows whether the service currently has active risk or SLO burn alert coverage."
            ),
            confidence=0.80 if matching_alerts else 0.60,
            raw={"active_alert_count": len(matching_alerts), "slo_alert_count": len(slo_alerts), "alert_names": alert_names},
        )
    ]


def collect_grafana_evidence(tools: list[dict[str, Any]], service: str) -> list[SignalEvidence]:
    tool = _find_tool(tools, "grafana")
    if not tool:
        return []

    base = _tool_url(tool)
    if not base:
        return []

    url = urljoin(base + "/", "api/search")
    headers = _auth_headers(tool)
    ok, data = _safe_get(url, headers=headers, params={"query": service})

    if not ok:
        return [_warning("grafana", "collection_warning", service, "Grafana dashboard evidence unavailable", str(data))]

    dashboards = []
    if isinstance(data, list):
        dashboards = [item for item in data if isinstance(item, dict) and (item.get("type") in {"dash-db", "dashboard"} or item.get("uid"))]

    titles = [str(item.get("title") or "untitled") for item in dashboards[:10]]

    return [
        SignalEvidence(
            source="grafana",
            signal_type="dashboard_coverage",
            service=service,
            title="Grafana dashboard coverage",
            value=f"matching_dashboards={len(dashboards)}" + (f": {', '.join(titles[:5])}" if titles else ""),
            interpretation=(
                "Dashboard coverage indicates whether operations teams can observe the recommended SLO after rollout."
            ),
            confidence=0.75 if dashboards else 0.55,
            raw={"dashboard_count": len(dashboards), "dashboards": dashboards[:20]},
        )
    ]


def collect_opensearch_evidence(tools: list[dict[str, Any]], service: str, lookback_days: int) -> list[SignalEvidence]:
    tool = _find_tool(tools, "opensearch", "elasticsearch")
    if not tool:
        return []

    base = _tool_url(tool)
    if not base:
        return []

    query = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {
                        "query_string": {
                            "query": f'"{service}" AND (error OR exception OR timeout OR failed OR failure)',
                            "default_operator": "AND",
                        }
                    }
                ]
            }
        },
    }

    url = urljoin(base + "/", "_search")
    ok, data = _safe_post(url, headers=_auth_headers(tool), json_body=query, timeout=12)

    if not ok:
        return [_warning("opensearch", "collection_warning", service, "OpenSearch log evidence unavailable", str(data))]

    count = 0
    if isinstance(data, dict):
        total = data.get("hits", {}).get("total", 0)
        count = int(total.get("value", 0)) if isinstance(total, dict) else int(total or 0)

    return [
        SignalEvidence(
            source="opensearch",
            signal_type="log_error_context",
            service=service,
            title="OpenSearch error log context",
            value=f"matching_error_events={count}",
            interpretation=(
                f"OpenSearch was searched for service-related error, exception, timeout, and failure signals over {lookback_days} days."
            ),
            confidence=0.70 if count > 0 else 0.55,
            raw={"matching_error_log_events": count},
        )
    ]


def collect_splunk_evidence(tools: list[dict[str, Any]], service: str, lookback_days: int) -> list[SignalEvidence]:
    tool = _find_tool(tools, "splunk")
    if not tool:
        return []

    mgmt_url = str(tool.get("splunk_mgmt_url") or tool.get("mgmt_url") or _tool_url(tool).replace(":8000", ":8089")).rstrip("/")
    if not mgmt_url:
        return []

    token = _auth_token(tool)
    if not token:
        return [
            SignalEvidence(
                source="splunk",
                signal_type="collection_warning",
                service=service,
                title="Splunk token not configured",
                value="Splunk is configured, but no token was provided for search.",
                interpretation="Splunk log context was skipped because no search token was available.",
                confidence=0.20,
                raw={"warning": "missing_splunk_token"},
            )
        ]

    search = (
        f'search earliest=-{int(lookback_days)}d '
        f'("{service}" AND (error OR exception OR timeout OR failed OR failure)) '
        f'| stats count as matching_error_events'
    )

    url = urljoin(mgmt_url + "/", "services/search/jobs/export")
    ok, data = _safe_post(
        url,
        headers=_auth_headers(tool, splunk=True),
        data={"search": search, "output_mode": "json"},
        timeout=20,
    )

    if not ok:
        return [_warning("splunk", "collection_warning", service, "Splunk log evidence unavailable", str(data))]

    count = 0
    text = data if isinstance(data, str) else json.dumps(data)

    for line in text.splitlines():
        try:
            item = json.loads(line)
            result = item.get("result", {})
            if "matching_error_events" in result:
                count = int(float(result["matching_error_events"]))
                break
        except Exception:
            continue

    return [
        SignalEvidence(
            source="splunk",
            signal_type="splunk_error_context",
            service=service,
            title="Splunk error/event context",
            value=f"matching_error_events={count}",
            interpretation="Splunk evidence validates whether observed SLO risk is visible in operational event data.",
            confidence=0.70 if count > 0 else 0.55,
            raw={"matching_error_events": count},
        )
    ]


def collect_multi_source_evidence(
    tools: list[dict[str, Any]],
    service: str,
    lookback_days: int,
) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []

    evidence.extend(collect_jaeger_evidence(tools, service))
    evidence.extend(collect_alertmanager_evidence(tools, service))
    evidence.extend(collect_grafana_evidence(tools, service))
    evidence.extend(collect_opensearch_evidence(tools, service, lookback_days))
    evidence.extend(collect_splunk_evidence(tools, service, lookback_days))

    return evidence


def build_tool_inventory(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()

    purpose = {
        "prometheus": "historical SLI behavior, objectives, traffic, latency, availability, error rate",
        "jaeger": "service map, operations, dependencies, critical path context",
        "tempo": "trace operations and dependency context",
        "alertmanager": "active alerts, SLO burn context, operational risk",
        "grafana": "dashboard coverage and operational readiness",
        "opensearch": "log and error evidence",
        "elasticsearch": "log and error evidence",
        "splunk": "log, event, and error evidence",
        "loki": "log evidence",
    }

    for tool in tools:
        name = _tool_name(tool)
        url = _tool_url(tool)

        if not name or name in seen:
            continue

        seen.add(name)
        inventory.append(
            {
                "name": name,
                "url": url,
                "has_auth": bool(_auth_token(tool)),
                "used_for": purpose.get(name, "supporting observability evidence"),
            }
        )

    return inventory
