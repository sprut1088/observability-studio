from accelerators.ayosa.adapters.prometheus import PrometheusAdapter
from accelerators.ayosa.adapters.jaeger import JaegerAdapter
from accelerators.ayosa.adapters.alertmanager import AlertmanagerAdapter
from accelerators.ayosa.adapters.grafana import GrafanaAdapter
from accelerators.ayosa.adapters.splunk import SplunkAdapter
from accelerators.ayosa.adapters.elasticsearch import ElasticsearchAdapter
from accelerators.ayosa.adapters.loki import LokiAdapter
from accelerators.ayosa.adapters.tempo import TempoAdapter
from accelerators.ayosa.adapters.datadog import DatadogAdapter
from accelerators.ayosa.adapters.dynatrace import DynatraceAdapter
from accelerators.ayosa.adapters.appdynamics import AppDynamicsAdapter


ADAPTERS = {
    "prometheus": PrometheusAdapter,
    "grafana": GrafanaAdapter,
    "jaeger": JaegerAdapter,
    "alertmanager": AlertmanagerAdapter,
    "splunk": SplunkAdapter,
    "elasticsearch": ElasticsearchAdapter,
    "loki": LokiAdapter,
    "tempo": TempoAdapter,
    "datadog": DatadogAdapter,
    "dynatrace": DynatraceAdapter,
    "appdynamics": AppDynamicsAdapter,
}