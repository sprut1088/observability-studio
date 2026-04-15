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
    url: str
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

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
    download_url: Optional[str] = None