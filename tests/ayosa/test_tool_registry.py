"""Unit tests for the AYOSA structured tool registry.

Pure, in-process — no network, no real adapters.
"""

from __future__ import annotations

import pytest

from accelerators.ayosa.agent.tool_registry import (
    KNOWN_INTENTS,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    ToolDefinition,
    describe_missing_signals,
    format_missing_signal_message,
    get_tool,
    list_tools,
    select_tools_for_signals,
    tools_for_intent,
    tools_for_signal,
    validate_tool_config,
)
from accelerators.ayosa.registry import ADAPTERS


# ──────────────────────────────────────────────────────────────────────── #
# Catalogue invariants
# ──────────────────────────────────────────────────────────────────────── #
EXPECTED_TOOLS = {
    "prometheus", "alertmanager", "elasticsearch", "opensearch", "splunk",
    "grafana", "jaeger", "tempo", "loki", "datadog", "dynatrace", "appdynamics",
}

EXPECTED_SIGNAL_TYPES = {"metrics", "logs", "alerts", "traces", "dashboards"}


class TestCatalogue:
    def test_all_expected_tools_present(self):
        assert set(TOOL_REGISTRY.keys()) == EXPECTED_TOOLS

    def test_definition_count_matches_index(self):
        assert len(TOOL_DEFINITIONS) == len(TOOL_REGISTRY)

    def test_every_signal_type_is_known(self):
        seen = {s for td in TOOL_DEFINITIONS for s in td.signal_types}
        assert seen.issubset(EXPECTED_SIGNAL_TYPES)
        # Every signal type should be produced by at least one tool.
        assert seen == EXPECTED_SIGNAL_TYPES

    def test_every_intent_reference_is_known(self):
        seen = {i for td in TOOL_DEFINITIONS for i in td.supported_intents}
        unknown = seen - KNOWN_INTENTS
        assert not unknown, f"Unknown intents referenced: {unknown}"

    def test_no_payment_or_checkout_hardcoding(self):
        """Registry must remain service-agnostic."""
        text = " ".join(
            (
                td.name
                + " "
                + " ".join(td.example_queries)
                + " "
                + " ".join(td.supported_intents)
                + " "
                + " ".join(td.required_fields)
                + " "
                + " ".join(td.optional_fields)
            )
            for td in TOOL_DEFINITIONS
        ).lower()
        for forbidden in ("payment", "checkout", "paymentservice", "checkoutservice"):
            assert forbidden not in text, f"Found '{forbidden}' in registry"

    def test_adapter_key_maps_into_existing_registry(self):
        """Backward compatibility — every registry entry's adapter_key
        must resolve in the legacy ADAPTERS map."""
        for td in TOOL_DEFINITIONS:
            assert td.adapter_key in ADAPTERS, (
                f"{td.name}.adapter_key='{td.adapter_key}' not in ADAPTERS"
            )

    def test_required_fields_always_include_base_url(self):
        for td in TOOL_DEFINITIONS:
            assert "base_url" in td.required_fields, (
                f"{td.name} must require base_url"
            )


# ──────────────────────────────────────────────────────────────────────── #
# Lookup helpers
# ──────────────────────────────────────────────────────────────────────── #
class TestLookup:
    def test_get_tool_case_insensitive(self):
        assert get_tool("Prometheus") is TOOL_REGISTRY["prometheus"]
        assert get_tool("  SPLUNK  ") is TOOL_REGISTRY["splunk"]

    def test_get_tool_unknown(self):
        assert get_tool("nope") is None
        assert get_tool("") is None
        assert get_tool(None) is None  # type: ignore[arg-type]

    def test_list_tools_enabled_only(self):
        assert len(list_tools()) == len(TOOL_DEFINITIONS)
        # Disable one in a copy and confirm filtering works
        td = ToolDefinition(
            name="x", signal_types=["metrics"], adapter_key="x", enabled=False
        )
        assert not td.covers("metrics")
        assert not td.supports_intent("anything")

    def test_tools_for_signal(self):
        metric_tools = {t.name for t in tools_for_signal("metrics")}
        assert "prometheus" in metric_tools
        assert "datadog" in metric_tools
        # Pure log tools should NOT appear under metrics
        assert "loki" not in metric_tools
        assert "elasticsearch" not in metric_tools

    def test_tools_for_intent(self):
        names = {t.name for t in tools_for_intent("trace_lookup")}
        assert "jaeger" in names
        assert "tempo" in names
        # A purely metrics tool must not support trace_lookup
        assert "prometheus" not in names


# ──────────────────────────────────────────────────────────────────────── #
# Tool selection (the planner's core algorithm)
# ──────────────────────────────────────────────────────────────────────── #
class TestSelectToolsForSignals:
    def test_selects_one_tool_per_signal(self):
        selected, covered = select_tools_for_signals(
            ["metrics", "logs"], ["prometheus", "elasticsearch", "alertmanager"]
        )
        assert set(selected) == {"prometheus", "elasticsearch"}
        assert set(covered) == {"metrics", "logs"}

    def test_no_overlap_when_one_tool_covers_multiple_signals(self):
        # datadog covers metrics + logs; should be selected once
        selected, covered = select_tools_for_signals(
            ["metrics", "logs"], ["datadog"]
        )
        assert selected == ["datadog"]
        assert set(covered) == {"metrics", "logs"}

    def test_unconfigured_tools_ignored(self):
        selected, covered = select_tools_for_signals(
            ["traces"], ["prometheus", "alertmanager"]
        )
        assert selected == []
        assert covered == []

    def test_case_insensitive_configured_tools(self):
        selected, _ = select_tools_for_signals(
            ["alerts"], ["AlertManager", "GRAFANA"]
        )
        # alertmanager comes before grafana in registry order
        assert selected[0] == "alertmanager"

    def test_unknown_signal_returns_nothing(self):
        selected, covered = select_tools_for_signals(
            ["nonexistent_signal"], ["prometheus"]  # type: ignore[list-item]
        )
        assert selected == []
        assert covered == []

    def test_empty_inputs(self):
        assert select_tools_for_signals([], []) == ([], [])
        assert select_tools_for_signals(["metrics"], []) == ([], [])
        assert select_tools_for_signals([], ["prometheus"]) == ([], [])


# ──────────────────────────────────────────────────────────────────────── #
# Missing signal reporting
# ──────────────────────────────────────────────────────────────────────── #
class TestMissingSignals:
    def test_describe_missing_signals(self):
        missing = describe_missing_signals(
            ["metrics", "logs", "traces"], ["prometheus"]
        )
        assert "metrics" not in missing  # covered by prometheus
        assert "logs" in missing
        assert "traces" in missing
        # Suggestions come from the registry — no random strings
        for sig, suggestions in missing.items():
            for s in suggestions:
                assert s in TOOL_REGISTRY, f"Suggestion '{s}' not in registry"

    def test_describe_missing_signals_caps_suggestions(self):
        missing = describe_missing_signals(
            ["logs"], [], max_suggestions_per_signal=2
        )
        assert len(missing["logs"]) == 2

    def test_format_missing_signal_message_includes_signal_and_suggestions(self):
        msg = format_missing_signal_message(
            "error_investigation", ["logs"], ["prometheus"]
        )
        assert "error investigation" in msg
        assert "logs" in msg
        assert "elasticsearch" in msg or "splunk" in msg or "loki" in msg

    def test_format_missing_signal_message_empty_when_covered(self):
        msg = format_missing_signal_message(
            "service_health", ["metrics"], ["prometheus"]
        )
        assert msg == ""

    def test_no_hardcoded_service_names_in_message(self):
        msg = format_missing_signal_message(
            "error_investigation", ["logs", "traces"], []
        )
        for forbidden in ("payment", "checkout"):
            assert forbidden not in msg.lower()


# ──────────────────────────────────────────────────────────────────────── #
# Config validation
# ──────────────────────────────────────────────────────────────────────── #
class TestValidateToolConfig:
    def test_missing_required_field(self):
        # splunk requires base_url + auth_token
        assert validate_tool_config(
            "splunk", {"base_url": "http://x", "auth_token": ""}
        ) == ["auth_token"]

    def test_all_fields_present(self):
        assert (
            validate_tool_config(
                "prometheus", {"base_url": "http://x"}
            )
            == []
        )

    def test_unknown_tool_returns_empty(self):
        assert validate_tool_config("nope", {}) == []


# ──────────────────────────────────────────────────────────────────────── #
# Planner integration — confirms the agent honours the registry
# ──────────────────────────────────────────────────────────────────────── #
class TestPlannerUsesRegistry:
    def _agent_input(self, message: str, tools: list[str]):
        from accelerators.ayosa.agent.schemas import AgentInput, AgentToolConfig

        return AgentInput(
            message=message,
            service=None,
            time_range="15m",
            tools=[
                AgentToolConfig(tool=t, base_url=f"http://{t}.local", auth_token=None)
                for t in tools
            ],
            session_id="reg-1",
        )

    def test_planner_picks_registry_tool_for_traces(self):
        from accelerators.ayosa.agent.planner import Planner

        plan = Planner().build(
            self._agent_input("show me a trace lookup", ["tempo", "prometheus"])
        )
        assert plan.intent == "trace_lookup"
        assert "tempo" in plan.selected_tools
        assert "prometheus" not in plan.selected_tools  # not a traces tool

    def test_planner_skips_disabled_tool(self, monkeypatch):
        from accelerators.ayosa.agent import tool_registry
        from accelerators.ayosa.agent.planner import Planner

        original = tool_registry.TOOL_REGISTRY["prometheus"].enabled
        try:
            tool_registry.TOOL_REGISTRY["prometheus"].enabled = False
            plan = Planner().build(
                self._agent_input("p99 latency", ["prometheus"])
            )
            assert "prometheus" not in plan.selected_tools
        finally:
            tool_registry.TOOL_REGISTRY["prometheus"].enabled = original

    def test_planner_missing_signal_message_via_registry(self):
        """When evidence is empty the agent's answer is registry-derived."""
        from accelerators.ayosa.agent import AyosaAgent
        from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher

        # Empty adapter map ⇒ no findings produced
        agent = AyosaAgent(dispatcher=ToolDispatcher({}))
        result = agent.run(
            self._agent_input("any active alerts?", ["alertmanager"])
        )
        # alerts ARE covered by alertmanager, but dispatcher returns no
        # findings (empty adapter map). The fallback message kicks in.
        # Now ask for a signal NOT covered to exercise the registry path:
        result2 = agent.run(
            self._agent_input("any active alerts?", ["prometheus"])
        )
        # alerts is required, prometheus doesn't cover it
        assert "alerts" in result2.final_response.lower()
        # Suggestion must come from the registry
        assert any(
            name in result2.final_response
            for name in ("alertmanager", "grafana", "splunk", "datadog", "dynatrace")
        )
