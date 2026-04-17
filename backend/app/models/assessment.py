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
    ai_provider: Optional[str] = None   # "anthropic" | "azure"
    ai_api_key: Optional[str] = None


class AssessmentResponse(BaseModel):
    """Result returned after a completed assessment run."""
    success: bool
    message: str
    download_url: Optional[str] = None
    score: Optional[float] = None
