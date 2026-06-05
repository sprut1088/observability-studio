"""AYOSA agent evaluation harness — Feature 27.

A fixture-driven test bed for measuring whether the agent (deterministic
planner OR tool-use loop) selects the right tools, gathers enough
evidence, and produces an answer that mentions the key facts.

This package is **dev/QA tooling**, not part of the production code
path. It is imported by:

  * ``tests/ayosa/evals/test_evals.py`` — pytest parametrized over
    every fixture in ``fixtures/``.
  * ``tests/ayosa/evals/test_harness.py`` — unit tests for the pure
    scoring + loader functions.
  * ``scripts/run_evals.py`` — CLI that emits a CSV report.
"""
