"""Schema-only tests for the AYOSA MCP server.

These tests deliberately do NOT import `mcp_server.server` (which is the
only module that touches the optional `mcp` SDK). They cover:

* Every required tool name is present.
* Every JSON Schema is well-formed and marks sensitive fields.
* Pydantic input models validate / reject the right shapes.
* The redaction helper scrubs every documented sensitive field.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_server.schemas import (
    AyosaGetRunInput,
    AyosaInvestigateInput,
    AyosaSearchWorkspaceInput,
    QueryPrometheusInput,
    REDACTED_PLACEHOLDER,
    SENSITIVE_INPUT_FIELDS,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    redact_for_log,
)


REQUIRED_TOOLS = (
    "query_prometheus",
    "query_elasticsearch",
    "query_splunk",
    "query_alertmanager",
    "query_jaeger",
    "ayosa_investigate",
    "ayosa_search_workspace",
    "ayosa_get_run",
)


# ──────────────────────────────────────────────────────────────────────── #
# Catalogue
# ──────────────────────────────────────────────────────────────────────── #
class TestCatalogue:
    def test_every_required_tool_present(self):
        assert set(REQUIRED_TOOLS) <= set(TOOL_NAMES)

    def test_no_duplicate_tool_names(self):
        assert len(TOOL_NAMES) == len(set(TOOL_NAMES))

    def test_schema_count_matches_names(self):
        assert len(TOOL_SCHEMAS) == len(TOOL_NAMES)


# ──────────────────────────────────────────────────────────────────────── #
# Per-schema invariants
# ──────────────────────────────────────────────────────────────────────── #
class TestSchemaShape:
    @pytest.mark.parametrize("schema", TOOL_SCHEMAS)
    def test_schema_has_name_description_input_schema(self, schema):
        assert isinstance(schema["name"], str) and schema["name"]
        assert isinstance(schema["description"], str) and schema["description"]
        inp = schema["input_schema"]
        assert inp["type"] == "object"
        assert "properties" in inp and isinstance(inp["properties"], dict)
        assert "required" in inp and isinstance(inp["required"], list)
        # Lock the door: no extra free-form params at the MCP boundary.
        assert inp.get("additionalProperties") is False

    @pytest.mark.parametrize("schema", TOOL_SCHEMAS)
    def test_sensitive_fields_marked(self, schema):
        """Any property whose name is in SENSITIVE_INPUT_FIELDS must
        carry `sensitive: True` so clients/loggers can hide it."""
        for prop_name, prop in schema["input_schema"]["properties"].items():
            if prop_name.lower() in SENSITIVE_INPUT_FIELDS:
                assert prop.get("sensitive") is True, (
                    f"{schema['name']}.{prop_name} must be marked sensitive"
                )

    def test_query_tools_share_baseline_properties(self):
        baseline = {"base_url", "auth_token", "service",
                    "time_range", "message"}
        for s in TOOL_SCHEMAS:
            if not s["name"].startswith("query_"):
                continue
            assert set(s["input_schema"]["properties"].keys()) == baseline
            assert s["input_schema"]["required"] == ["base_url"]

    def test_ayosa_investigate_requires_tools(self):
        s = next(s for s in TOOL_SCHEMAS if s["name"] == "ayosa_investigate")
        assert "tools" in s["input_schema"]["required"]
        assert s["input_schema"]["properties"]["tools"]["minItems"] == 1
        # The tool-config sub-schema must also flag auth_token sensitive.
        item = s["input_schema"]["properties"]["tools"]["items"]
        assert item["properties"]["auth_token"]["sensitive"] is True

    def test_get_run_requires_run_id(self):
        s = next(s for s in TOOL_SCHEMAS if s["name"] == "ayosa_get_run")
        assert s["input_schema"]["required"] == ["run_id"]

    def test_search_workspace_limit_bounds(self):
        s = next(s for s in TOOL_SCHEMAS if s["name"] == "ayosa_search_workspace")
        limit = s["input_schema"]["properties"]["limit"]
        assert limit["minimum"] == 1
        assert limit["maximum"] == 100


# ──────────────────────────────────────────────────────────────────────── #
# Pydantic input models
# ──────────────────────────────────────────────────────────────────────── #
class TestPydanticValidation:
    def test_query_input_accepts_minimal(self):
        v = QueryPrometheusInput(base_url="http://prom")
        assert v.time_range == "30m"
        assert v.auth_token is None

    def test_query_input_rejects_missing_base_url(self):
        with pytest.raises(ValidationError):
            QueryPrometheusInput()  # type: ignore[call-arg]

    def test_investigate_requires_message_and_tools(self):
        with pytest.raises(ValidationError):
            AyosaInvestigateInput(message="hi")  # type: ignore[call-arg]

    def test_investigate_accepts_full_payload(self):
        v = AyosaInvestigateInput(
            message="why is payments slow?",
            service="payments",
            tools=[{"tool": "prometheus", "base_url": "http://p",
                    "auth_token": "secret-xyz"}],
        )
        assert v.tools[0].auth_token == "secret-xyz"
        assert v.time_range == "30m"

    def test_search_rejects_out_of_bounds_limit(self):
        with pytest.raises(ValidationError):
            AyosaSearchWorkspaceInput(query="x", limit=0)
        with pytest.raises(ValidationError):
            AyosaSearchWorkspaceInput(query="x", limit=1000)

    def test_get_run_rejects_empty(self):
        with pytest.raises(ValidationError):
            AyosaGetRunInput(run_id="")


# ──────────────────────────────────────────────────────────────────────── #
# Redaction
# ──────────────────────────────────────────────────────────────────────── #
class TestRedaction:
    def test_redacts_every_documented_sensitive_field(self):
        payload = {name: "leak-me" for name in SENSITIVE_INPUT_FIELDS}
        out = redact_for_log(payload)
        for k in SENSITIVE_INPUT_FIELDS:
            assert out[k] == REDACTED_PLACEHOLDER

    def test_preserves_non_sensitive_values(self):
        payload = {"base_url": "http://prom", "service": "payments",
                   "auth_token": "x"}
        out = redact_for_log(payload)
        assert out["base_url"] == "http://prom"
        assert out["service"] == "payments"
        assert out["auth_token"] == REDACTED_PLACEHOLDER

    def test_recurses_into_nested_containers(self):
        payload = {
            "tools": [
                {"tool": "prometheus", "auth_token": "secret-1"},
                {"tool": "splunk", "auth_token": "secret-2"},
            ],
            "ai": {"api_key": "k", "model": "claude"},
        }
        out = redact_for_log(payload)
        assert out["tools"][0]["auth_token"] == REDACTED_PLACEHOLDER
        assert out["tools"][1]["auth_token"] == REDACTED_PLACEHOLDER
        assert out["tools"][0]["tool"] == "prometheus"
        assert out["ai"]["api_key"] == REDACTED_PLACEHOLDER
        assert out["ai"]["model"] == "claude"

    def test_handles_none_values_without_redacting_to_string(self):
        out = redact_for_log({"auth_token": None})
        assert out["auth_token"] is None

    def test_does_not_mutate_input(self):
        payload = {"auth_token": "secret"}
        redact_for_log(payload)
        assert payload["auth_token"] == "secret"

    def test_returns_scalars_unchanged(self):
        assert redact_for_log("hello") == "hello"
        assert redact_for_log(42) == 42
        assert redact_for_log(None) is None
