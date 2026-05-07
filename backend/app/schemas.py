from typing import List, Optional, Literal
from pydantic import BaseModel, HttpUrl, Field

SystemName = Literal[
    "prometheus",
    "grafana",
    "loki",
    "jaeger",
    "alertmanager",
    "tempo",
    "elasticsearch",
    "appdynamics",
    "datadog",
    "dynatrace",
    "splunk"
]

UsageType = Literal["metrics", "traces", "logs", "dashboards", "alerts"]

class ToolConfig(BaseModel):
    name: SystemName
    enabled: bool = True
    usages: List[UsageType] = Field(default_factory=list)

    #generic
    url: str
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    #splunk-specific
    splunk_base_url: Optional[str] = None      # ex: http://10.235.21.132:8000
    splunk_mgmt_url: Optional[str] = None      # ex: https://10.235.21.132:8089
    splunk_hec_url: Optional[str] = None       # ex: http://10.235.21.132:8088
    splunk_hec_token: Optional[str] = None
    splunk_app: Optional[str] = None           # ex: search
    splunk_verify_ssl: bool = False

class AIConfig(BaseModel):
    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    azure_api_key: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None

class ClientConfig(BaseModel):
    name: str = "MVP Client"
    environment: str = "dev"

class RunRequest(BaseModel):
    client: ClientConfig
    tools: List[ToolConfig]
    ai: Optional[AIConfig] = None

class ValidationResponse(BaseModel):
    system: str
    reachable: bool
    message: str

class RunResponse(BaseModel):
    success: bool
    message: str
    preview_url: Optional[str] = None
    download_url: Optional[str] = None
    json_url: Optional[str] = None
