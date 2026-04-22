from typing import Optional
from pydantic import BaseModel


class AssessmentRequest(BaseModel):
    """
    Simplified assessment descriptor for the Hub tile.
    Used by POST /api/v1/assess.
    """
    tool_source: str            # e.g. "prometheus"
    api_endpoint: str           # base URL of the tool
    auth_token: Optional[str] = None

    # AI branch
    use_ai: bool = False
    ai_provider: Optional[str] = None       # "anthropic" | "azure"
    ai_api_key: Optional[str] = None
    azure_endpoint: Optional[str] = None    # required when ai_provider="azure"
    azure_deployment: Optional[str] = None  # Azure deployment name, e.g. gpt-4o
    azure_api_version: Optional[str] = None # default: 2024-02-01


class AssessmentResponse(BaseModel):
    """Result returned after a completed assessment run."""
    success: bool
    message: str
    preview_url: Optional[str] = None
    download_url: Optional[str] = None
    json_url: Optional[str] = None
    score: Optional[float] = None
