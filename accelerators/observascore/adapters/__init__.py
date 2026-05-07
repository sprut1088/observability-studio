"""Read-only adapters for observability tools."""
from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.adapters.prometheus import PrometheusAdapter
from observascore.adapters.grafana import GrafanaAdapter
from observascore.adapters.loki import LokiAdapter
from observascore.adapters.jaeger import JaegerAdapter
from observascore.adapters.alertmanager import AlertManagerAdapter
from observascore.adapters.tempo import TempoAdapter
from observascore.adapters.elasticsearch import ElasticsearchAdapter
from observascore.adapters.otel_collector import OtelCollectorAdapter
from observascore.adapters.appdynamics import AppDynamicsAdapter
from observascore.adapters.datadog import DatadogAdapter
from observascore.adapters.dynatrace import DynatraceAdapter
from observascore.adapters.splunk_adapter import SplunkAdapter

__all__ = [
    "BaseAdapter",
    "AdapterError",
    # Open-source stack
    "PrometheusAdapter",
    "GrafanaAdapter",
    "LokiAdapter",
    "JaegerAdapter",
    "AlertManagerAdapter",
    "TempoAdapter",
    "ElasticsearchAdapter",
    "OtelCollectorAdapter",
    # Commercial APM / observability platforms
    "AppDynamicsAdapter",
    "DatadogAdapter",
    "DynatraceAdapter",
    "SplunkAdapter",
]