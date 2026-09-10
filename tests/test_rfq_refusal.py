"""The three refusal tests (addendum §1.6). Conformance item 5 is a structural claim about this
phase, and these are what make it checkable rather than asserted."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "harness" / "venues" / "kalshi" / "rfq.py"
SOCKET = ROOT / "harness" / "venues" / "kalshi" / "rfq_socket.py"


def test_the_handler_module_contains_no_send_no_post_and_no_quotes_path():
    """The listener module receives parsed frames only and never the socket object, so there is
    nothing in it that could send anything at all.

    The path assertion is the **literal** `communications/quotes`, which is what conformance
    item 5 names. A bare `quotes` would be tripped by this module's own prose about the venue's
    quote events and by the `rfq_quotes` table name, neither of which is a submission path."""
    body = HANDLER.read_text()
    assert ".send(" not in body
    assert "POST" not in body.upper().replace("POSTED", "")
    assert "communications/quotes" not in body.lower()


def test_the_socket_module_holds_no_rest_transport():
    """It has a `.send(` -- it owns the subscribe -- so the guarantee it carries instead is that
    it cannot make an HTTP request at all: no transport, no httpx, no authed client."""
    body = SOCKET.read_text()
    for forbidden in ("KalshiTransport", "httpx", "authed", "requests", "urllib"):
        assert forbidden not in body, f"rfq_socket.py imports {forbidden}"
    assert "communications/quotes" not in body.lower()


def test_the_transport_refuses_the_exact_quote_path(tmp_path):
    """`POST /communications/quotes` is the path phase 5 must never call, and this is where the
    refusal actually lives: `harness/venues/kalshi/http.py` raises before signing and before any
    network I/O, so a paper process cannot even build the request."""
    from harness.feeds.http import HttpClient
    from harness.venues.kalshi.http import KalshiTransport, PaperModeViolation
    # The brief names `tests/fixtures/test_key.pem`, which does not exist; `KEY_PEM` is the
    # repository's one generated test key and is what every other Kalshi transport test uses.
    # The refusal is raised before signing, so the key is never parsed either way.
    from tests.test_kalshi_transport import KEY_PEM

    transport = KalshiTransport(HttpClient(1.0), "https://api.elections.kalshi.com/trade-api/v2",
                                "prod", "abc", KEY_PEM,
                                timeout_s=1.0, writes_enabled=False, recorder=None)
    try:
        with pytest.raises(PaperModeViolation) as caught:
            transport.request("POST", "/communications/quotes",
                              json={"rfq_id": "x", "yes_bid": 1, "no_bid": 1,
                                    "rest_remainder": False})
        assert caught.value.method == "POST"
        assert caught.value.path == "/communications/quotes"
    finally:
        transport.close()


def test_no_module_in_the_repository_names_the_quote_path():
    """The whole-repository half. `docs/` and this file are excluded: naming the forbidden path
    in a plan, a runbook or the test that refuses it is the point."""
    offenders = []
    for path in sorted((ROOT / "harness").rglob("*.py")):
        if "communications/quotes" in path.read_text():
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
