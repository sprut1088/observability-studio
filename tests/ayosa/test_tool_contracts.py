"""Tests for the Step 5 JSON-Schema tool contracts.

Covers:
  * Per-tool Anthropic / OpenAI schema shape.
  * Configured-tool filtering.
  * `validate_tool_call` happy + error paths.
  * Intent-router schema + helpers.
"""

from __future__ import annotations

from accelerators.ayosa.agent.intent_router_llm import (
    CANONICAL_INTENTS,
    INTENT_ROUTER_INPUT_SCHEMA,
    INTENT_ROUTER_TOOL_NAME,
    anthropic_intent_router_tool,
    openai_intent_router_tool,
)
from accelerators.ayosa.agent.tool_registry import (
    TOOL_CALL_ARGUMENTS_SCHEMA,
    TOOL_REGISTRY,
    build_anthropic_tool_schemas,
    build_openai_tool_schemas,
    to_anthropic_tool_schema,
    to_openai_tool_schema,
    validate_tool_call,
)


# ─────────────────────────────────────────────────────────────────────── #
# Per-tool schema shape
# ─────────────────────────────────────────────────────────────────────── #
class TestPerToolSchemas:
    def test_anthropic_schema_has_required_fields(self):
        prom = TOOL_REGISTRY["prometheus"]
        schema = to_anthropic_tool_schema(prom)
        assert schema["name"] == "prometheus"
        assert isinstance(schema["description"], str) and schema["description"]
        assert schema["input_schema"] is TOOL_CALL_ARGUMENTS_SCHEMA

    def test_openai_schema_has_function_envelope(self):
        loki = TOOL_REGISTRY["loki"]
        schema = to_openai_tool_schema(loki)
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "loki"
        assert schema["function"]["parameters"] is TOOL_CALL_ARGUMENTS_SCHEMA

    def test_description_mentions_signals_and_intents(self):
        td = TOOL_REGISTRY["grafana"]
        desc = to_anthropic_tool_schema(td)["description"]
        for signal in td.signal_types:
            assert signal in desc
        # Should mention at least one supported intent.
        assert any(i in desc for i in td.supported_intents)


# ─────────────────────────────────────────────────────────────────────── #
# Filtering by configured tools
# ─────────────────────────────────────────────────────────────────────── #
class TestSchemaFiltering:
    def test_no_filter_returns_all_enabled(self):
        names = {s["name"] for s in build_anthropic_tool_schemas()}
        assert names == set(TOOL_REGISTRY.keys())

    def test_filter_returns_only_configured(self):
        schemas = build_anthropic_tool_schemas(["Prometheus", " loki "])
        names = sorted(s["name"] for s in schemas)
        assert names == ["loki", "prometheus"]

    def test_openai_filter_returns_only_configured(self):
        schemas = build_openai_tool_schemas(["grafana"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "grafana"

    def test_unknown_configured_tools_silently_skipped(self):
        schemas = build_anthropic_tool_schemas(["prometheus", "made-up-tool"])
        names = [s["name"] for s in schemas]
        assert names == ["prometheus"]

    def test_empty_iterable_returns_no_tools(self):
        assert build_anthropic_tool_schemas([]) == []
        assert build_openai_tool_schemas([]) == []


# ─────────────────────────────────────────────────────────────────────── #
# validate_tool_call
# ─────────────────────────────────────────────────────────────────────── #
class TestValidateToolCall:
    def test_minimum_valid_call(self):
        errors = validate_tool_call(
            "prometheus", {"reason": "compute error rate"}
        )
        assert errors == []

    def test_full_valid_call(self):
        errors = validate_tool_call(
            "loki",
            {
                "service": "checkout",
                "time_range": "15m",
                "query": '{job="checkout"} |= "error"',
                "reason": "fetch recent errors",
            },
        )
        assert errors == []

    def test_unknown_tool_rejected(self):
        errors = validate_tool_call("not-a-tool", {"reason": "x"})
        assert any("unknown tool" in e for e in errors)

    def test_missing_required_argument(self):
        errors = validate_tool_call("prometheus", {})
        assert any("reason" in e for e in errors)

    def test_unknown_argument_rejected(self):
        errors = validate_tool_call(
            "prometheus", {"reason": "x", "rogue_field": 42}
        )
        assert any("rogue_field" in e for e in errors)

    def test_wrong_type_rejected(self):
        errors = validate_tool_call(
            "prometheus", {"reason": "x", "service": 123}
        )
        assert any("service" in e for e in errors)

    def test_arguments_must_be_dict(self):
        errors = validate_tool_call("prometheus", "reason=x")  # type: ignore[arg-type]
        assert any("JSON object" in e for e in errors)

    def test_null_strings_accepted(self):
        # The schema allows ["string","null"] for service/time_range/query.
        errors = validate_tool_call(
            "prometheus",
            {"reason": "x", "service": None, "time_range": None, "query": None},
        )
        assert errors == []


# ─────────────────────────────────────────────────────────────────────── #
# Intent-router schema
# ─────────────────────────────────────────────────────────────────────── #
class TestIntentRouterSchema:
    def test_enum_matches_canonical_intents(self):
        enum = INTENT_ROUTER_INPUT_SCHEMA["properties"]["intent"]["enum"]
        assert tuple(enum) == CANONICAL_INTENTS

    def test_anthropic_tool_shape(self):
        spec = anthropic_intent_router_tool()
        assert spec["name"] == INTENT_ROUTER_TOOL_NAME
        assert spec["input_schema"] is INTENT_ROUTER_INPUT_SCHEMA
        assert "description" in spec

    def test_openai_tool_shape(self):
        spec = openai_intent_router_tool()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == INTENT_ROUTER_TOOL_NAME
        assert spec["function"]["parameters"] is INTENT_ROUTER_INPUT_SCHEMA

    def test_required_includes_intent_and_confidence(self):
        required = set(INTENT_ROUTER_INPUT_SCHEMA["required"])
        assert {"intent", "confidence", "reasoning"} <= required
