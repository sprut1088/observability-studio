from observascore.adapters import (
    PrometheusAdapter,
    GrafanaAdapter,
    LokiAdapter,
    JaegerAdapter,
    AlertManagerAdapter,
    TempoAdapter,
    ElasticsearchAdapter,
    AppDynamicsAdapter,
    DatadogAdapter,
    DynatraceAdapter,
    SplunkAdapter,
)

ADAPTER_MAP = {
    "prometheus": PrometheusAdapter,
    "grafana": GrafanaAdapter,
    "loki": LokiAdapter,
    "jaeger": JaegerAdapter,
    "alertmanager": AlertManagerAdapter,
    "tempo": TempoAdapter,
    "elasticsearch": ElasticsearchAdapter,
    "appdynamics": AppDynamicsAdapter,
    "datadog": DatadogAdapter,
    "dynatrace": DynatraceAdapter,
    "splunk": SplunkAdapter,
}


def validate_tool(tool: dict) -> tuple[bool, str]:
    name = tool["name"]

    adapter_cls = ADAPTER_MAP.get(name)
    if not adapter_cls:
        return False, f"No adapter found for {name}"

    try:
        adapter = adapter_cls(tool)
        ok = adapter.health_check()
        return (True, "Connection successful") if ok else (False, "Health check failed")
    except Exception as e:
        return False, str(e)