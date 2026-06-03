"""Tests for the LLM intent router + smart classifier.

All LLM calls are mocked — these tests never hit the network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from accelerators.ayosa.agent.intent_classifier import (
    classify_intent,
    classify_intent_smart,
)
from accelerators.ayosa.agent.intent_router_llm import (
    CANONICAL_INTENTS,
    RouterResult,
    route_intent_llm,
)


# --------------------------------------------------------------------------- #
# Keyword classifier stays unchanged (regression guard)
# --------------------------------------------------------------------------- #
def test_classify_intent_keyword_unchanged():
    assert classify_intent("what time is it") == "current_time"
    assert classify_intent("any alerts firing right now") == "active_alerts"
    assert classify_intent("show me the latest error") == "latest_error"
    assert classify_intent("hello world") == "general_observability_question"


# --------------------------------------------------------------------------- #
# Smart classifier: no LLM config → keyword fallback
# --------------------------------------------------------------------------- #
def test_smart_no_llm_config_uses_keyword():
    intent, meta = classify_intent_smart("show me firing alerts", llm_config=None)
    assert intent == "active_alerts"
    assert meta["source"] == "keyword"


def test_smart_llm_disabled_uses_keyword():
    cfg = {"enabled": False, "provider": "anthropic", "api_key": "x"}
    intent, meta = classify_intent_smart("show me firing alerts", llm_config=cfg)
    assert intent == "active_alerts"
    assert meta["source"] == "keyword"


# --------------------------------------------------------------------------- #
# Fast-path intents bypass the LLM
# --------------------------------------------------------------------------- #
def test_smart_fast_path_skips_llm_for_current_time():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "x"}
    with patch(
        "accelerators.ayosa.agent.intent_classifier.route_intent_llm"
    ) as mock_router:
        intent, meta = classify_intent_smart("what time is it", llm_config=cfg)
    assert intent == "current_time"
    assert meta["source"] == "fast_path"
    mock_router.assert_not_called()


def test_smart_fast_path_skips_llm_for_stability_ranking():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "x"}
    with patch(
        "accelerators.ayosa.agent.intent_classifier.route_intent_llm"
    ) as mock_router:
        intent, meta = classify_intent_smart(
            "services with less than 0.5% errors", llm_config=cfg
        )
    assert intent == "service_stability_ranking"
    assert meta["source"] == "fast_path"
    mock_router.assert_not_called()


# --------------------------------------------------------------------------- #
# LLM success path
# --------------------------------------------------------------------------- #
def test_smart_llm_success_returns_llm_intent():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "x"}
    fake = RouterResult(
        intent="latency_issues",
        confidence=0.92,
        service="payments",
        time_range="1h",
        reasoning="user explicitly mentioned p99",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    with patch(
        "accelerators.ayosa.agent.intent_classifier.route_intent_llm",
        return_value=fake,
    ):
        intent, meta = classify_intent_smart(
            "is the payments service feeling sluggish today",
            llm_config=cfg,
        )
    assert intent == "latency_issues"
    assert meta["source"] == "llm"
    assert meta["confidence"] == pytest.approx(0.92)
    assert meta["llm_reasoning"] == "user explicitly mentioned p99"
    assert meta["service_hint"] == "payments"
    assert meta["time_hint"] == "1h"


# --------------------------------------------------------------------------- #
# LLM failure → keyword fallback (marked as llm_fallback)
# --------------------------------------------------------------------------- #
def test_smart_llm_failure_falls_back_to_keyword():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "x"}
    with patch(
        "accelerators.ayosa.agent.intent_classifier.route_intent_llm",
        return_value=None,
    ):
        intent, meta = classify_intent_smart(
            "any firing alerts", llm_config=cfg
        )
    assert intent == "active_alerts"
    assert meta["source"] == "llm_fallback"


# --------------------------------------------------------------------------- #
# route_intent_llm: provider call mocked at SDK level
# --------------------------------------------------------------------------- #
class _FakeAnthropicResponse:
    def __init__(self, text: str):
        self.content = [type("Block", (), {"text": text})()]


class _FakeAnthropicClient:
    def __init__(self, text: str):
        self._text = text
        self.messages = self

    def create(self, **kwargs):  # noqa: ARG002
        return _FakeAnthropicResponse(self._text)


def _patch_anthropic(text: str):
    fake_module = type(
        "anthropic",
        (),
        {"Anthropic": lambda **kw: _FakeAnthropicClient(text)},
    )
    return patch.dict("sys.modules", {"anthropic": fake_module})


def test_route_intent_llm_returns_canonical_intent():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "k"}
    payload = (
        '{"intent": "latency_issues", "confidence": 0.81, '
        '"service": "checkout", "time_range": "15m", '
        '"reasoning": "p95 mentioned"}'
    )
    with _patch_anthropic(payload):
        result = route_intent_llm("checkout slow in last 15m", cfg)
    assert result is not None
    assert result.intent == "latency_issues"
    assert result.confidence == pytest.approx(0.81)
    assert result.service == "checkout"
    assert result.time_range == "15m"


def test_route_intent_llm_non_canonical_intent_returns_none():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "k"}
    payload = '{"intent": "telepathy", "confidence": 0.9, "reasoning": "x"}'
    with _patch_anthropic(payload):
        result = route_intent_llm("anything", cfg)
    assert result is None


def test_route_intent_llm_garbage_payload_returns_none():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "k"}
    with _patch_anthropic("not json at all"):
        result = route_intent_llm("anything", cfg)
    assert result is None


def test_route_intent_llm_disabled_returns_none():
    cfg = {"enabled": False, "provider": "anthropic", "api_key": "k"}
    result = route_intent_llm("anything", cfg)
    assert result is None


def test_route_intent_llm_handles_markdown_fences():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "k"}
    payload = (
        "```json\n"
        '{"intent": "active_alerts", "confidence": 0.7, "reasoning": "ok"}\n'
        "```"
    )
    with _patch_anthropic(payload):
        result = route_intent_llm("anything", cfg)
    assert result is not None
    assert result.intent == "active_alerts"


def test_route_intent_llm_provider_exception_returns_none():
    cfg = {"enabled": True, "provider": "anthropic", "api_key": "k"}

    class _Boom:
        def __init__(self, **kw):  # noqa: ARG002
            raise RuntimeError("network down")

    fake_module = type("anthropic", (), {"Anthropic": _Boom})
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        result = route_intent_llm("anything", cfg)
    assert result is None


# --------------------------------------------------------------------------- #
# Planner honours intent_override end-to-end
# --------------------------------------------------------------------------- #
def test_planner_uses_intent_override():
    from accelerators.ayosa.agent.planner import Planner
    from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

    planner = Planner()
    agent_input = AgentInput(
        message="checkout looks weird",  # would keyword-classify as general
        service="checkout",
        time_range="30m",
        tools=[AgentToolConfig(tool="prometheus", base_url="http://x")],
    )
    plan = planner.build(agent_input, intent_hint="latency_issues")
    assert plan.intent == "latency_issues"
    # latency_issues requires metrics + traces — prometheus covers metrics
    assert "metrics" in plan.required_signals
    assert "prometheus" in plan.selected_tools
