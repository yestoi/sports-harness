"""The Claude client: what it sends, what it counts, and what it refuses to do.

Every response in this file is one of the two recorded fixtures or a hand-built payload of the
same shape. Nothing here makes a call, and no test in this file reads `secrets/`.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness.research.client import (PRIMARY_MODEL, RAW_TEXT_MAX_CHARS, REQUEST_TIMEOUT_S,
                                     SHADOW_MODEL, SNIPPETS_MAX_BYTES, WEB_SEARCH_TOOL_TYPE,
                                     ResearchClient, error_from, output_from, parse_response,
                                     prompt_hash, snippets_from, tool_calls_from, usage_from,
                                     web_search_tool)
from harness.research.spend import WORST_CASE_OUTPUT_TOKENS, Usage, cost_usd
from harness.research.text import sanitize_model_text

FIXTURES = Path(__file__).parent / "fixtures"
PROVING = json.loads((FIXTURES / "anthropic_structured_websearch.json").read_text())
CACHED = json.loads((FIXTURES / "anthropic_cache_hit.json").read_text())


class _FakeResponse:
    """What `response.model_dump(mode="json")` and `response._request_id` look like, built from
    a plain payload dict -- so a fake `messages.create` can return exactly what a real one would
    hand to `parse_response`."""

    def __init__(self, payload: dict, request_id: str | None) -> None:
        self._payload = payload
        self._request_id = request_id

    def model_dump(self, mode="json"):
        return self._payload


def _keyed_settings(env_settings, tmp_path, key_text="sk-ant-not-a-real-key"):
    key = tmp_path / "anthropic_api_key"
    key.write_text(key_text)
    return env_settings.model_copy(update={"anthropic_api_key_file": key})


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


def test_tool_calls_record_code_execution_blocks_with_a_sanitized_excerpt():
    """Review round 1 minor: Opus 5 wraps its searches in a `code_execution` sandbox on this
    tool version, and the record must show it, not only the `web_search` blocks."""
    code = "import json\n" + "x" * 400
    payload = {"usage": {}, "content": [
        {"type": "server_tool_use", "name": "code_execution", "input": {"code": code}}]}
    calls = tool_calls_from(payload)
    assert calls == [{"name": "code_execution", "code": sanitize_model_text(code, 200)}]
    assert len(calls[0]["code"]) <= 200
    assert "type" not in calls[0]      # the pinned type string is only ever recorded for search


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
    assert result.raw is None       # no schema failure here -- nothing to carry


def test_a_schema_failure_carries_the_raw_text_through_the_sanitizer():
    """Review round 1 Important 3: the controller's own proving run hit exactly this shape (a
    `max_tokens` truncation); without the raw text a truncation is indistinguishable from a
    genuine preamble. `sanitize_model_text` runs first (F60), so control characters are gone and
    the text is capped at `RAW_TEXT_MAX_CHARS`."""
    text = "\x00" + '{"decision": "proceed", "confidence": 0.8, "reason": "no news f' + "z" * 3000
    payload = {"stop_reason": "end_turn", "usage": {}, "content": [
        {"type": "text", "text": text}]}
    result = parse_response(PRIMARY_MODEL, payload, latency_ms=100, request_id="req_trunc")
    assert result.error == "schema" and result.output is None
    assert result.raw == sanitize_model_text(text, RAW_TEXT_MAX_CHARS)
    assert len(result.raw) <= RAW_TEXT_MAX_CHARS
    assert "\x00" not in result.raw


def test_raw_is_none_when_there_is_no_text_block_at_all():
    result = parse_response(PRIMARY_MODEL, {"stop_reason": "end_turn", "usage": {}, "content": []},
                            latency_ms=1, request_id="r")
    assert result.error == "schema" and result.raw is None


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
    settings = _keyed_settings(env_settings, tmp_path)
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
        # Review round 1 minor: `reserve_spend`'s advisory lock is held only around the
        # reservation, never across the call (T4's docstring), so a bounded per-request timeout
        # is the client's own concern rather than something the lock already limits.
        assert captured["timeout"] == REQUEST_TIMEOUT_S
    finally:
        client.close()


def test_call_sends_the_request_shape_max_tokens_output_config_tools_and_thinking(
        env_settings, tmp_path):
    """Review round 1 Important 1: `ResearchClient.call` was never exercised by a test, so
    nothing pinned the request shape -- `max_tokens`, `output_config.format`/`effort`, the system
    blocks (the `cache_control` breakpoint rides on that passthrough), `tools`, `thinking`."""
    settings = _keyed_settings(env_settings, tmp_path)
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(PROVING, "req_live")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    system = [{"type": "text", "text": "frozen", "cache_control": {"type": "ephemeral"}}]
    schema = {"type": "object", "properties": {}}
    tool = web_search_tool(3)
    thinking = {"type": "adaptive"}
    try:
        result = client.call(model=PRIMARY_MODEL, system=system, user="what happened today",
                             schema=schema, effort="high", max_output_tokens=4096,
                             tools=(tool,), thinking=thinking)
    finally:
        client.close()

    assert captured["model"] == PRIMARY_MODEL
    assert captured["max_tokens"] == 4096
    assert captured["system"] == system
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["messages"] == [{"role": "user", "content": "what happened today"}]
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": schema},
                                         "effort": "high"}
    assert captured["tools"] == [tool]
    assert captured["thinking"] == thinking
    assert result.request_id == "req_live" and result.error is None


def test_a_call_with_no_tools_or_thinking_omits_those_keys(env_settings, tmp_path):
    settings = _keyed_settings(env_settings, tmp_path)
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(PROVING, "req_live")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    try:
        client.call(model=PRIMARY_MODEL, system=[], user="hi", schema={}, effort="low")
    finally:
        client.close()
    assert "tools" not in captured and "thinking" not in captured


def test_a_raising_create_returns_a_labelled_call_result_not_an_exception(env_settings, tmp_path):
    """Review round 1 Important 1: a transport failure must still hand the caller a `CallResult`
    -- never raise -- so the caller's `finally` still releases the reservation."""
    settings = _keyed_settings(env_settings, tmp_path)

    class RateLimitError(Exception):
        pass

    class FakeMessages:
        def create(self, **kwargs):
            raise RateLimitError("429 too many requests")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    try:
        result = client.call(model=PRIMARY_MODEL, system=[], user="hi", schema={}, effort="high")
    finally:
        client.close()
    assert result.error == "RateLimitError"
    assert result.usage == Usage()
    assert result.request_id is None
    assert result.output is None


def test_max_output_tokens_defaults_to_the_worst_case(env_settings, tmp_path):
    """Review round 1 Important 2: design 0.3 -- the reservation and the request ceiling are the
    same constant, so a caller that passes nothing still requests exactly what was reserved."""
    settings = _keyed_settings(env_settings, tmp_path)
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(PROVING, "req_live")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    try:
        client.call(model=PRIMARY_MODEL, system=[], user="hi", schema={}, effort="high")
    finally:
        client.close()
    assert captured["max_tokens"] == WORST_CASE_OUTPUT_TOKENS


def test_max_output_tokens_above_the_worst_case_raises(env_settings, tmp_path):
    settings = _keyed_settings(env_settings, tmp_path)

    class FakeMessages:
        def create(self, **kwargs):
            raise AssertionError("must not be reached: the ceiling check runs first")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    client = ResearchClient(settings, factory=FakeAnthropic)
    try:
        with pytest.raises(ValueError, match="exceeds the reserved worst case"):
            client.call(model=PRIMARY_MODEL, system=[], user="hi", schema={}, effort="high",
                        max_output_tokens=WORST_CASE_OUTPUT_TOKENS + 1)
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
