from typing import Optional
from pydantic import BaseModel


class ConnectionSchema(BaseModel):
    tool_name: str
    base_url: str
    auth_token: Optional[str] = None

    # generic basic auth
    username: Optional[str] = None
    password: Optional[str] = None

    # splunk-specific
    splunk_base_url: Optional[str] = None
    splunk_mgmt_url: Optional[str] = None
    splunk_hec_url: Optional[str] = None
    splunk_hec_token: Optional[str] = None
    splunk_app: Optional[str] = "search"
    splunk_verify_ssl: bool = False


class ConnectionResponse(BaseModel):
    """Result of a connectivity probe."""
    tool_name: str
    reachable: bool
    message: str
    latency_ms: Optional[float] = None
