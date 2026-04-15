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
}

def validate_tool(tool: dict) -> tuple[bool, str]:
    name = tool["name"]

    if name == "splunk":
        return False, "Splunk adapter not implemented in current repo MVP"

    adapter_cls = ADAPTER_MAP.get(name)
    if not adapter_cls:
        return False, f"No adapter found for {name}"

    try:
        adapter = adapter_cls(tool)
        ok = adapter.health_check()
        return (True, "Connection successful") if ok else (False, "Tool unreachable")
    except Exception as e:
        return False, str(e)