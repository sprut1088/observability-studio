"""CLI runner for the AYOSA eval harness.

Loads every JSON fixture in ``tests/ayosa/evals/fixtures/``, runs each
through the agent (with scripted adapters — no network, no LLM), and
writes a CSV report to ``runtime/eval_report.csv``. Exits non-zero if
any fixture fails so the script can gate CI when desired.

Usage::

    python scripts/run_evals.py
    python scripts/run_evals.py --output runtime/my_report.csv
    python scripts/run_evals.py --fixtures-dir tests/ayosa/evals/fixtures
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

# Add repo root to sys.path so 'tests.ayosa.evals.harness' resolves
# when the script is invoked as ``python scripts/run_evals.py``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.ayosa.evals.harness import (  # noqa: E402  — path setup must precede import
    EvalResult,
    FIXTURES_DIR,
    discover_fixture_paths,
    load_fixture,
    run_fixture,
)

logger = logging.getLogger("ayosa.evals")


CSV_COLUMNS = [
    "fixture",
    "mode",
    "passed",
    "tool_precision",
    "tool_recall",
    "tool_f1",
    "forbidden_tools_called",
    "keyword_coverage",
    "iterations",
    "iterations_within_budget",
    "observation_count",
    "meets_min_observations",
    "failure_reasons",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=FIXTURES_DIR,
        help="Directory containing JSON fixture files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "runtime" / "eval_report.csv",
        help="CSV report destination.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-fixture stdout lines.",
    )
    return parser.parse_args(argv)


def run_all(fixtures_dir: Path) -> list[EvalResult]:
    paths = discover_fixture_paths(fixtures_dir)
    if not paths:
        logger.warning("No fixtures found in %s", fixtures_dir)
        return []
    results: list[EvalResult] = []
    for path in paths:
        try:
            fixture = load_fixture(path)
        except Exception as exc:  # noqa: BLE001 — surface and continue
            logger.error("Failed to load %s: %s", path, exc)
            continue
        try:
            results.append(run_fixture(fixture))
        except Exception as exc:  # noqa: BLE001 — never crash the whole run
            logger.error("Fixture %s raised during run: %s", fixture.name, exc)
    return results


def write_report(results: list[EvalResult], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_csv_row())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = _parse_args(argv)

    results = run_all(args.fixtures_dir)
    if not results:
        print(f"No fixtures executed from {args.fixtures_dir}", file=sys.stderr)
        return 1

    write_report(results, args.output)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    if not args.quiet:
        for r in results:
            marker = "PASS" if r.passed else "FAIL"
            print(
                f"[{marker}] {r.fixture_name:35s} mode={r.mode:13s} "
                f"f1={r.tool_f1:.2f} kw={r.keyword_coverage:.2f} "
                f"iter={r.iterations}/{'within' if r.iterations_within_budget else 'over'} "
                f"obs={r.observation_count}"
            )
            if not r.passed:
                for reason in r.failure_reasons:
                    print(f"          - {reason}")
        print(
            f"\nSummary: {passed}/{len(results)} passed "
            f"({failed} failed). Report -> {args.output}"
        )

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
