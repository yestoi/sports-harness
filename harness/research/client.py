"""The one place in the harness that calls Anthropic.

Every research feature -- the veto's primary, its shadow, the weekly annotator, the parlay
rationale -- goes through this module, and every call goes through `harness.research.spend`'s
reservation first. `tests/test_research_client.py::test_only_the_client_module_calls_messages_create`
asserts the first half structurally; the callers' own tests assert the second.

Four things here are decisions, not conveniences:

* **`max_retries = 0`** (ruling A-C3, hole 3). The SDK retries 408/409/429/5xx twice by default.
  A request billed server-side but lost client-side is retried, paid for twice and counted once,
  and the U4 caps are enforced from what is counted. Every attempt is its own call, its own
  reservation and its own row.
* **`pause_turn` is a failure, never continued.** Resuming a paused turn is a second billed
  request under one reservation. It returns as `error = "pause_turn"` and the veto records
  `veto_error`.
* **Searches are counted from the response.** Web search is billed per search **on top of**
  tokens and the fetched `Usage` snippet exposes no count, so `usage_from` counts
  `server_tool_use` blocks named `web_search`, preferring a `usage.server_tool_use` sub-field
  when the installed SDK exposes one. A token-only model under-reports a three-search call by
  about four times.
* **A server-tool error does not raise.** A web-search failure comes back HTTP 200 with a
  `web_search_tool_result` block whose `content` is a single error *object* rather than a list,
  so every reader here branches on the type before indexing.

The parsers all take a plain dict -- `response.model_dump(mode="json")` -- rather than an SDK
object, which is what lets the two recorded fixtures exercise exactly the code production runs.
"""
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import anthropic

from harness.research.spend import WORST_CASE_OUTPUT_TOKENS, Usage
from harness.research.text import sanitize_model_text

log = logging.getLogger(__name__)

#: The Anthropic client's request timeout, in seconds. `reserve_spend`'s advisory lock is held
#: only around the reservation, never across the call itself (T4's docstring), so this bounds a
#: single slow request rather than anything shared -- generous because the veto's own latency is
#: already 10-30 s and a three-search call re-bills context on every round.
REQUEST_TIMEOUT_S = 120.0

#: The exact model id strings. Never a date suffix (verified-facts D4).
PRIMARY_MODEL = "claude-opus-5"
SHADOW_MODEL = "claude-sonnet-5"

#: Pinned against the installed SDK by this task's controller step A, and asserted by
#: `test_the_tool_type_string_matches_the_installed_sdk`. Verified-facts D4 records two
#: different candidates from two sources, so this is measured and never written from memory.
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

#: Ruling B-M9: `research_notes.snippets` is capped at 6 KB at write, with a `truncated` flag
#: inside the object rather than beside it, so a reader of one row can see it.
SNIPPETS_MAX_BYTES = 6 * 1024


@dataclass(frozen=True)
class CallResult:
    """One model's side of one call, whatever happened.

    `error` is `None`, or one of `pause_turn`, `refusal`, `search_error`, `schema`, `max_tokens`,
    or the class name of a transport exception. `output` is the parsed structured output, or
    `None`.

    `raw` is `None` except on a `schema` failure, where it carries the first text block's own
    text, sanitized: without the text there is no way to tell a genuinely malformed response
    from a preamble the model wrote before JSON it never got to write.

    `error_detail` is `None` except on an `anthropic.APIStatusError` (fix 39, journal 110) or a
    `max_tokens` response (fix 41, journal 112). For the former it is the API's own `message`
    field for the rejected request -- never the request, never the SDK's own `.message` (which
    echoes the whole decoded body back). For the latter it names the output token count the
    response actually stopped at (fix 41: the controller's own proving run, and the first live
    annotator calls on the corrected schema, both hit exactly this shape -- a response accepted
    with HTTP 200 whose JSON never completed because `max_output_tokens` ran out first). Both are
    sanitized and capped like every other stored string, so either failure is diagnosable from
    the notes row instead of carrying only the exception's class name or an unparsed truncation.
    """

    model: str
    output: dict | None
    usage: Usage
    stop_reason: str | None
    request_id: str | None
    latency_ms: int
    tool_calls: list[dict]
    snippets: dict
    error: str | None
    raw: str | None = None
    error_detail: str | None = None


def web_search_tool(max_uses: int) -> dict:
    """The server-side search tool block.

    `allowed_domains` and `blocked_domains` are deliberately absent (D22): the veto is
    shadow-only, defaults to `proceed`, and requires a quoted evidence id for anything else, so
    an injected page can at worst produce a recorded decision that changes nothing. Promotion to
    enforcing must revisit that.
    """
    return {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}


def prompt_hash(system_blocks: list[dict]) -> str:
    """The sha256 of the frozen system prompt, recorded on every note.

    Hashed over the canonical JSON of the blocks rather than the concatenated text: a
    `cache_control` breakpoint moving is a different prompt for caching purposes, and the record
    has to be able to say so.
    """
    canonical = json.dumps(system_blocks, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- the parsers ------------------------------------------------------------------------------

def _blocks(payload: dict) -> list[dict]:
    content = payload.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def usage_from(payload: dict) -> Usage:
    usage = payload.get("usage") or {}
    sub = usage.get("server_tool_use") or {}
    searches = sub.get("web_search_requests")
    if not isinstance(searches, int):
        searches = sum(1 for b in _blocks(payload)
                       if b.get("type") == "server_tool_use" and b.get("name") == "web_search")
    return Usage(input_tokens=int(usage.get("input_tokens") or 0),
                 output_tokens=int(usage.get("output_tokens") or 0),
                 cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                 cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                 searches=int(searches))


def snippets_from(payload: dict) -> dict:
    """Every retrieved page's id, URL, title and age -- and nothing else.

    The encrypted page content is deliberately dropped: F60 says snippets are never re-rendered,
    the veto's evidence ids point at these entries, and storing the body would put megabytes of
    untrusted text in a column no reader is allowed to show.
    """
    items: list[dict] = []
    for block in _blocks(payload):
        if block.get("type") != "web_search_tool_result":
            continue
        results = block.get("content")
        if not isinstance(results, list):      # an error object, not a result list
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            items.append({"id": f"s{len(items) + 1}",
                          "url": str(result.get("url") or ""),
                          "title": str(result.get("title") or ""),
                          "page_age": str(result.get("page_age") or "")})
    truncated = False
    # Measured with `"truncated": False` -- one byte *longer* than the `true` serialization --
    # so the ceiling holds for whichever flag the returned object actually carries. Measuring
    # with `True` instead let an untruncated result land one byte over 6 KB (review round 1).
    while items and len(json.dumps({"items": items, "truncated": False}).encode()) > SNIPPETS_MAX_BYTES:
        items.pop()
        truncated = True
    return {"items": items, "truncated": truncated}


#: `tool_calls_from`'s code-execution entries carry at most this many characters of the model's
#: own sandbox code (review round 1 minor): enough to see what ran, never the whole script.
_CODE_EXCERPT_CHARS = 200


def tool_calls_from(payload: dict) -> list[dict]:
    """What the model asked a server tool for.

    `web_search` calls carry the pinned type string and the query (ruling B-M5). Opus 5 routes
    its searches through a `code_execution` sandbox on this tool version (controller artefacts,
    2026-09-10), so every other `server_tool_use` block is recorded too, under its own name, with
    an excerpt of its code run through `sanitize_model_text` -- it is model-authored text landing
    in a stored column, so F60's sanitizer applies to it exactly as it does to any other.
    """
    calls: list[dict] = []
    for block in _blocks(payload):
        if block.get("type") != "server_tool_use":
            continue
        name = block.get("name")
        if name == "web_search":
            calls.append({"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search",
                          "query": str((block.get("input") or {}).get("query") or "")})
        else:
            code = str((block.get("input") or {}).get("code") or "")
            calls.append({"name": str(name),
                          "code": sanitize_model_text(code, _CODE_EXCERPT_CHARS)})
    return calls


def _first_text(payload: dict) -> str | None:
    """The first text block's own text, unparsed -- what `parse_response` sanitizes and carries
    as `CallResult.raw` on a `schema` failure. `None` when there is no text block at all."""
    for block in _blocks(payload):
        if block.get("type") == "text":
            return block.get("text")
    return None


def output_from(payload: dict) -> tuple[dict | None, str | None]:
    """The structured output, or `(None, "schema")`.

    With `output_config.format` set, the first *text* block holds valid JSON -- but adaptive
    thinking puts thinking blocks ahead of it, so the search is by block type and not by index.
    """
    for block in _blocks(payload):
        if block.get("type") != "text":
            continue
        try:
            parsed = json.loads(block.get("text") or "")
        except (TypeError, ValueError):
            return None, "schema"
        return (parsed, None) if isinstance(parsed, dict) else (None, "schema")
    return None, "schema"


def error_from(payload: dict) -> str | None:
    """Which of the recorded failure shapes this response is, or None."""
    stop_reason = payload.get("stop_reason")
    if stop_reason in ("pause_turn", "refusal", "max_tokens"):
        # Fix 41 (journal 112): a `max_tokens` response used to fall through to the `schema`
        # branch below -- its JSON is truncated and so never parses -- and be indistinguishable
        # from a genuinely malformed response. It is its own failure shape now: the response
        # itself says why the JSON is incomplete, and `parse_response` records that reason
        # rather than guessing at it from a parse failure.
        return stop_reason
    for block in _blocks(payload):
        if block.get("type") == "web_search_tool_result" and not isinstance(
                block.get("content"), list):
            return "search_error"
    return output_from(payload)[1]


#: F60: the sanitizer's cap for the raw text a schema failure carries. Generous relative to the
#: 300-character veto reason -- this is a diagnostic field, not prose meant to be read whole.
RAW_TEXT_MAX_CHARS = 2000

#: Fix 39: the cap for the API's own error message, stored in `CallResult.error_detail` and
#: `research_notes.output.error_detail`. Sized like the veto's own reason (F60's 300) rather than
#: `RAW_TEXT_MAX_CHARS`: this is one sentence naming what was wrong with the request, not prose
#: that might run long.
ERROR_DETAIL_MAX_CHARS = 300


def _error_detail(exc: anthropic.APIStatusError) -> str | None:
    """The API's own `message` field for a 4xx/5xx response, sanitized and capped -- never
    `exc.message`, which the SDK builds as `f"Error code: {status} - {body}"` and so echoes the
    whole decoded body back rather than naming the one field a person needs to diagnose a
    rejected schema (the brief's own distinction: "not the request, not the body's echo of it").
    `None` when the body was not the structured `{"error": {"message": ...}}` shape the API sends
    for a normal 4xx/5xx -- an unparsed body (`_make_status_error_from_response` falls back to
    the raw response text when it is not JSON) carries nothing this can safely single out.
    """
    body = getattr(exc, "body", None)
    message = body.get("error") if isinstance(body, dict) else None
    message = message.get("message") if isinstance(message, dict) else None
    return sanitize_model_text(message, ERROR_DETAIL_MAX_CHARS) if isinstance(message, str) \
        else None


def parse_response(model: str, payload: dict, latency_ms: int,
                   request_id: str | None) -> CallResult:
    error = error_from(payload)
    output, _ = output_from(payload)
    usage = usage_from(payload)
    raw = None
    error_detail = None
    if error == "schema":
        text = _first_text(payload)
        if text is not None:
            raw = sanitize_model_text(text, RAW_TEXT_MAX_CHARS)
    elif error == "max_tokens":
        # Fix 41 (journal 112): the count is the diagnosable fact here -- it is what tells a
        # reader whether the response stopped at the caller's own `max_output_tokens` (a budget
        # too low for this prompt) or somewhere short of it (a different problem).
        error_detail = f"stopped at {usage.output_tokens} output tokens"
    return CallResult(model=model, output=None if error else output, usage=usage,
                      stop_reason=payload.get("stop_reason"), request_id=request_id,
                      latency_ms=latency_ms, tool_calls=tool_calls_from(payload),
                      snippets=snippets_from(payload), error=error, raw=raw,
                      error_detail=error_detail)


# --- the client -----------------------------------------------------------------------------------

class ResearchClient:
    """One Anthropic client, built only when the key file is a file.

    `factory` exists for the tests: it defaults to `anthropic.Anthropic` and is the seam that
    keeps every test in this repository from needing a key.
    """

    def __init__(self, settings, factory: Callable | None = None) -> None:
        if not settings.has_anthropic_key():
            raise RuntimeError("no anthropic key: research features are dormant")
        if factory is None:
            factory = anthropic.Anthropic
        self.s = settings
        self._client = factory(api_key=settings.anthropic_api_key(), max_retries=0,
                               timeout=REQUEST_TIMEOUT_S)

    def call(self, *, model: str, system: list[dict], user: str, schema: dict, effort: str,
             max_output_tokens: int = WORST_CASE_OUTPUT_TOKENS, tools: tuple[dict, ...] = (),
             thinking: dict | None = None) -> CallResult:
        """One request. Never retries, never resumes a paused turn, never raises on a venue
        error: a transport failure comes back as a `CallResult` whose `error` is the exception's
        class name, so the caller's `finally` still releases the reservation.

        `max_output_tokens` defaults to `WORST_CASE_OUTPUT_TOKENS` and may never exceed it
        (design 0.3): `reserve_spend`'s projection is priced at that same constant, so a caller
        that requested more tokens than it reserved would be billed against a reservation that
        was never large enough to cover it.
        """
        if max_output_tokens > WORST_CASE_OUTPUT_TOKENS:
            raise ValueError(f"max_output_tokens {max_output_tokens} exceeds the reserved "
                             f"worst case {WORST_CASE_OUTPUT_TOKENS}")
        started = time.monotonic()
        kwargs = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema},
                              "effort": effort},
        }
        if tools:
            kwargs["tools"] = list(tools)
        if thinking is not None:
            kwargs["thinking"] = thinking
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            # Fix 39 (journal 110): the first live annotator call returned HTTP 400 and the old
            # `except Exception` branch below logged only the class name, leaving nothing to
            # diagnose it with. Caught ahead of the general branch so every 4xx/5xx also carries
            # the API's own message into the log and the returned result.
            detail = _error_detail(exc)
            log.warning("anthropic call failed on %s: %s (status %s): %s", model,
                        type(exc).__name__, exc.status_code, detail)
            return CallResult(model=model, output=None, usage=Usage(), stop_reason=None,
                              request_id=None,
                              latency_ms=int((time.monotonic() - started) * 1000),
                              tool_calls=[], snippets={"items": [], "truncated": False},
                              error=type(exc).__name__, error_detail=detail)
        except Exception as exc:  # noqa: BLE001 - the caller must still release its reservation
            log.warning("anthropic call failed on %s: %s", model, type(exc).__name__)
            return CallResult(model=model, output=None, usage=Usage(), stop_reason=None,
                              request_id=None,
                              latency_ms=int((time.monotonic() - started) * 1000),
                              tool_calls=[], snippets={"items": [], "truncated": False},
                              error=type(exc).__name__)
        payload = response.model_dump(mode="json")
        return parse_response(model, payload,
                              latency_ms=int((time.monotonic() - started) * 1000),
                              request_id=getattr(response, "_request_id", None))

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            close()
