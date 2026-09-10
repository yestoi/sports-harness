"""The frozen prompt: its shape, its cache breakpoint, its schema, and its untrusted block."""
import json

from harness.research.client import prompt_hash
from harness.research.prompt import (EFFORT, MAX_OUTPUT_TOKENS, OUTPUT_SCHEMA, PROMPT_HASH,
                                     SYSTEM_BLOCKS, THINKING, render_user)
from harness.research.spend import WORST_CASE_OUTPUT_TOKENS


def test_the_effort_is_written_high():
    """Addendum 0.4: R:217's "default effort" is written as `high`, explicitly, and pinned in
    the prompt hash -- so the veto study's arms are unambiguous."""
    assert EFFORT == "high"


def test_thinking_is_adaptive():
    assert THINKING == {"type": "adaptive"}


def test_the_last_cache_breakpoint_is_the_system_block():
    """Caching is a prefix match and the render order is tools then system then messages, so the
    frozen prompt is cacheable exactly as long as the features sit *after* the breakpoint. They
    are in the user message, which is where."""
    assert SYSTEM_BLOCKS[-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in SYSTEM_BLOCKS[:-1])


def test_the_prompt_hash_is_frozen():
    assert PROMPT_HASH == prompt_hash(SYSTEM_BLOCKS)
    assert len(PROMPT_HASH) == 64


def test_the_prompt_defaults_to_proceed_and_demands_evidence():
    text = " ".join(block["text"] for block in SYSTEM_BLOCKS).lower()
    assert "proceed" in text
    assert "evidence" in text
    assert "untrusted" in text


def test_the_schema_is_closed_and_names_the_four_fields():
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["properties"]) == {"decision", "confidence", "reason",
                                                "evidence_ids"}
    assert OUTPUT_SCHEMA["properties"]["decision"]["enum"] == ["proceed", "reduce", "veto"]
    assert OUTPUT_SCHEMA["properties"]["reason"]["maxLength"] == 300
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])


def test_the_user_message_separates_the_two_blocks():
    body = render_user({"fair_p": 0.51}, {"weather": {"short_forecast": "Sunny"}})
    assert "UNTRUSTED" in body
    assert body.index("UNTRUSTED") > body.index("0.51")
    assert json.loads(body.split("UNTRUSTED", 1)[0].split("FEATURES", 1)[1].strip())


def test_the_user_message_is_the_only_thing_that_varies():
    """Two signals produce two different user messages and the same system blocks, which is what
    makes the cache hit and what makes the prompt hash mean something."""
    a = render_user({"fair_p": 0.51}, {"weather": None})
    b = render_user({"fair_p": 0.62}, {"weather": None})
    assert a != b
    assert prompt_hash(SYSTEM_BLOCKS) == PROMPT_HASH


def test_the_output_budget_is_the_reserved_worst_case():
    """Addendum 0.3, as amended at the T4 review: "the worst-case output tokens per call equal
    the `max_tokens` every caller passes (4,096, exported by the spend module)". The brief's
    literal 1,024 is the pre-amendment number and `ResearchClient.call` would accept it, which
    is exactly how a reservation and a request part company."""
    assert MAX_OUTPUT_TOKENS == WORST_CASE_OUTPUT_TOKENS == 4096


def test_the_reason_cap_in_the_schema_is_the_sanitizer_s():
    """F60's 300 is one number with one home. A schema that asked for more than the sanitizer
    keeps would make the model write prose that is silently cut at storage."""
    from harness.research.text import VETO_REASON_MAX

    assert OUTPUT_SCHEMA["properties"]["reason"]["maxLength"] == VETO_REASON_MAX
