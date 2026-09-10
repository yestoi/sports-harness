"""F60's sanitizer for prose that came from a model or from a venue.

Ruling A-I5. `harness.telemetry.sanitize_reason` is the wrong function for this and is left
exactly as it is: it caps at 200 characters (so the 300-character veto reason and the
600-character parlay rationale are unreachable through it), it strips the apostrophe (which
mangles the fan voice spec 8.1 asks for), and it does not apply the F55 patterns.

Order matters and is the whole design:

1. **Redact first**, on the whole original string. `harness.logging_setup.redact` is the one
   home for the F55 patterns; truncating or stripping markup before it could cut an `sk-ant-...`
   string in half and leave a prefix the pattern no longer matches.
2. Strip markup, then control characters, then collapse whitespace.
3. Truncate to the caller's limit, last.

This is not an instruction filter and does not pretend to be one. It makes a string safe to
**store and render**. What makes an untrusted string safe to **read** is elsewhere: the veto's
prompt puts retrieved text in a fixed untrusted block, defaults to `proceed`, and requires a
quoted evidence id for anything else.
"""
import re

from harness.logging_setup import redact

#: F60: "The veto `reason` is capped at 300 characters with control characters and markup
#: stripped" (R:247-251).
VETO_REASON_MAX = 300
#: `parlay_cards.rationale` is `String(600)` (ruling B-M11), and the column is the cap.
RATIONALE_MAX = 600

#: Anything that looks like a tag. Deliberately greedy on the tag itself and nothing else: the
#: text between tags is kept, because a model that wrote "<b>LSU</b> covers" meant "LSU covers".
_MARKUP = re.compile(r"<[^>]*>")
#: Every C0 control character and DEL. Newline and tab are handled by the whitespace collapse
#: below instead of being deleted, so "one\n\ntwo" does not become "onetwo".
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def sanitize_model_text(text, limit: int) -> str:
    """One string of model or venue provenance, made safe to store and to render.

    `limit` is the caller's, never a constant in here: 300 for a veto reason, 600 for a parlay
    rationale, 240 for an annotator bullet, 120 for an RFQ excerpt, 80 for a forecast phrase.
    """
    if limit <= 0:
        raise ValueError(f"sanitize_model_text needs a positive limit, got {limit!r}")
    if text is None:
        return ""
    value = redact(str(text))
    value = _MARKUP.sub("", value)
    value = _CONTROL.sub("", value)
    return _WHITESPACE.sub(" ", value).strip()[:limit]
