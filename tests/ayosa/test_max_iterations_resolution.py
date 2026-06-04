"""Step 18 — per-request ``max_iterations`` override + LLM-conditional default.

Verifies the resolver applies the right precedence (explicit override
beats LLM default beats no-LLM default), clamps out-of-range values
to ``[1, 8]``, and tolerates non-int garbage by falling back to the
single-pass floor. Also asserts the resolved cap is surfaced on
``AyosaChatResponse`` as ``max_iterations`` so the UI can render
"Pass N of up to M" badges.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from accelerators.ayosa.agent_bridge import resolve_max_iterations
from accelerators.ayosa.models import AyosaChatRequest, AyosaToolConfig


def _req(**kwargs) -> SimpleNamespace:
    base = {
        "message": "x",
        "tools": [],
        "ai": None,
        "max_iterations": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_default_without_llm_is_single_pass():
    assert resolve_max_iterations(_req()) == 1


def test_default_with_llm_enabled_bumps_to_four():
    ai = SimpleNamespace(enabled=True)
    assert resolve_max_iterations(_req(ai=ai)) == 4


def test_default_with_llm_disabled_stays_single_pass():
    ai = SimpleNamespace(enabled=False)
    assert resolve_max_iterations(_req(ai=ai)) == 1


@pytest.mark.parametrize("override,expected", [
    (1, 1),
    (2, 2),
    (5, 5),
    (8, 8),
])
def test_explicit_override_in_range_wins(override, expected):
    ai = SimpleNamespace(enabled=True)  # explicit override beats LLM default
    assert resolve_max_iterations(_req(ai=ai, max_iterations=override)) == expected


@pytest.mark.parametrize("override,expected", [
    (0, 1),
    (-3, 1),
    (9, 8),
    (1000, 8),
])
def test_explicit_override_clamped_to_floor_and_ceiling(override, expected):
    assert resolve_max_iterations(_req(max_iterations=override)) == expected


def test_garbage_override_falls_back_to_floor():
    assert resolve_max_iterations(_req(max_iterations="not-a-number")) == 1


def test_chat_request_model_accepts_max_iterations_field():
    req = AyosaChatRequest(
        message="hi",
        tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
        max_iterations=3,
    )
    assert req.max_iterations == 3
    assert resolve_max_iterations(req) == 3


def test_chat_request_model_default_max_iterations_is_none():
    req = AyosaChatRequest(
        message="hi",
        tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
    )
    assert req.max_iterations is None
