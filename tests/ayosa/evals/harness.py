"""Pure functions + small classes that drive the AYOSA eval harness.

Design contract
---------------
1. **Pure scoring functions.** ``score_run()`` takes an ``AgentResult``
   plus a ``Fixture.golden`` and returns an ``EvalResult``. No I/O, no
   randomness — given the same inputs it always returns the same score.
2. **No network.** The scripted dispatcher returns canned observations
   from the fixture. The synthesiser is gated on ``llm.enabled`` so
   fixtures keep ``llm=None`` and never reach Anthropic / Azure.
3. **No side effects.** A fresh ``AyosaAgent`` is constructed per
   fixture so the in-memory ``ContextManager`` doesn't leak state
   across runs. No SQLite / file persistence is touched.

Two execution modes are supported per fixture:

  * ``"deterministic"`` (default) — exercises ``Planner`` + ``Dispatcher``
    via ``AyosaAgent.run``. This is the path most users hit when the
    tool-use loop is off.
  * ``"tool_use"`` — requires the fixture's ``scripted_llm`` field, a
    list of Anthropic-shaped messages. Builds a fake Anthropic client
    that returns those messages in sequence and routes through the
    Step 24 ``ToolUseLoop``.

The scoring metrics are the same in both modes so a fixture can be
evaluated against either path and the deltas are directly comparable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentResult,
    AgentToolConfig,
    Observation,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# Fixture data classes
# ──────────────────────────────────────────────────────────────────────── #
@dataclass(frozen=True)
class GoldenAnswer:
    """The expected behaviour for one fixture.

    All fields are optional so fixtures only have to assert what they
    actually care about; defaults are permissive.
    """
    expected_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    max_iterations: int = 4
    min_observations: int = 0
    min_keyword_coverage: float = 0.5

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GoldenAnswer":
        d = d or {}
        return cls(
            expected_tools=tuple(_lower_strip_seq(d.get("expected_tools") or ())),
            forbidden_tools=tuple(_lower_strip_seq(d.get("forbidden_tools") or ())),
            expected_keywords=tuple(
                str(k).lower() for k in (d.get("expected_keywords") or ())
            ),
            max_iterations=int(d.get("max_iterations", 4)),
            min_observations=int(d.get("min_observations", 0)),
            min_keyword_coverage=float(d.get("min_keyword_coverage", 0.5)),
        )


@dataclass(frozen=True)
class Fixture:
    """One incident scenario."""
    name: str
    description: str
    mode: str  # "deterministic" | "tool_use"
    agent_input: AgentInput
    scripted_adapters: dict[str, list[dict[str, Any]]]
    scripted_llm: tuple[dict[str, Any], ...]  # only used when mode == tool_use
    golden: GoldenAnswer
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source_path: Path | None = None) -> "Fixture":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"Fixture {source_path} is missing 'name'")
        mode = str(raw.get("mode") or "deterministic").strip().lower()
        if mode not in {"deterministic", "tool_use"}:
            raise ValueError(
                f"Fixture {name!r}: unknown mode {mode!r}. "
                "Must be 'deterministic' or 'tool_use'."
            )

        agent_input = _build_agent_input(name, raw.get("input") or {})
        scripted = _normalise_scripted_adapters(
            name, raw.get("scripted_adapters") or {}
        )
        scripted_llm = tuple(raw.get("scripted_llm") or ())
        if mode == "tool_use" and not scripted_llm:
            raise ValueError(
                f"Fixture {name!r}: mode='tool_use' requires "
                "'scripted_llm' (a list of Anthropic-shaped messages)."
            )

        return cls(
            name=name,
            description=str(raw.get("description") or "").strip(),
            mode=mode,
            agent_input=agent_input,
            scripted_adapters=scripted,
            scripted_llm=scripted_llm,
            golden=GoldenAnswer.from_dict(raw.get("golden") or {}),
            source_path=source_path,
        )


# ──────────────────────────────────────────────────────────────────────── #
# Eval result
# ──────────────────────────────────────────────────────────────────────── #
@dataclass(frozen=True)
class EvalResult:
    """Scored outcome for one fixture run."""
    fixture_name: str
    mode: str
    passed: bool
    tool_precision: float
    tool_recall: float
    tool_f1: float
    forbidden_tools_called: bool
    keyword_coverage: float
    iterations: int
    iterations_within_budget: bool
    observation_count: int
    meets_min_observations: bool
    final_response: str
    failure_reasons: tuple[str, ...] = ()

    def to_csv_row(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture_name,
            "mode": self.mode,
            "passed": self.passed,
            "tool_precision": round(self.tool_precision, 3),
            "tool_recall": round(self.tool_recall, 3),
            "tool_f1": round(self.tool_f1, 3),
            "forbidden_tools_called": self.forbidden_tools_called,
            "keyword_coverage": round(self.keyword_coverage, 3),
            "iterations": self.iterations,
            "iterations_within_budget": self.iterations_within_budget,
            "observation_count": self.observation_count,
            "meets_min_observations": self.meets_min_observations,
            "failure_reasons": " | ".join(self.failure_reasons),
        }


# ──────────────────────────────────────────────────────────────────────── #
# Pure helpers
# ──────────────────────────────────────────────────────────────────────── #
def _lower_strip_seq(seq: Sequence[Any]) -> list[str]:
    return [str(s).strip().lower() for s in seq if str(s).strip()]


def _build_agent_input(fixture_name: str, raw: dict[str, Any]) -> AgentInput:
    if not isinstance(raw, dict):
        raise ValueError(
            f"Fixture {fixture_name!r}: 'input' must be an object."
        )
    message = str(raw.get("message") or "").strip()
    if not message:
        raise ValueError(
            f"Fixture {fixture_name!r}: 'input.message' is required."
        )
    tools_raw = raw.get("tools") or []
    if not isinstance(tools_raw, list):
        raise ValueError(
            f"Fixture {fixture_name!r}: 'input.tools' must be a list."
        )
    tools = [
        AgentToolConfig(
            tool=str(t.get("tool") or "").strip(),
            base_url=str(t.get("base_url") or "http://stub.local").strip(),
            auth_token=(t.get("auth_token") or None),
        )
        for t in tools_raw
    ]
    return AgentInput(
        message=message,
        service=raw.get("service"),
        time_range=str(raw.get("time_range") or "30m"),
        tools=tools,
        llm=None,  # harness never reaches a real LLM via the synthesiser
        session_id=str(raw.get("session_id") or f"eval-{fixture_name}"),
    )


def _normalise_scripted_adapters(
    fixture_name: str,
    raw: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Validate the ``scripted_adapters`` block: each tool key maps to a
    list of observation-shaped dicts."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"Fixture {fixture_name!r}: 'scripted_adapters' must be an object."
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for key, items in raw.items():
        if not isinstance(items, list):
            raise ValueError(
                f"Fixture {fixture_name!r}: 'scripted_adapters[{key}]' "
                "must be a list."
            )
        clean: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Fixture {fixture_name!r}: every entry under "
                    f"'scripted_adapters[{key}]' must be an object."
                )
            clean.append(dict(item))
        out[str(key).strip().lower()] = clean
    return out


# ──────────────────────────────────────────────────────────────────────── #
# Scripted adapter factory
# ──────────────────────────────────────────────────────────────────────── #
class _ScriptedAdapter:
    """Adapter that returns a fixed list of observations on every call.

    Matches the duck-typed contract that ``ToolDispatcher._invoke_adapter``
    expects: a class that accepts ``(base_url, auth_token=None)`` and
    exposes ``investigate(service, time_range, message, plan=None)``.
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
        *,
        observations: list[dict[str, Any]] | None = None,
    ) -> None:
        self.base_url = base_url
        self.auth_token = auth_token
        self._observations = list(observations or [])
        self.call_history: list[dict[str, Any]] = []

    def investigate(self, service, time_range, message, plan=None):  # noqa: D401
        self.call_history.append({
            "service": service,
            "time_range": time_range,
            "message": message,
            "plan": plan,
        })
        # Return a fresh copy each call so callers can't mutate our state.
        return [dict(o) for o in self._observations]


def build_scripted_dispatcher(
    scripted_adapters: dict[str, list[dict[str, Any]]],
) -> ToolDispatcher:
    """Build a ``ToolDispatcher`` whose adapter registry returns canned
    observations for every tool in ``scripted_adapters``.

    Any tool the agent tries to dispatch that isn't in the script
    surfaces through the dispatcher's normal "no adapter found" error
    path — which is itself a useful negative signal in scoring.
    """
    adapters: dict[str, Callable[..., _ScriptedAdapter]] = {}
    for tool_key, observations in scripted_adapters.items():
        # Default-argument trick captures the per-tool observations
        # without late-binding all factories to the last loop value.
        def _factory(base_url, auth_token=None, _obs=observations):
            return _ScriptedAdapter(
                base_url, auth_token=auth_token, observations=_obs
            )
        adapters[tool_key] = _factory
    return ToolDispatcher(adapters=adapters)


# ──────────────────────────────────────────────────────────────────────── #
# Scoring
# ──────────────────────────────────────────────────────────────────────── #
def _tool_calls_from_result(result: AgentResult) -> list[str]:
    """Return the lower-cased names of every tool the agent actually
    invoked. Only ``done`` and ``error`` steps count — ``pending`` /
    ``running`` / ``skipped`` mean the dispatcher never finished an
    adapter call, so they should not contribute to tool-selection
    precision/recall."""
    called: list[str] = []
    for step in result.tool_steps:
        if step.status not in {"done", "error"}:
            continue
        called.append((step.tool or "").strip().lower())
    return [t for t in called if t]


def _keyword_coverage(text: str, keywords: Sequence[str]) -> float:
    """Fraction of ``keywords`` that appear (case-insensitive substring)
    in ``text``. Returns 1.0 when ``keywords`` is empty so it never
    fails closed."""
    if not keywords:
        return 1.0
    haystack = (text or "").lower()
    hits = sum(1 for kw in keywords if kw and kw in haystack)
    return hits / len(keywords)


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def score_run(result: AgentResult, golden: GoldenAnswer) -> EvalResult:
    """Pure scorer — no I/O. Inputs in, ``EvalResult`` out.

    The pass criteria are deliberately conservative: every dimension
    has to clear its threshold for ``passed=True``. Adjust per-fixture
    via ``GoldenAnswer.min_keyword_coverage`` / ``max_iterations`` /
    ``min_observations``.
    """
    called = _tool_calls_from_result(result)
    called_set = set(called)
    expected_set = set(golden.expected_tools)

    if called_set:
        precision = len(called_set & expected_set) / len(called_set)
    else:
        # No tools called → precision is undefined; treat as 0 unless
        # nothing was expected.
        precision = 1.0 if not expected_set else 0.0

    if expected_set:
        recall = len(called_set & expected_set) / len(expected_set)
    else:
        recall = 1.0

    forbidden_called = bool(called_set & set(golden.forbidden_tools))
    coverage = _keyword_coverage(result.final_response, golden.expected_keywords)
    iterations_ok = result.iterations <= golden.max_iterations
    obs_count = len(result.observations)
    enough_obs = obs_count >= golden.min_observations

    failures: list[str] = []
    if expected_set and recall < 1.0:
        missed = sorted(expected_set - called_set)
        failures.append(f"missed expected tools: {missed}")
    if forbidden_called:
        offenders = sorted(called_set & set(golden.forbidden_tools))
        failures.append(f"called forbidden tools: {offenders}")
    if coverage < golden.min_keyword_coverage:
        missing_kws = [
            kw for kw in golden.expected_keywords
            if kw and kw not in (result.final_response or "").lower()
        ]
        failures.append(
            f"keyword coverage {coverage:.2f} < {golden.min_keyword_coverage:.2f} "
            f"(missing: {missing_kws})"
        )
    if not iterations_ok:
        failures.append(
            f"iterations {result.iterations} > budget {golden.max_iterations}"
        )
    if not enough_obs:
        failures.append(
            f"observation count {obs_count} < min {golden.min_observations}"
        )

    passed = not failures

    return EvalResult(
        fixture_name="",  # caller fills this in
        mode="",
        passed=passed,
        tool_precision=precision,
        tool_recall=recall,
        tool_f1=_f1(precision, recall),
        forbidden_tools_called=forbidden_called,
        keyword_coverage=coverage,
        iterations=result.iterations,
        iterations_within_budget=iterations_ok,
        observation_count=obs_count,
        meets_min_observations=enough_obs,
        final_response=result.final_response or "",
        failure_reasons=tuple(failures),
    )


# ──────────────────────────────────────────────────────────────────────── #
# Fixture loader
# ──────────────────────────────────────────────────────────────────────── #
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def discover_fixture_paths(directory: Path | None = None) -> list[Path]:
    """Return every ``*.json`` fixture path in ``directory``, sorted by
    file name so test ordering is deterministic across machines."""
    base = (directory or FIXTURES_DIR).resolve()
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("*.json") if p.is_file())


def load_fixture(path: Path) -> Fixture:
    """Read one JSON fixture from disk and parse it into a ``Fixture``."""
    raw_text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fixture {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Fixture {path} must be a JSON object at the top level.")
    return Fixture.from_dict(data, source_path=path)


def iter_fixtures(directory: Path | None = None) -> Iterator[Fixture]:
    for path in discover_fixture_paths(directory):
        yield load_fixture(path)


# ──────────────────────────────────────────────────────────────────────── #
# Run one fixture
# ──────────────────────────────────────────────────────────────────────── #
def run_fixture(fixture: Fixture) -> EvalResult:
    """Execute one fixture through the agent and score the result.

    Constructs a brand-new ``AyosaAgent`` per call so the in-memory
    ``ContextManager`` doesn't bleed across fixtures.
    """
    dispatcher = build_scripted_dispatcher(fixture.scripted_adapters)
    # Honour the fixture's iteration budget so the agent doesn't loop
    # forever on a misconfigured scenario.
    agent = AyosaAgent(
        dispatcher=dispatcher,
        max_iterations=max(1, fixture.golden.max_iterations),
    )

    if fixture.mode == "deterministic":
        result = agent.run(fixture.agent_input)
    elif fixture.mode == "tool_use":
        result = _run_tool_use(agent, fixture)
    else:  # pragma: no cover — guarded in Fixture.from_dict
        raise ValueError(f"Unsupported mode: {fixture.mode}")

    scored = score_run(result, fixture.golden)
    # Patch the immutable EvalResult to carry fixture identity.
    return EvalResult(
        fixture_name=fixture.name,
        mode=fixture.mode,
        passed=scored.passed,
        tool_precision=scored.tool_precision,
        tool_recall=scored.tool_recall,
        tool_f1=scored.tool_f1,
        forbidden_tools_called=scored.forbidden_tools_called,
        keyword_coverage=scored.keyword_coverage,
        iterations=scored.iterations,
        iterations_within_budget=scored.iterations_within_budget,
        observation_count=scored.observation_count,
        meets_min_observations=scored.meets_min_observations,
        final_response=scored.final_response,
        failure_reasons=scored.failure_reasons,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Tool-use mode plumbing (scripted Anthropic client)
# ──────────────────────────────────────────────────────────────────────── #
def _run_tool_use(agent: AyosaAgent, fixture: Fixture) -> AgentResult:
    """Drive the ``ToolUseLoop`` with a fake Anthropic client built
    from ``fixture.scripted_llm``.

    ``scripted_llm`` is a list of message dicts; each must include
    ``content`` (a list of block dicts with ``type`` ∈ {text, tool_use})
    and a ``stop_reason``. The fake client returns them in order.
    """
    from accelerators.ayosa.agent.tool_use_loop import (
        ToolUseLoop,
        synthesize_agent_result_from_loop,
    )
    from accelerators.ayosa.agent.intent_classifier import classify_intent_smart

    client = _ScriptedAnthropicClient(fixture.scripted_llm)
    loop = ToolUseLoop(
        client=client,
        dispatcher=agent.dispatcher,
        max_iterations=agent.max_iterations,
    )
    intent, intent_meta = classify_intent_smart(
        fixture.agent_input.message,
        llm_config=None,  # harness never reaches real LLM here
        service_hint=fixture.agent_input.service,
    )
    loop_result = loop.run(fixture.agent_input)
    return synthesize_agent_result_from_loop(
        loop_result=loop_result,
        agent_input=fixture.agent_input,
        intent=intent,
        intent_meta=intent_meta,
    )


class _ScriptedBlock:
    """Attribute-style wrapper so the ToolUseLoop's content extractors
    treat scripted dicts like real Anthropic SDK blocks."""

    __slots__ = ("type", "text", "id", "name", "input")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.type = raw.get("type")
        self.text = raw.get("text")
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.input = raw.get("input") or {}


class _ScriptedMessage:
    """Attribute-style wrapper mimicking ``anthropic.types.Message``."""

    __slots__ = ("content", "stop_reason")

    def __init__(self, raw: dict[str, Any]) -> None:
        blocks_raw = raw.get("content") or []
        self.content = [_ScriptedBlock(b) for b in blocks_raw]
        self.stop_reason = raw.get("stop_reason") or "end_turn"


class _ScriptedMessages:
    def __init__(self, scripted: Sequence[dict[str, Any]]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _ScriptedMessage:
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError(
                "Scripted Anthropic client exhausted — the agent made "
                "more LLM calls than the fixture provided."
            )
        return _ScriptedMessage(self._scripted.pop(0))


class _ScriptedAnthropicClient:
    """Drop-in replacement for ``anthropic.Anthropic`` for the harness."""

    def __init__(self, scripted: Sequence[dict[str, Any]]) -> None:
        self.messages = _ScriptedMessages(scripted)


__all__ = [
    "EvalResult",
    "FIXTURES_DIR",
    "Fixture",
    "GoldenAnswer",
    "build_scripted_dispatcher",
    "discover_fixture_paths",
    "iter_fixtures",
    "load_fixture",
    "run_fixture",
    "score_run",
]
