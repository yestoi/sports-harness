"""Conformance item 5: the transport backstop.

The refusal matrix and the host assertion are the reason nothing in phase 4 can reach the
production venue. Every test here is a fence, not a feature.
"""
import base64
import dataclasses
import logging

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from harness.feeds.http import HttpClient
from harness.venues.kalshi.http import (
    DEMO_HOST_SUFFIX, HostNotAllowed, KalshiTransport, PaperModeViolation, PROD_HOSTS,
)

PROD = "https://api.elections.kalshi.com/trade-api/v2"
DEMO = "https://external-api.demo.kalshi.co/trade-api/v2"

# A 2048-bit RSA key generated once at module import, the same way tests/test_kalshi_auth.py
# builds one inside its test body (there is no importable helper there to reuse).
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())

_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH)


def verify_signature(headers, method: str, path: str) -> bool:
    """Verify the RSA-PSS signature over f"{timestamp}{METHOD}{path}" with KEY_PEM's public half.

    httpx lowercases header names in ``dict(request.headers)``, so match case-insensitively.
    """
    h = {k.upper(): v for k, v in dict(headers).items()}
    msg = f"{h['KALSHI-ACCESS-TIMESTAMP']}{method.upper()}{path}".encode()
    _KEY.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg, _PSS,
                             hashes.SHA256())
    return True


class _StepClock:
    """A UTC clock that advances ``step_ms`` milliseconds on every call."""

    def __init__(self, step_ms: int, start: datetime | None = None) -> None:
        self._step = timedelta(milliseconds=step_ms)
        self._now = start or datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        now = self._now
        self._now = self._now + self._step
        return now


@pytest.fixture
def db_session_factory(_schema):
    """A sessionmaker bound to the branch test database.

    Defined here rather than in tests/conftest.py: session_recorder is the only consumer, and
    tests/conftest.py is shared with the other phase 4 tasks in flight. Truncates after, the
    same way the conftest db_session fixture does.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from harness.db.models import Base

    yield sessionmaker(bind=_schema)
    tables = ", ".join(sorted(Base.metadata.tables))
    with _schema.begin() as conn:
        conn.execute(text(f"truncate {tables} restart identity cascade"))


def _t(base_url=PROD, env="prod", writes_enabled=False, recorder=None, **kw):
    return KalshiTransport(HttpClient(5.0), base_url, env, "kid", KEY_PEM,
                           timeout_s=5.0, writes_enabled=writes_enabled,
                           recorder=recorder if recorder is not None else [].append, **kw)


# --- conformance item 5: the refusal matrix ------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("POST", "/portfolio/events/orders"),
    ("POST", "/communications/quotes"),
    ("POST", "/portfolio/events/orders/abc/amend"),
    ("DELETE", "/portfolio/events/orders/abc"),
    ("PUT", "/anything"),
    ("PATCH", "/anything"),
])
def test_transport_refuses_every_non_get_when_writes_disabled(method, path):
    rows = []
    t = _t(recorder=rows.append)
    with pytest.raises(PaperModeViolation) as exc:
        t.request(method, path)
    assert exc.value.method == method and exc.value.path == path
    assert rows == []          # refused before signing, before I/O, before recording


def test_transport_refuses_post_when_writes_disabled():
    with pytest.raises(PaperModeViolation):
        _t().request("POST", "/portfolio/events/orders", json={"ticker": "X"})


def test_transport_refuses_rfq_quote_post():
    with pytest.raises(PaperModeViolation):
        _t().request("POST", "/communications/quotes", json={})


def test_transport_refuses_a_lowercase_non_get():
    # The method is upper-cased before the refusal, so "post" cannot slip past the matrix.
    with pytest.raises(PaperModeViolation) as exc:
        _t().request("post", "/portfolio/events/orders")
    assert exc.value.method == "POST"


def test_refusal_precedes_the_host_check():
    # An unreachable host must not mask the paper-mode refusal: the refusal is the outer fence.
    with pytest.raises(PaperModeViolation):
        _t(base_url="https://evil.example/trade-api/v2").request("POST", "/anything")


def test_the_refusal_happens_before_sign_request_is_called(monkeypatch):
    # Conformance item 5: the refusal is upstream of signing, so a paper process never even
    # derives a signature for a write.
    import harness.venues.kalshi.http as mod

    def _boom(*a, **k):
        raise AssertionError("sign_request must not be reached when writes are disabled")

    monkeypatch.setattr(mod, "sign_request", _boom)
    with pytest.raises(PaperModeViolation):
        _t().request("POST", "/portfolio/events/orders", json={"ticker": "X"})


@respx.mock
def test_the_refusal_does_no_network_io():
    with pytest.raises(PaperModeViolation):
        _t().request("POST", "/portfolio/events/orders", json={"ticker": "X"})
    assert not respx.calls


def test_transport_builds_no_write_client_when_disabled():
    t = _t(writes_enabled=False)
    assert t._write_client is None
    t2 = _t(writes_enabled=True)
    assert isinstance(t2._write_client, httpx.Client)
    t.close()
    t2.close()


def test_http_client_has_no_write_methods():
    # The recorder path cannot issue a non-GET by construction (§1.1, D5).
    for name in ("post", "put", "delete", "patch", "request", "send"):
        assert not hasattr(HttpClient, name)


# --- host assertions (A-I14) ---------------------------------------------------------------

def test_prod_transport_refuses_a_host_outside_the_allowlist():
    with pytest.raises(HostNotAllowed):
        _t(base_url="https://api.kalshi.com/trade-api/v2").request("GET", "/exchange/status")


def test_demo_transport_refuses_a_production_host():
    with pytest.raises(HostNotAllowed):
        _t(base_url=PROD, env="demo").request("GET", "/exchange/status")


def test_prod_transport_refuses_a_demo_host():
    with pytest.raises(HostNotAllowed):
        _t(base_url=DEMO, env="prod").request("GET", "/exchange/status")


def test_host_check_is_on_the_parsed_hostname_not_the_url_string():
    evil = "https://evil.example/trade-api/v2?u=api.elections.kalshi.com"
    with pytest.raises(HostNotAllowed):
        _t(base_url=evil).request("GET", "/exchange/status")


def test_demo_suffix_check_is_on_a_label_boundary():
    # "notdemo.kalshi.co" ends in the suffix as a raw string but is a different registrable name.
    with pytest.raises(HostNotAllowed):
        _t(base_url="https://evil-demo.kalshi.co.attacker.test/trade-api/v2",
           env="demo").request("GET", "/exchange/status")


def test_an_unknown_env_is_refused():
    with pytest.raises(HostNotAllowed):
        _t(base_url=PROD, env="staging").request("GET", "/exchange/status")


def test_allowlist_constants_are_the_roadmap_values():
    assert PROD_HOSTS == frozenset({"api.elections.kalshi.com"})
    assert DEMO_HOST_SUFFIX == "demo.kalshi.co"


@respx.mock
def test_demo_transport_accepts_the_pinned_demo_host():
    respx.get(f"{DEMO}/portfolio/balance").respond(200, json={"balance": 0})
    rows = []
    r = _t(base_url=DEMO, env="demo", recorder=rows.append).request("GET", "/portfolio/balance")
    assert r.status == 200 and rows[0].env == "demo"


# --- signing (A-I6) -------------------------------------------------------------------------

@respx.mock
def test_signed_path_includes_the_api_prefix_and_excludes_the_query():
    seen = {}

    def _capture(request):
        seen["headers"] = dict(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    respx.get(f"{PROD}/portfolio/orders").mock(side_effect=_capture)
    _t().request("GET", "/portfolio/orders", params={"status": "resting"})
    # the signature was computed over "/trade-api/v2/portfolio/orders" with no query string
    assert verify_signature(seen["headers"], "GET", "/trade-api/v2/portfolio/orders")
    assert "status=resting" in seen["url"]      # the query was still sent


@respx.mock
def test_signing_timestamp_carries_the_clock_offset():
    captured = []

    def _capture(request):
        captured.append(request.headers["KALSHI-ACCESS-TIMESTAMP"])
        return httpx.Response(200, json={})

    respx.get(f"{PROD}/exchange/status").mock(side_effect=_capture)
    frozen = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    _t(clock_offset_ms=1500, clock=lambda: frozen).request("GET", "/exchange/status")
    assert int(captured[0]) == int(frozen.timestamp() * 1000) + 1500


@respx.mock
def test_the_key_id_is_sent_and_the_private_key_never_is():
    seen = {}

    def _capture(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200, json={})

    respx.get(f"{PROD}/exchange/status").mock(side_effect=_capture)
    _t().request("GET", "/exchange/status")
    assert seen["headers"]["kalshi-access-key"] == "kid"
    assert b"PRIVATE KEY" not in seen["body"]


# --- venue_requests rows (§1.1, D5) ----------------------------------------------------------

@respx.mock
def test_one_venue_request_row_per_attempt_with_no_headers_or_bodies():
    route = respx.get(f"{PROD}/exchange/status")
    route.side_effect = [httpx.Response(429, headers={"Retry-After": "0"}),
                         httpx.Response(200, json={"ok": True})]
    rows = []
    t = _t(recorder=rows.append, sleep=lambda _s: None)
    t.request("GET", "/exchange/status")
    assert [r.status for r in rows] == [429, 200]
    assert all(r.path == "/exchange/status" and r.venue == "kalshi" and r.env == "prod"
               for r in rows)
    assert all(not hasattr(r, "headers") and not hasattr(r, "body") for r in rows)
    assert t.counters["venue_429"] == 1


@respx.mock
def test_a_recorded_row_carries_a_utc_timestamp_and_an_elapsed_ms():
    respx.get(f"{PROD}/exchange/status").respond(200, json={})
    rows = []
    _t(recorder=rows.append).request("GET", "/exchange/status")
    assert rows[0].ts.tzinfo is not None and rows[0].elapsed_ms >= 0
    assert rows[0].method == "GET"


@respx.mock
def test_a_transport_error_records_a_row_with_a_null_status():
    respx.get(f"{PROD}/exchange/status").mock(side_effect=httpx.ConnectError("boom"))
    rows = []
    with pytest.raises(Exception):
        _t(recorder=rows.append, sleep=lambda _s: None).request("GET", "/exchange/status")
    assert len(rows) == 2 and all(r.status is None for r in rows)   # one attempt, one retry


# --- retries are re-signed (A-I5) -------------------------------------------------------------

@respx.mock
def test_every_retry_is_re_signed_with_a_fresh_timestamp():
    stamps = []
    route = respx.get(f"{PROD}/exchange/status")

    def _capture(request):
        stamps.append(request.headers["KALSHI-ACCESS-TIMESTAMP"])
        return httpx.Response(429 if len(stamps) < 3 else 200,
                              headers={"Retry-After": "0"}, json={})

    route.mock(side_effect=_capture)
    clock = _StepClock(step_ms=1000)
    _t(sleep=lambda _s: None, clock=clock).request("GET", "/exchange/status")
    assert len(stamps) == 3 and len(set(stamps)) == 3


@respx.mock
def test_429_retries_at_most_three_times_then_returns_the_response():
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "0"})
    rows = []
    r = _t(recorder=rows.append, sleep=lambda _s: None).request("GET", "/exchange/status")
    assert r.status == 429 and len(rows) == 4        # the first attempt plus three retries


@respx.mock
def test_retry_after_is_capped_at_ten_seconds():
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "600"})
    slept = []
    _t(recorder=[].append, sleep=slept.append).request("GET", "/exchange/status")
    assert slept and max(slept) == 10.0


@respx.mock
def test_a_missing_or_unparseable_retry_after_falls_back_to_two_seconds():
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "Wed, 21 Oct"})
    slept = []
    _t(recorder=[].append, sleep=slept.append).request("GET", "/exchange/status")
    assert slept == [2.0, 2.0, 2.0]


@respx.mock
def test_a_non_finite_retry_after_falls_back_to_the_default():
    # Retry-After is venue text and therefore untrusted: "nan" must not reach time.sleep.
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "nan"})
    slept = []
    _t(recorder=[].append, sleep=slept.append).request("GET", "/exchange/status")
    assert slept == [2.0, 2.0, 2.0]


@respx.mock
def test_a_negative_retry_after_is_clamped_to_zero():
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "-5"})
    slept = []
    _t(recorder=[].append, sleep=slept.append).request("GET", "/exchange/status")
    assert slept == [0.0, 0.0, 0.0]


@respx.mock
def test_the_429_counter_counts_attempts_not_calls():
    respx.get(f"{PROD}/exchange/status").respond(429, headers={"Retry-After": "0"})
    t = _t(recorder=[].append, sleep=lambda _s: None)
    t.request("GET", "/exchange/status")
    assert t.counters == {"venue_429": 4, "attempts": 4}
    t.counters["venue_429"] = 999                      # counters returns a copy
    assert t.counters["venue_429"] == 4


@respx.mock
def test_a_5xx_is_retried_once():
    route = respx.get(f"{PROD}/exchange/status")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={})]
    rows = []
    r = _t(recorder=rows.append, sleep=lambda _s: None).request("GET", "/exchange/status")
    assert r.status == 200 and [x.status for x in rows] == [503, 200]


@respx.mock
def test_a_persistent_5xx_is_returned_after_one_retry():
    respx.get(f"{PROD}/exchange/status").respond(503)
    rows = []
    r = _t(recorder=rows.append, sleep=lambda _s: None).request("GET", "/exchange/status")
    assert r.status == 503 and len(rows) == 2


@respx.mock
def test_a_4xx_that_is_not_429_is_returned_without_a_retry():
    respx.get(f"{PROD}/portfolio/balance").respond(401, json={"error": "unauthorized"})
    rows = []
    r = _t(recorder=rows.append, sleep=lambda _s: None).request("GET", "/portfolio/balance")
    assert r.status == 401 and len(rows) == 1


@respx.mock
def test_a_post_is_never_resent_after_a_timeout():
    # Non-idempotent: one attempt, one row, the error propagates for the caller to reconcile.
    respx.post(f"{PROD}/portfolio/events/orders").mock(side_effect=httpx.ReadTimeout("t"))
    rows = []
    t = _t(writes_enabled=True, recorder=rows.append, sleep=lambda _s: None)
    with pytest.raises(httpx.ReadTimeout):
        t.request("POST", "/portfolio/events/orders", json={"ticker": "X"})
    assert len(rows) == 1


@respx.mock
def test_a_post_that_returns_429_is_not_resent():
    respx.post(f"{PROD}/portfolio/events/orders").respond(429, headers={"Retry-After": "0"})
    rows = []
    t = _t(writes_enabled=True, recorder=rows.append, sleep=lambda _s: None)
    r = t.request("POST", "/portfolio/events/orders", json={"ticker": "X"})
    assert r.status == 429 and len(rows) == 1


@respx.mock
def test_a_write_goes_through_the_transport_private_client():
    respx.post(f"{PROD}/portfolio/events/orders").respond(201, json={"order": {}})
    rows = []
    t = _t(writes_enabled=True, recorder=rows.append)
    assert t.request("POST", "/portfolio/events/orders", json={"ticker": "X"}).status == 201
    assert rows[0].method == "POST"


@respx.mock
def test_a_write_never_touches_the_read_client():
    respx.post(f"{PROD}/portfolio/events/orders").respond(201, json={})
    t = _t(writes_enabled=True)
    t._read_client.request = lambda *a, **k: pytest.fail("a write used the read client")
    assert t.request("POST", "/portfolio/events/orders", json={"ticker": "X"}).status == 201


@respx.mock
def test_a_write_is_signed_over_the_prefixed_path():
    seen = {}

    def _capture(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(201, json={})

    respx.post(f"{PROD}/portfolio/events/orders").mock(side_effect=_capture)
    _t(writes_enabled=True).request("POST", "/portfolio/events/orders", json={"ticker": "X"})
    assert verify_signature(seen["headers"], "POST", "/trade-api/v2/portfolio/events/orders")


@respx.mock
def test_a_non_json_body_yields_a_none_body_not_an_error():
    respx.get(f"{PROD}/exchange/status").respond(200, text="not json")
    r = _t().request("GET", "/exchange/status")
    assert r.status == 200 and r.body is None


# --- the transport never hands a header value to the logging layer ---------------------------

@respx.mock
def test_a_signed_request_emits_no_log_record_carrying_a_header_value(caplog):
    """The guarantee this module owns.

    The roadmap-invariant-4 filter in harness/logging_setup.py redacts "HEADER: value" text but
    not a dict repr, so the transport does not rely on it: it never logs a header mapping at all.
    Asserted here at every level, on the record's msg, its rendered message and its args, using
    the real signature that went out on the wire.
    """
    seen = {}

    def _capture(request):
        seen.update({k.upper(): v for k, v in dict(request.headers).items()})
        return httpx.Response(200, json={"balance": 0})

    respx.get(f"{PROD}/portfolio/balance").mock(side_effect=_capture)
    rows = []
    caplog.set_level(logging.NOTSET)        # NOTSET on the root captures every level
    _t(recorder=rows.append).request("GET", "/portfolio/balance")

    secrets = [seen["KALSHI-ACCESS-KEY"], seen["KALSHI-ACCESS-SIGNATURE"],
               seen["KALSHI-ACCESS-TIMESTAMP"]]
    assert all(secrets) and len(secrets[1]) > 100        # a real RSA-PSS signature went out

    for record in caplog.records:
        blob = f"{record.msg!r} {record.getMessage()!r} {record.args!r}"
        for secret in secrets:
            assert secret not in blob, (
                f"{record.name} at {record.levelname} logged a signed header value")

    # And the venue_requests row carries no header field and no body field, by construction.
    (row,) = rows
    assert [f.name for f in dataclasses.fields(row)] == [
        "venue", "env", "method", "path", "status", "ts", "elapsed_ms"]
    row_blob = " ".join(str(getattr(row, f.name)) for f in dataclasses.fields(row))
    for secret in secrets:
        assert secret not in row_blob


def test_session_recorder_writes_a_row_per_call(db_session_factory):
    from harness.db.models import VenueRequest
    from harness.venues.kalshi.http import VenueRequestRow, session_recorder

    rec = session_recorder(db_session_factory)
    rec(VenueRequestRow("kalshi", "prod", "GET", "/exchange/status", 200,
                        datetime.now(timezone.utc), 12))
    with db_session_factory() as s:
        row = s.execute(select(VenueRequest)).scalar_one()
    assert row.method == "GET" and row.status == 200 and row.elapsed_ms == 12


def test_session_recorder_does_not_enlist_in_a_caller_transaction(db_session_factory):
    from harness.db.models import VenueRequest
    from harness.venues.kalshi.http import VenueRequestRow, session_recorder

    rec = session_recorder(db_session_factory)
    with db_session_factory() as caller:
        caller.execute(select(VenueRequest)).all()          # open a transaction
        rec(VenueRequestRow("kalshi", "demo", "GET", "/exchange/status", None,
                            datetime.now(timezone.utc), None))
        caller.rollback()                                    # the caller's work is discarded
    with db_session_factory() as s:
        rows = s.execute(select(VenueRequest)).scalars().all()
    assert len(rows) == 1 and rows[0].status is None and rows[0].elapsed_ms is None
