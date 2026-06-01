"""Tests for `accelerators.obsco.studio_knowledge`.

Covers detection precision, fact structure, and alias coverage. The
knowledge base is curated, so the tests focus on:
- declarative invariants (every entry has the required keys),
- detection precision (no false positives on bare tokens),
- topic detection (intent vocabulary works),
- alias / synonym matching is case-insensitive and word-bounded.
"""

from __future__ import annotations

import pytest

from accelerators.obsco.studio_knowledge import (
    STUDIO_FACTS,
    STUDIO_TOPICS,
    detect_studio_topics,
    get_studio_facts,
    list_studio_keys,
)


REQUIRED_KEYS = {
    "display_name",
    "category",
    "purpose",
    "inputs",
    "how_it_works",
    "outputs",
    "api_endpoints",
    "key_features",
    "faqs",
    "related_docs",
    "aliases",
}


# ──────────────────────────────────────────────────────────────────────── #
# Fact-base shape
# ──────────────────────────────────────────────────────────────────────── #
class TestRegistryShape:
    def test_at_least_seven_accelerators(self) -> None:
        # platform, obscrawl, observascore, rca_agent, red_panel,
        # gap_map, ayosa, obsco, mcp_server
        assert len(STUDIO_FACTS) >= 7

    @pytest.mark.parametrize("key", list(STUDIO_FACTS.keys()))
    def test_required_keys_present(self, key: str) -> None:
        facts = STUDIO_FACTS[key]
        missing = REQUIRED_KEYS - set(facts.keys())
        assert not missing, f"{key} missing keys: {missing}"

    @pytest.mark.parametrize("key", list(STUDIO_FACTS.keys()))
    def test_aliases_are_lowercase_and_non_empty(self, key: str) -> None:
        aliases = STUDIO_FACTS[key]["aliases"]
        assert isinstance(aliases, tuple)
        assert len(aliases) >= 1
        for a in aliases:
            assert isinstance(a, str)
            assert a == a.lower()
            assert a.strip() == a

    @pytest.mark.parametrize("key", list(STUDIO_FACTS.keys()))
    def test_list_fields_are_lists(self, key: str) -> None:
        facts = STUDIO_FACTS[key]
        for f in ("inputs", "how_it_works", "outputs",
                  "api_endpoints", "key_features", "related_docs"):
            assert isinstance(facts[f], list), f"{key}.{f} is not list"

    @pytest.mark.parametrize("key", list(STUDIO_FACTS.keys()))
    def test_faqs_are_qa_pairs(self, key: str) -> None:
        for entry in STUDIO_FACTS[key]["faqs"]:
            assert set(entry.keys()) == {"q", "a"}
            assert entry["q"].strip()
            assert entry["a"].strip()


# ──────────────────────────────────────────────────────────────────────── #
# Getters
# ──────────────────────────────────────────────────────────────────────── #
class TestGetters:
    def test_list_studio_keys_returns_all(self) -> None:
        assert set(list_studio_keys()) == set(STUDIO_FACTS.keys())

    def test_get_studio_facts_hit(self) -> None:
        assert get_studio_facts("observascore") is STUDIO_FACTS["observascore"]

    def test_get_studio_facts_case_insensitive(self) -> None:
        assert get_studio_facts("ObservaScore") is STUDIO_FACTS["observascore"]

    def test_get_studio_facts_missing(self) -> None:
        assert get_studio_facts("does-not-exist") is None

    def test_get_studio_facts_empty(self) -> None:
        assert get_studio_facts("") is None


# ──────────────────────────────────────────────────────────────────────── #
# Detection: accelerator matching
# ──────────────────────────────────────────────────────────────────────── #
class TestDetectAccelerator:
    @pytest.mark.parametrize(
        "msg, expected",
        [
            ("How does ObservaScore work?", "observascore"),
            ("Tell me about obscrawl", "obscrawl"),
            ("What does the RCA Agent do?", "rca_agent"),
            ("Explain RED panel intelligence", "red_panel_intelligence"),
            ("How does the gap map identify blind spots?",
             "observability_gap_map"),
            ("How does AYOSA classify intents?", "ayosa"),
            ("How does ObsCo know what to answer?", "obsco"),
            ("How do I run the MCP server?", "mcp_server"),
        ],
    )
    def test_each_accelerator_detected_by_primary_alias(
        self, msg: str, expected: str,
    ) -> None:
        matches = detect_studio_topics(msg)
        keys = [m["accelerator"] for m in matches]
        assert expected in keys

    def test_empty_input_returns_empty(self) -> None:
        assert detect_studio_topics("") == []
        assert detect_studio_topics("   ") == []

    def test_no_accelerator_mention_returns_empty(self) -> None:
        assert detect_studio_topics("How do I query Splunk for errors?") == []

    def test_case_insensitive(self) -> None:
        a = detect_studio_topics("OBSERVASCORE outputs?")
        b = detect_studio_topics("observascore outputs?")
        assert [m["accelerator"] for m in a] == [m["accelerator"] for m in b]

    def test_multiple_accelerators_in_one_question(self) -> None:
        msg = "Compare ObservaScore and ObsCrawl outputs"
        keys = [m["accelerator"] for m in detect_studio_topics(msg)]
        assert "observascore" in keys
        assert "obscrawl" in keys

    def test_preserves_declaration_order(self) -> None:
        msg = "I want to know about AYOSA and ObsCrawl"
        keys = [m["accelerator"] for m in detect_studio_topics(msg)]
        # obscrawl is declared before ayosa in STUDIO_FACTS.
        order = list(STUDIO_FACTS.keys())
        assert order.index(keys[0]) < order.index(keys[1])

    def test_word_boundary_no_substring_false_positive(self) -> None:
        # "scored" must not trigger "score" alias mid-token; the
        # platform/observascore alias "maturity score" should not fire here.
        matches = detect_studio_topics("My deploy was scored as risky.")
        assert [m["accelerator"] for m in matches] == []

    def test_longer_alias_does_not_block_shorter_when_distinct(self) -> None:
        # "root cause analysis" and "rca" both alias rca_agent — should
        # still produce exactly one entry.
        matches = detect_studio_topics("Run a root cause analysis with rca")
        keys = [m["accelerator"] for m in matches]
        assert keys.count("rca_agent") == 1


# ──────────────────────────────────────────────────────────────────────── #
# Detection: topic vocabulary
# ──────────────────────────────────────────────────────────────────────── #
class TestDetectTopics:
    def test_how_it_works_detected(self) -> None:
        m = detect_studio_topics("How does ObservaScore work?")
        assert "how_it_works" in m[0]["topics"]

    def test_outputs_detected(self) -> None:
        m = detect_studio_topics("What output does AYOSA produce?")
        assert "outputs" in m[0]["topics"]

    def test_inputs_detected(self) -> None:
        m = detect_studio_topics("What inputs does ObsCrawl require?")
        assert "inputs" in m[0]["topics"]

    def test_api_detected(self) -> None:
        m = detect_studio_topics("What endpoints does the RCA agent expose?")
        assert "api" in m[0]["topics"]

    def test_troubleshooting_detected(self) -> None:
        m = detect_studio_topics("Why is my ObservaScore assessment failing?")
        assert "troubleshooting" in m[0]["topics"]

    def test_history_detected(self) -> None:
        m = detect_studio_topics("How do I compare two AYOSA runs?")
        assert "history" in m[0]["topics"]

    def test_no_topic_returns_empty_topic_list(self) -> None:
        m = detect_studio_topics("ObservaScore")
        # purpose token "what" not present — topics may be empty; that
        # MUST not crash, and the accelerator is still matched.
        assert m and m[0]["accelerator"] == "observascore"
        assert isinstance(m[0]["topics"], list)


# ──────────────────────────────────────────────────────────────────────── #
# Vocabulary sanity
# ──────────────────────────────────────────────────────────────────────── #
class TestVocabulary:
    def test_every_topic_has_at_least_one_keyword(self) -> None:
        for topic, kws in STUDIO_TOPICS.items():
            assert kws, topic

    def test_every_keyword_is_lowercase_word(self) -> None:
        for topic, kws in STUDIO_TOPICS.items():
            for kw in kws:
                assert kw == kw.lower()
                assert " " not in kw
                assert kw.isascii()
