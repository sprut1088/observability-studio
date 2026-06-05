"""Unit tests for the eval-harness internals — pure functions only.

These tests should never touch the network, the filesystem outside
``tmp_path``, or the agent's persistence layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from accelerators.ayosa.agent.schemas import (
    AgentResult,
    Observation,
    Plan,
    ToolStep,
)
from tests.ayosa.evals.harness import (
    EvalResult,
    Fixture,
    GoldenAnswer,
    _keyword_coverage,
    _f1,
    _tool_calls_from_result,
    build_scripted_dispatcher,
    discover_fixture_paths,
    iter_fixtures,
    load_fixture,
    run_fixture,
    score_run,
)


# ──────────────────────────────────────────────────────────────────────── #
# Pure scoring helpers
# ──────────────────────────────────────────────────────────────────────── #
class TestKeywordCoverage:
    def test_all_keywords_present_returns_one(self):
        assert _keyword_coverage("p99 latency is 2.3s", ("latency", "2.3")) == 1.0

    def test_case_insensitive_match(self):
        assert _keyword_coverage("Latency hit 2.3s", ("latency",)) == 1.0

    def test_partial_match_fraction(self):
        coverage = _keyword_coverage("latency only", ("latency", "missing"))
        assert coverage == pytest.approx(0.5)

    def test_empty_keywords_returns_one(self):
        # No expectations = trivially satisfied — must NEVER fail closed.
        assert _keyword_coverage("anything", ()) == 1.0

    def test_blank_text_with_keywords_returns_zero(self):
        assert _keyword_coverage("", ("latency",)) == 0.0


class TestF1:
    def test_perfect(self):
        assert _f1(1.0, 1.0) == 1.0

    def test_zero_both(self):
        assert _f1(0.0, 0.0) == 0.0

    def test_harmonic_mean(self):
        assert _f1(0.5, 1.0) == pytest.approx(2.0 * 0.5 * 1.0 / 1.5)


class TestToolCallsFromResult:
    def _make_result(self, steps):
        return AgentResult(
            intent="x",
            plan=Plan(intent="x", service=None, time_range="15m"),
            tool_steps=steps,
            observations=[],
            reflections=[],
            final_response="",
            confidence=0.0,
        )

    def test_excludes_skipped_and_pending(self):
        steps = [
            ToolStep(index=0, tool="prometheus", label="prom", status="done"),
            ToolStep(index=1, tool="loki", label="loki", status="skipped"),
            ToolStep(index=2, tool="jaeger", label="jaeger", status="pending"),
            ToolStep(index=3, tool="alertmanager", label="am", status="error"),
        ]
        assert _tool_calls_from_result(self._make_result(steps)) == [
            "prometheus",
            "alertmanager",
        ]

    def test_lowercases_tool_names(self):
        steps = [ToolStep(index=0, tool="Prometheus", label="x", status="done")]
        assert _tool_calls_from_result(self._make_result(steps)) == ["prometheus"]


# ──────────────────────────────────────────────────────────────────────── #
# score_run() end-to-end behaviour
# ──────────────────────────────────────────────────────────────────────── #
def _result(
    *,
    tool_steps=None,
    observations=None,
    final_response="",
    iterations=1,
) -> AgentResult:
    r = AgentResult(
        intent="x",
        plan=Plan(intent="x", service=None, time_range="15m"),
        tool_steps=tool_steps or [],
        observations=observations or [],
        reflections=[],
        final_response=final_response,
        confidence=0.0,
    )
    r.iterations = iterations
    return r


class TestScoreRun:
    def test_perfect_run_passes(self):
        result = _result(
            tool_steps=[ToolStep(index=0, tool="prometheus", label="x", status="done")],
            observations=[Observation(source="prometheus", signal="metrics", finding="p99=2.3s", status="ok")],
            final_response="p99 latency is 2.3s",
            iterations=1,
        )
        golden = GoldenAnswer(
            expected_tools=("prometheus",),
            expected_keywords=("latency", "2.3"),
            max_iterations=2,
            min_observations=1,
        )
        score = score_run(result, golden)
        assert score.passed is True
        assert score.tool_precision == 1.0
        assert score.tool_recall == 1.0
        assert score.tool_f1 == 1.0
        assert score.keyword_coverage == 1.0
        assert score.failure_reasons == ()

    def test_missing_expected_tool_fails(self):
        result = _result(
            tool_steps=[ToolStep(index=0, tool="loki", label="x", status="done")],
            observations=[Observation(source="loki", signal="logs", finding="x", status="ok")],
            final_response="found a log",
            iterations=1,
        )
        golden = GoldenAnswer(
            expected_tools=("prometheus",),
            expected_keywords=(),
            min_observations=1,
        )
        score = score_run(result, golden)
        assert score.passed is False
        assert score.tool_recall == 0.0
        assert any("missed expected tools" in r for r in score.failure_reasons)

    def test_forbidden_tool_called_fails(self):
        result = _result(
            tool_steps=[
                ToolStep(index=0, tool="prometheus", label="x", status="done"),
                ToolStep(index=1, tool="splunk", label="x", status="done"),
            ],
            observations=[
                Observation(source="prometheus", signal="metrics", finding="ok", status="ok"),
                Observation(source="splunk", signal="logs", finding="ok", status="ok"),
            ],
            final_response="ok",
            iterations=1,
        )
        golden = GoldenAnswer(
            expected_tools=("prometheus",),
            forbidden_tools=("splunk",),
            expected_keywords=(),
            min_observations=1,
        )
        score = score_run(result, golden)
        assert score.passed is False
        assert score.forbidden_tools_called is True
        assert any("forbidden" in r for r in score.failure_reasons)

    def test_iteration_budget_breach_fails(self):
        result = _result(
            tool_steps=[ToolStep(index=0, tool="prometheus", label="x", status="done")],
            observations=[Observation(source="prometheus", signal="metrics", finding="x", status="ok")],
            final_response="ok",
            iterations=5,
        )
        golden = GoldenAnswer(
            expected_tools=("prometheus",),
            expected_keywords=(),
            max_iterations=2,
            min_observations=1,
        )
        score = score_run(result, golden)
        assert score.passed is False
        assert score.iterations_within_budget is False

    def test_missing_keywords_fail_only_when_below_threshold(self):
        # 0.5 coverage at default threshold 0.5 → passes
        result = _result(
            tool_steps=[ToolStep(index=0, tool="prometheus", label="x", status="done")],
            observations=[Observation(source="prometheus", signal="metrics", finding="x", status="ok")],
            final_response="payments only",
            iterations=1,
        )
        golden = GoldenAnswer(
            expected_tools=("prometheus",),
            expected_keywords=("payments", "missing"),
            min_observations=1,
            min_keyword_coverage=0.5,
        )
        score = score_run(result, golden)
        assert score.keyword_coverage == 0.5
        assert score.passed is True

        # Tightening threshold causes failure
        golden_strict = GoldenAnswer(
            expected_tools=("prometheus",),
            expected_keywords=("payments", "missing"),
            min_observations=1,
            min_keyword_coverage=0.75,
        )
        score_strict = score_run(result, golden_strict)
        assert score_strict.passed is False
        assert any("keyword coverage" in r for r in score_strict.failure_reasons)

    def test_no_expected_tools_means_precision_and_recall_one(self):
        result = _result(
            tool_steps=[],
            observations=[],
            final_response="just chatting",
            iterations=1,
        )
        golden = GoldenAnswer(
            expected_tools=(),
            expected_keywords=(),
            min_observations=0,
        )
        score = score_run(result, golden)
        assert score.tool_precision == 1.0
        assert score.tool_recall == 1.0
        assert score.passed is True


# ──────────────────────────────────────────────────────────────────────── #
# Scripted dispatcher
# ──────────────────────────────────────────────────────────────────────── #
class TestScriptedDispatcher:
    def test_returns_canned_observations(self):
        from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

        dispatcher = build_scripted_dispatcher({
            "prometheus": [
                {"source": "prometheus", "signal": "metrics", "finding": "x", "status": "ok"},
            ],
        })
        plan = Plan(
            intent="latency_issues",
            service="payments",
            time_range="15m",
            required_signals=["metrics"],
            selected_tools=["prometheus"],
        )
        agent_input = AgentInput(
            message="why slow?",
            service="payments",
            time_range="15m",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://x")],
            llm=None,
        )
        steps, observations = dispatcher.dispatch(plan, agent_input)
        assert len(observations) == 1
        assert observations[0].source == "prometheus"
        assert observations[0].finding == "x"
        assert any(s.tool == "prometheus" and s.status == "done" for s in steps)

    def test_unknown_tool_returns_error_observation(self):
        from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

        dispatcher = build_scripted_dispatcher({})  # no scripts
        plan = Plan(
            intent="latency_issues",
            service="payments",
            time_range="15m",
            required_signals=["metrics"],
            selected_tools=["prometheus"],
        )
        agent_input = AgentInput(
            message="why slow?",
            service="payments",
            time_range="15m",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://x")],
            llm=None,
        )
        _steps, observations = dispatcher.dispatch(plan, agent_input)
        # Dispatcher surfaces a single error observation from the "no adapter" branch.
        assert any(o.status == "error" for o in observations)


# ──────────────────────────────────────────────────────────────────────── #
# Fixture loader
# ──────────────────────────────────────────────────────────────────────── #
class TestFixtureLoader:
    def test_round_trip_minimal_fixture(self, tmp_path: Path):
        payload = {
            "name": "smoke",
            "description": "smoke test",
            "mode": "deterministic",
            "input": {
                "message": "are any alerts firing?",
                "service": "payments",
                "time_range": "15m",
                "tools": [{"tool": "alertmanager", "base_url": "http://am"}],
            },
            "scripted_adapters": {
                "alertmanager": [
                    {"source": "alertmanager", "signal": "alerts", "finding": "ok", "status": "ok"}
                ]
            },
            "golden": {
                "expected_tools": ["alertmanager"],
                "expected_keywords": ["ok"],
            },
        }
        path = tmp_path / "smoke.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        fix = load_fixture(path)
        assert fix.name == "smoke"
        assert fix.mode == "deterministic"
        assert fix.agent_input.message == "are any alerts firing?"
        assert fix.agent_input.llm is None  # harness rule
        assert "alertmanager" in fix.scripted_adapters
        assert fix.golden.expected_tools == ("alertmanager",)
        assert fix.golden.expected_keywords == ("ok",)
        assert fix.source_path == path

    def test_missing_name_raises(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"description": "x"}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'name'"):
            load_fixture(path)

    def test_invalid_mode_raises(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({
            "name": "x",
            "mode": "magic",
            "input": {"message": "x", "tools": []},
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown mode"):
            load_fixture(path)

    def test_tool_use_mode_requires_scripted_llm(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({
            "name": "x",
            "mode": "tool_use",
            "input": {"message": "x", "tools": []},
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="scripted_llm"):
            load_fixture(path)

    def test_discover_fixtures_finds_seed_set(self):
        # The seed fixture directory ships with this repo — at least one file.
        paths = discover_fixture_paths()
        assert len(paths) >= 1
        assert all(p.suffix == ".json" for p in paths)
        # Loader works for every shipped fixture without raising.
        for fix in iter_fixtures():
            assert fix.name
            assert fix.agent_input.message
            assert fix.agent_input.llm is None  # never reach LLM in harness


# ──────────────────────────────────────────────────────────────────────── #
# run_fixture — smoke
# ──────────────────────────────────────────────────────────────────────── #
class TestRunFixtureSmoke:
    """Sanity-check the full pipeline on a one-tool fixture built inline."""

    def test_deterministic_run_passes(self, tmp_path: Path):
        payload = {
            "name": "inline_smoke",
            "description": "inline-built fixture",
            "mode": "deterministic",
            "input": {
                "message": "what is p99 latency on payments?",
                "service": "payments",
                "time_range": "15m",
                "tools": [{"tool": "prometheus", "base_url": "http://prom"}],
            },
            "scripted_adapters": {
                "prometheus": [
                    {
                        "source": "prometheus",
                        "signal": "metrics",
                        "finding": "p99 latency = 2.3s",
                        "status": "ok",
                    }
                ]
            },
            "golden": {
                "expected_tools": ["prometheus"],
                "expected_keywords": ["latency", "2.3"],
                "max_iterations": 2,
                "min_observations": 1,
            },
        }
        path = tmp_path / "inline_smoke.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        fixture = load_fixture(path)
        result = run_fixture(fixture)
        assert isinstance(result, EvalResult)
        assert result.fixture_name == "inline_smoke"
        assert result.mode == "deterministic"
        assert result.passed is True, result.failure_reasons
        assert result.tool_recall == 1.0
        assert result.observation_count >= 1
        assert result.iterations >= 1
