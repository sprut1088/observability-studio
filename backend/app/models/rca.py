"""Pydantic models for the RCA Agent API."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class RCATool(BaseModel):
    """A single observability tool endpoint for RCA signal collection."""
    tool_name: str              # prometheus | grafana | jaeger | opensearch | elasticsearch
    base_url: str               # e.g. http://host:9090
    auth_token: Optional[str] = None


class RCAIncident(BaseModel):
    """Context about the incident under investigation."""
    service: str = "all"                        # primary service (or 'all')
    alert_name: str = "Incident Investigation"  # alert or issue title
    description: str = ""                       # free-text description
    time_window_minutes: int = 15               # look-back window


class RCARequest(BaseModel):
    """POST /api/v1/rca request body."""
    tools: list[RCATool]
    incident: RCAIncident

    # AI provider — "anthropic" (default) | "azure"
    ai_provider: str = "anthropic"

    # Anthropic-specific
    ai_api_key: Optional[str] = None           # Anthropic API key
    ai_model: str = "claude-sonnet-4-6"        # Anthropic model name

    # Azure OpenAI-specific
    azure_endpoint: Optional[str] = None       # https://your-resource.openai.azure.com/
    azure_deployment: Optional[str] = None     # deployment name, e.g. gpt-4o
    azure_api_version: Optional[str] = None    # default: 2024-02-01


class RCAResponse(BaseModel):
    """POST /api/v1/rca response."""
    success: bool
    message: str
    download_url: Optional[str] = None
    run_id: Optional[str] = None
    anomaly_count: int = 0
    firing_alert_count: int = 0
    error_log_count: int = 0
    blast_radius: int = 0
