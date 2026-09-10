"""The Claude client: what it sends, what it counts, and what it refuses to do.

Every response in this file is one of the two recorded fixtures or a hand-built payload of the
same shape. Nothing here makes a call, and no test in this file reads `secrets/`.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness.research.client import (PRIMARY_MODEL, SHADOW_MODEL, SNIPPETS_MAX_BYTES,
                                     WEB_SEARCH_TOOL_TYPE, ResearchClient, error_from,
                                     output_from, parse_response, prompt_hash, snippets_from,
                                     tool_calls_from, usage_from, web_search_tool)
from harness.research.spend import Usage, cost_usd

FIXTURES = Path(__file__).parent / "fixtures"
PROVING = json.loads((FIXTURES / "anthropic_structured_websearch.json").read_text())
CACHED = json.loads((FIXTURES / "anthropic_cache_hit.json").read_text())


# --- the recorded proof (ruling B-I11) --------------------------------------------------------

def test_neither_fixture_contains_a_key():
    for name in ("anthropic_structured_websearch.json", "anthropic_cache_hit.json"):
        body = (FIXTURES / name).read_text()
        assert "sk-ant-" not in body, f"{name} carries a key"


def test_the_proving_call_returned_structured_output_and_ran_a_search():
    """B-I11: structured output plus web_search, parsed on the installed SDK, before any veto
    code was merged. The fixture is the proof and this is the assertion over it."""
    output, error = output_from(PROVING)
    assert error is None
    assert output["decision"] in ("proceed", "reduce", "veto")
    assert set(output) == {"decision", "confidence", "reason", "evidence_ids"}
    # `>= 1`, not `== 2`: the recorded call runs as many searches as the model chose. Addendum §5
    # asks for accounting from a response with two searches, and the deterministic two-search
    # case is `test_searches_are_counted_from_the_server_tool_use_blocks` below. This assertion
    # is that the recording proves the tool ran at all.
    assert usage_from(PROVING).searches >= 1


def test_the_second_call_read_the_cache():
    """B-M4: the veto's cache assertion, pinned here against the recording so the veto test can
    assert the same thing over its own call without a second live call."""
    assert CACHED["usage"]["cache_read_input_tokens"] > 0
    assert usage_from(CACHED).cache_read_tokens == CACHED["usage"]["cache_read_input_tokens"]


def test_the_tool_type_string_matches_the_installed_sdk():
    """Verified-facts D4 records two candidate literals from two sources; this is measured, and
    a version bump that changes it fails here rather than at 3 a.m. in production.

    Not every name that matches `WebSearchTool*Param` is a tool-type param: the SDK also ships
    `WebSearchToolResultBlockParamContentParam`, a content union with no `type` literal, and
    `typing.get_type_hints` raises `TypeError` trying to resolve it. That name contributes no
    candidate literal either way, so it is skipped rather than allowed to fail the pin.

    The installed SDK (1.4.0) marks each TypedDict's `type` field `Required[Literal[...]]`, so a
    single `typing.get_args` call peels off only the `Required` wrapper and leaves the `Literal`
    object itself, not its string. `_literal_strings` recurses through `get_args` until it hits
    the actual string leaves, which also makes it correct against an SDK that dropped the
    `Required` wrapper and exposed a bare `Literal` directly.
    """
    import typing

    import anthropic.types as types

    def _literal_strings(tp) -> set[str]:
        args = typing.get_args(tp)
        strings: set[str] = set()
        for arg in args:
            if isinstance(arg, str):
                strings.add(arg)
            else:
                strings |= _literal_strings(arg)
        return strings

    names = [n for n in dir(types) if "WebSearchTool" in n and n.endswith("Param")]
    literals: set[str] = set()
    for name in names:
        try:
            hint = typing.get_type_hints(getattr(types, name), include_extras=True).get("type")
        except TypeError:
            continue
        literals |= _literal_strings(hint)
    assert WEB_SEARCH_TOOL_TYPE in literals, f"{WEB_SEARCH_TOOL_TYPE} not in {literals}"


# --- the tool block ---------------------------------------------------------------------------

def test_the_web_search_tool_block():
    assert web_search_tool(3) == {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search",
                                  "max_uses": 3}


def test_allowed_domains_is_not_set():
    """D22: `allowed_domains` is unset. The veto is shadow-only, defaults to proceed, and needs
    quoted evidence for anything else, so an injected page can at worst produce a recorded
    decision that changes nothing. Promotion to enforcing must revisit it."""
    assert "allowed_domains" not in web_search_tool(3)
    assert "blocked_domains" not in web_search_tool(3)


# --- usage and cost ----------------------------------------------------------------------------

def test_usage_reads_the_four_token_fields():
    payload = {"content": [], "usage": {"input_tokens": 100, "output_tokens": 20,
                                        "cache_read_input_tokens": 30,
                                        "cache_creation_input_tokens": 40}}
    assert usage_from(payload) == Usage(100, 20, 30, 40, 0)


def test_searches_are_counted_from_the_server_tool_use_blocks():
    payload = {"usage": {}, "content": [
        {"type": "server_tool_use", "name": "code_execution", "input": {"code": "..."}},
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "a"}},
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "b"}},
        {"type": "code_execution_tool_result", "content": {}},
        {"type": "text", "text": "{}"},
    ]}
    # The `code_execution` blocks are the ones Opus 5 wraps its searches in (controller
    # artefacts, 2026-09-10): only `web_search` is billed per search, so only it is counted.
    assert usage_from(payload).searches == 2


def test_a_usage_sub_field_wins_over_the_block_count_when_present():
    """Verified-facts D4: pin the sub-field against the installed SDK. When the response carries
    one it is the venue's own count and beats ours."""
    payload = {"usage": {"server_tool_use": {"web_search_requests": 5}},
               "content": [{"type": "server_tool_use", "name": "web_search", "input": {}}]}
    assert usage_from(payload).searches == 5


def test_a_missing_usage_object_counts_as_zero_not_as_an_error():
    assert usage_from({"content": []}).input_tokens == 0


# --- snippets and tool calls -------------------------------------------------------------------

def test_snippets_carry_url_title_and_id_and_nothing_else():
    payload = {"usage": {}, "content": [{"type": "web_search_tool_result", "content": [
        {"type": "web_search_result", "url": "https://example.org/a", "title": "A",
         "page_age": "1 hour ago", "encrypted_content": "zzzz"},
    ]}]}
    snippets = snippets_from(payload)
    assert snippets["items"] == [{"id": "s1", "url": "https://example.org/a", "title": "A",
                                  "page_age": "1 hour ago"}]
    assert snippets["truncated"] is False
    assert "encrypted_content" not in json.dumps(snippets)


def test_snippets_are_truncated_at_six_kilobytes_with_a_flag():
    """Ruling B-M9: JSONB bounds are enforced by truncation at write with a flag."""
    results = [{"type": "web_search_result", "url": f"https://example.org/{i}",
                "title": "x" * 200, "page_age": "1 hour ago"} for i in range(200)]
    snippets = snippets_from({"usage": {}, "content": [
        {"type": "web_search_tool_result", "content": results}]})
    assert snippets["truncated"] is True
    assert len(json.dumps(snippets).encode()) <= SNIPPETS_MAX_BYTES


def test_a_search_error_block_does_not_index_into_a_list():
    """Verified-facts D4: a web-search failure returns HTTP 200 with a `content` that is a
    single error *object*, not a list, so a parser that indexed it would raise."""
    payload = {"usage": {}, "content": [{"type": "web_search_tool_result",
                                         "content": {"error_code": "max_uses_exceeded"}}]}
    assert snippets_from(payload)["items"] == []
    assert error_from(payload) == "search_error"


def test_tool_calls_record_the_pinned_type_and_the_query():
    """Ruling B-M5: the tool's pinned type string is recorded per call, so a version bump is
    visible in the record rather than only in the code."""
    payload = {"usage": {}, "content": [
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "lsu injury"}}]}
    assert tool_calls_from(payload) == [
        {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "query": "lsu injury"}]


# --- the five error shapes ----------------------------------------------------------------------

@pytest.mark.parametrize("payload,expected", [
    ({"stop_reason": "pause_turn", "usage": {}, "content": []}, "pause_turn"),
    ({"stop_reason": "refusal", "usage": {}, "content": []}, "refusal"),
    ({"stop_reason": "end_turn", "usage": {}, "content": [
        {"type": "web_search_tool_result", "content": {"error_code": "unavailable"}}]},
     "search_error"),
    ({"stop_reason": "end_turn", "usage": {}, "content": [
        {"type": "text", "text": "not json at all"}]}, "schema"),
    ({"stop_reason": "end_turn", "usage": {}, "content": [
        {"type": "text", "text": "{\"ok\": 1}"}]}, None),
])
def test_error_from(payload, expected):
    assert error_from(payload) == expected


def test_output_from_returns_the_reason_when_the_json_will_not_parse():
    output, error = output_from({"content": [{"type": "text", "text": "{oops"}]})
    assert output is None and error == "schema"


def test_output_from_skips_thinking_blocks_to_find_the_text():
    output, _ = output_from({"content": [{"type": "thinking", "thinking": "..."},
                                         {"type": "text", "text": "{\"a\": 1}"}]})
    assert output == {"a": 1}


def test_parse_response_assembles_a_call_result():
    result = parse_response(PRIMARY_MODEL, PROVING, latency_ms=12_345, request_id="req_abc")
    assert result.model == PRIMARY_MODEL
    assert result.latency_ms == 12_345 and result.request_id == "req_abc"
    assert result.error is None and result.output is not None
    # The recording's own numbers, priced by the spend module: 99 in, 1,239 out, 12,851 cache
    # reads at 0.1x, 8,233 cache writes at 1.25x and 1 search at $0.01, on Opus 5's $5/$25.
    assert result.usage == Usage(99, 1_239, 12_851, 8_233, 1)
    assert cost_usd(PRIMARY_MODEL, result.usage) == Decimal("0.099352")


# --- structure ------------------------------------------------------------------------------------

def test_only_the_client_module_calls_messages_create():
    """Global constraint: every Anthropic call goes through `reserve_spend` first, and the only
    way to guarantee that is that there is exactly one place a call can be made from."""
    package = Path(__file__).resolve().parents[1] / "harness" / "research"
    offenders = [p.name for p in sorted(package.glob("*.py"))
                 if "messages.create" in p.read_text() and p.name != "client.py"]
    assert offenders == []


def test_the_client_is_built_with_retries_off(env_settings, tmp_path):
    """A-C3 hole 3: a retried request is a second billed call the accounting would never see."""
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = None

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    try:
        assert captured["max_retries"] == 0
        assert captured["api_key"] == "sk-ant-not-a-real-key"
    finally:
        client.close()


def test_the_client_refuses_to_build_without_a_key(env_settings):
    with pytest.raises(RuntimeError, match="no anthropic key"):
        ResearchClient(env_settings)


def test_prompt_hash_is_stable_and_changes_with_a_byte():
    a = [{"type": "text", "text": "frozen"}]
    b = [{"type": "text", "text": "frozen "}]
    assert prompt_hash(a) == prompt_hash(a) and len(prompt_hash(a)) == 64
    assert prompt_hash(a) != prompt_hash(b)


def test_the_model_ids_are_the_exact_strings():
    assert PRIMARY_MODEL == "claude-opus-5" and SHADOW_MODEL == "claude-sonnet-5"
    assert "-2026" not in PRIMARY_MODEL and "-2026" not in SHADOW_MODEL
