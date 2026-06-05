"""Parametrized integration tests — one test per JSON fixture.

Every fixture in ``tests/ayosa/evals/fixtures/`` is run end-to-end
through the real ``AyosaAgent`` (with scripted adapters) and scored
against its ``golden`` block. A fixture is considered a regression
indicator: if the agent's behaviour changes such that a previously
passing scenario now fails, the test fails with a structured list of
failure reasons surfacing exactly which dimension regressed.

The fixtures themselves are intentionally small and human-readable so
they double as documentation of "what the agent is supposed to do".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.ayosa.evals.harness import (
    EvalResult,
    discover_fixture_paths,
    load_fixture,
    run_fixture,
)


def _fixture_ids() -> list[str]:
    return [p.stem for p in discover_fixture_paths()]


@pytest.mark.parametrize(
    "fixture_path",
    discover_fixture_paths(),
    ids=_fixture_ids(),
)
def test_eval_fixture(fixture_path: Path) -> None:
    fixture = load_fixture(fixture_path)
    result: EvalResult = run_fixture(fixture)

    # Surface a rich failure message — the assertion error becomes the
    # diff between expected and observed agent behaviour.
    if not result.passed:
        pytest.fail(
            "Eval fixture {name!r} regressed:\n"
            "  mode                       = {mode}\n"
            "  tool_precision             = {p:.2f}\n"
            "  tool_recall                = {r:.2f}\n"
            "  tool_f1                    = {f:.2f}\n"
            "  forbidden_tools_called     = {forb}\n"
            "  keyword_coverage           = {kc:.2f}\n"
            "  iterations                 = {it}\n"
            "  iterations_within_budget   = {ib}\n"
            "  observation_count          = {oc}\n"
            "  meets_min_observations     = {mo}\n"
            "  final_response             = {resp!r}\n"
            "  failure_reasons:\n    - {reasons}\n".format(
                name=fixture.name,
                mode=result.mode,
                p=result.tool_precision,
                r=result.tool_recall,
                f=result.tool_f1,
                forb=result.forbidden_tools_called,
                kc=result.keyword_coverage,
                it=result.iterations,
                ib=result.iterations_within_budget,
                oc=result.observation_count,
                mo=result.meets_min_observations,
                resp=result.final_response[:300],
                reasons="\n    - ".join(result.failure_reasons),
            )
        )
