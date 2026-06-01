"""Tests for the ObsCo service layer end-to-end behaviour.

Validates the contract of `answer_question` for the three core
scenarios:
  1. External tool questions (legacy path — must still work).
  2. Observability Studio meta-questions (new — accelerator facts).
  3. Mixed questions that touch both layers.

Plus regression guards: never fabricate (no module → graceful fallback),
backward-compatible response shape, and best-effort LLM enhancement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import pytest

from accelerators.obsco import service as obsco_service
from accelerators.obsco.service import answer_question


# ──────────────────────────────────────────────────────────────────────── #
# Minimal Pydantic-shaped stand-ins (avoid importing the FastAPI app)
# ──────────────────────────────────────────────────────────────────────── #
@dataclass
class _AI:
    enabled: bool = False
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


@dataclass
class _Tool:
    tool: str
    base_url: Optional[str] = None
    auth_token: Optional[str] = None


@dataclass
class _Req:
    message: str
    tools: list[_Tool] = field(default_factory=list)
    ai: Optional[_AI] = None


def run(req: _Req) -> dict:
    return asyncio.run(answer_question(req))


# ──────────────────────────────────────────────────────────────────────── #
# Response shape — backwards compatibility
# ──────────────────────────────────────────────────────────────────────── #
class TestResponseShape:
    REQUIRED = {
        "answer", "mentioned_tools", "configured_tools",
        "tool_facts", "mentioned_accelerators",
        "studio_facts", "ai_used",
    }

    def test_all_fields_present(self) -> None:
        res = run(_Req(message="hi"))
        assert set(res.keys()) == self.REQUIRED

    def test_legacy_fields_still_typed_correctly(self) -> None:
        res = run(_Req(message="how do I query prometheus?"))
        assert isinstance(res["mentioned_tools"], list)
        assert isinstance(res["configured_tools"], list)
        assert isinstance(res["tool_facts"], dict)
        assert isinstance(res["ai_used"], bool)


# ──────────────────────────────────────────────────────────────────────── #
# Path 1 — external tool only (legacy behaviour preserved)
# ──────────────────────────────────────────────────────────────────────── #
class TestExternalToolPath:
    def test_prometheus_question_still_works(self) -> None:
        res = run(_Req(message="What endpoints does Prometheus expose?"))
        assert "prometheus" in res["mentioned_tools"]
        assert "prometheus" in res["tool_facts"]
        assert "Prometheus" in res["answer"]
        # studio side empty when only a tool is mentioned
        assert res["mentioned_accelerators"] == []
        assert res["studio_facts"] == {}

    def test_configured_tool_used_when_nothing_mentioned(self) -> None:
        # No tool/module mention, but a tool is configured.
        res = run(_Req(
            message="give me a primer",
            tools=[_Tool(tool="grafana", base_url="http://x")],
        ))
        assert "grafana" in res["configured_tools"]
        assert "grafana" in res["tool_facts"]


# ──────────────────────────────────────────────────────────────────────── #
# Path 2 — Observability Studio meta-questions
# ──────────────────────────────────────────────────────────────────────── #
class TestStudioPath:
    def test_observascore_how_it_works(self) -> None:
        res = run(_Req(message="How does ObservaScore work?"))
        assert "observascore" in res["mentioned_accelerators"]
        assert "observascore" in res["studio_facts"]
        assert "ObservaScore" in res["answer"]
        assert "How it works" in res["answer"]

    def test_obscrawl_outputs(self) -> None:
        res = run(_Req(message="What does ObsCrawl output?"))
        assert "obscrawl" in res["mentioned_accelerators"]
        assert "Outputs" in res["answer"]

    def test_ayosa_history(self) -> None:
        res = run(_Req(message="How do I compare AYOSA runs?"))
        assert "ayosa" in res["mentioned_accelerators"]
        # history maps to outputs renderer; "Outputs" or "Run history"
        # heading should appear.
        assert (
            "Run history" in res["answer"]
            or "Outputs" in res["answer"]
        )

    def test_rca_inputs(self) -> None:
        res = run(_Req(message="What inputs does the RCA agent require?"))
        assert "rca_agent" in res["mentioned_accelerators"]
        assert "Inputs" in res["answer"]

    def test_studio_only_question_does_not_dump_tool_facts(self) -> None:
        res = run(_Req(
            message="How does ObservaScore work?",
            tools=[_Tool(tool="prometheus")],
        ))
        # When a studio module is mentioned, we don't fall back to the
        # configured-tool dump.
        assert res["tool_facts"] == {}


# ──────────────────────────────────────────────────────────────────────── #
# Path 3 — mixed questions
# ──────────────────────────────────────────────────────────────────────── #
class TestMixedPath:
    def test_mixed_question_includes_both_layers(self) -> None:
        res = run(_Req(
            message="How does ObservaScore assess Prometheus targets?",
        ))
        assert "observascore" in res["studio_facts"]
        assert "prometheus" in res["tool_facts"]
        assert "ObservaScore" in res["answer"]
        assert "Prometheus" in res["answer"]


# ──────────────────────────────────────────────────────────────────────── #
# Fallbacks & anti-fabrication
# ──────────────────────────────────────────────────────────────────────── #
class TestFallbacks:
    def test_no_match_returns_friendly_intro(self) -> None:
        res = run(_Req(message="What's the weather like today?"))
        assert res["tool_facts"] == {}
        assert res["studio_facts"] == {}
        assert "ObsCo" in res["answer"]
        # mentions some real accelerator names
        assert "ObservaScore" in res["answer"]

    def test_unknown_accelerator_does_not_fabricate(self) -> None:
        # No alias matches → no studio_facts emitted, answer falls back.
        res = run(_Req(message="How does FakeAccelerator9000 work?"))
        assert res["mentioned_accelerators"] == []
        assert res["studio_facts"] == {}

    def test_empty_message_returns_intro(self) -> None:
        res = run(_Req(message=""))
        assert "ObsCo" in res["answer"]


# ──────────────────────────────────────────────────────────────────────── #
# LLM enhancement (best-effort, isolated by monkeypatch)
# ──────────────────────────────────────────────────────────────────────── #
class TestLLMEnhancement:
    def test_llm_disabled_when_ai_off(self) -> None:
        res = run(_Req(
            message="How does ObservaScore work?",
            ai=_AI(enabled=False, api_key="sk-test"),
        ))
        assert res["ai_used"] is False

    def test_llm_disabled_without_key(self) -> None:
        res = run(_Req(
            message="How does ObservaScore work?",
            ai=_AI(enabled=True, api_key=None),
        ))
        assert res["ai_used"] is False

    def test_llm_skipped_when_nothing_matched(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"n": 0}

        def _fake(*a, **k):
            called["n"] += 1
            return "should not be called"

        monkeypatch.setattr(obsco_service, "_try_llm_enhance", _fake)
        res = run(_Req(
            message="random question with no tool or module",
            ai=_AI(enabled=True, api_key="sk-test"),
        ))
        assert called["n"] == 0
        assert res["ai_used"] is False

    def test_llm_invoked_for_studio_question(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen = {}

        def _fake(message, grounding, api_key, model):
            seen["message"] = message
            seen["grounding"] = grounding
            seen["key"] = api_key
            seen["model"] = model
            return "ENHANCED ANSWER"

        monkeypatch.setattr(obsco_service, "_try_llm_enhance", _fake)
        res = run(_Req(
            message="How does ObservaScore work?",
            ai=_AI(enabled=True, api_key="sk-test", model="claude-x"),
        ))
        assert res["ai_used"] is True
        assert res["answer"] == "ENHANCED ANSWER"
        assert "ObservaScore" in seen["grounding"]
        assert "Observability Studio platform facts" in seen["grounding"]
        assert seen["model"] == "claude-x"

    def test_llm_grounding_includes_both_layers_when_mixed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen = {}

        def _fake(message, grounding, api_key, model):
            seen["grounding"] = grounding
            return "OK"

        monkeypatch.setattr(obsco_service, "_try_llm_enhance", _fake)
        run(_Req(
            message="How does ObservaScore assess Prometheus?",
            ai=_AI(enabled=True, api_key="sk-test"),
        ))
        assert "Observability Studio platform facts" in seen["grounding"]
        assert "External tool facts" in seen["grounding"]

    def test_llm_failure_falls_back_to_local(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            obsco_service, "_try_llm_enhance",
            lambda *a, **k: None,
        )
        res = run(_Req(
            message="How does ObservaScore work?",
            ai=_AI(enabled=True, api_key="sk-test"),
        ))
        assert res["ai_used"] is False
        assert "ObservaScore" in res["answer"]
