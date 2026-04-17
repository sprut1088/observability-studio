from typing import Optional
from pydantic import BaseModel


class ConnectionSchema(BaseModel):
    """
    Minimal connection descriptor for a single observability tool.
    Used by POST /api/v1/validate and POST /api/v1/crawl.
    """
    tool_name: str              # e.g. "prometheus", "grafana"
    base_url: str               # e.g. "http://host:9090"
    auth_token: Optional[str] = None   # Bearer token or API key


class ConnectionResponse(BaseModel):
    """Result of a connectivity probe."""
    tool_name: str
    reachable: bool
    message: str
    latency_ms: Optional[float] = None
