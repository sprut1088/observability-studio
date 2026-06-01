"""Pydantic v2 schemas for the ObsCo (Observability Copilot) chat endpoint."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ObsCoTool(BaseModel):
    tool: str
    base_url: Optional[str] = None
    auth_token: Optional[str] = None


class ObsCoAIConfig(BaseModel):
    enabled: bool = False
    provider: Optional[str] = None  # currently only "anthropic" supported
    api_key: Optional[str] = None
    model: Optional[str] = None


class ObsCoChatRequest(BaseModel):
    message: str
    tools: list[ObsCoTool] = []
    ai: Optional[ObsCoAIConfig] = None


class ObsCoChatResponse(BaseModel):
    answer: str
    mentioned_tools: list[str] = []
    configured_tools: list[str] = []
    tool_facts: dict = {}
    ai_used: bool = False
