"""Task 6b: the recorder's hourly `GET /account/limits` on a signed, GET-only reader.

Ruling A-C3: limits are read on the *reader*, because no writer exists in production. The
recorder builds a `KalshiReader` at startup only when both production key files are files,
calls `get_account_limits()` then and hourly, puts the tier and the buckets into
`runs.notes.venue_limits` and the Health block, and floors its Kalshi page pause at
`1 / read_refill_rate` -- never below `Settings.kalshi_sleep_s`.

This is also the only production writer of `venue_requests`. Without it the §3 paper-posture
tripwire ("non-GET on prod = 0") is vacuous on an empty table and Task 16's A-I8 verify row
(`count(*) where env = 'prod' and method = 'GET'` must be > 0) can never be satisfied, so the
last two tests here are about the row as much as about the feature.

Nothing here opens a socket to Kalshi and nothing here reads a file under `secrets/`: the
recorder tests drive `RecordingFakeTransport` (Task 6's `FakeTransport` plus Task 5's
`session_recorder`), the credential tests write throwaway key files under `tmp_path`, and the
one end-to-end test drives the real `KalshiTransport` over `respx`.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from harness.db.models import VenueRequest
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.recorder.tick import LIMITS_REFRESH_S, Recorder, page_pause_s
from harness.scheduler import build_recorder
from harness.venues.kalshi.authed import KalshiReader
from harness.venues.kalshi.http import (
    KalshiTransport, PaperModeViolation, VenueRequestRow, VenueTransportError, session_recorder,
)
from harness.venues.kalshi.public import KalshiPublic
from tests.test_kalshi_authed import FakeTransport, _err, _ok
from tests.test_kalshi_transport import KEY_PEM, PROD

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # the same tick clock tests/test_tick.py uses

LIMITS_BODY = {"tier": "basic",
               "read": {"refill_rate": "10", "capacity": "100"},
               "write": {"refill_rate": "5", "capacity": "50"}}

NOTE_FIELDS = {"tier", "read_refill_rate", "read_capacity",
               "write_refill_rate", "write_capacity", "page_pause_s", "read_at"}


# --- the pause floor -----------------------------------------------------------------------

@pytest.mark.parametrize("setting,rate,expected", [
    (0.05, Decimal("10"), 0.1),      # the venue is slower than us: floor rises
    (0.05, Decimal("100"), 0.05),    # the venue is faster: the setting wins
    (0.5,  Decimal("10"), 0.5),      # never lower than the setting
    (0.05, None, 0.05),              # unreadable bucket: unchanged
    (0.05, Decimal("0"), 0.05),      # nonsense bucket: unchanged
    (0.05, Decimal("-1"), 0.05),
])
def test_page_pause_is_floored_by_the_read_bucket_and_never_lowered(setting, rate, expected):
    assert page_pause_s(setting, rate) == pytest.approx(expected)


# --- the credential gate (plan-review round 2, N1) ------------------------------------------

@pytest.fixture
def env_settings_with_keys(env_settings, monkeypatch, tmp_path):
    """Settings pointing at throwaway key files. The PEM is the test suite's own RSA key, so a
    signature could actually be produced; no test here signs anything."""
    key_id, pem = tmp_path / "kalshi_key_id", tmp_path / "kalshi_private_key.pem"
    key_id.write_text("kid-123")
    pem.write_bytes(KEY_PEM)
    monkeypatch.setenv("KALSHI_KEY_ID_FILE", str(key_id))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_FILE", str(pem))
    from harness.config.settings import Settings

    return Settings()


def test_credentials_are_present_only_when_both_paths_are_files(env_settings_with_keys):
    assert env_settings_with_keys.has_kalshi_credentials() is True


def test_an_unmounted_secret_is_a_directory_and_is_not_a_credential(env_settings, monkeypatch,
                                                                    tmp_path):
    """N1: Compose materialises a missing bind source as an empty *directory*, so `exists()`
    would be True with nothing behind it and `read_text()` would raise inside the container."""
    key_id, pem = tmp_path / "as_dir_key_id", tmp_path / "as_dir_key.pem"
    key_id.mkdir()
    pem.mkdir()
    monkeypatch.setenv("KALSHI_KEY_ID_FILE", str(key_id))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_FILE", str(pem))
    from harness.config.settings import Settings

    settings = Settings()
    assert key_id.exists() and pem.exists()
    assert settings.has_kalshi_credentials() is False


# --- the recorder wiring --------------------------------------------------------------------

def test_no_limits_reader_is_built_without_the_credential_files(env_settings):
    assert env_settings.has_kalshi_credentials() is False
    assert build_recorder(env_settings)._limits_reader is None


def test_the_limits_reader_is_get_only(env_settings_with_keys):
    reader = build_recorder(env_settings_with_keys)._limits_reader
    try:
        assert reader._transport._writes_enabled is False
        assert reader._transport._write_client is None
        with pytest.raises(PaperModeViolation):
            reader._transport.request("POST", "/portfolio/events/orders")
    finally:
        reader._transport.close()


# --- the tick ------------------------------------------------------------------------------

@dataclass
class RecordingFakeTransport(FakeTransport):
    """`FakeTransport` plus Task 5's `session_recorder`.

    `KalshiTransport` writes one `venue_requests` row per attempt, failed attempts included;
    the fake has to do the same or the tripwire test below would be testing nothing. The
    end-to-end test at the bottom of this module drives the real transport instead.
    """
    recorder: Callable[[VenueRequestRow], None] | None = None
    ts: datetime = NOW

    def request(self, method, path, params=None, json=None):
        status = None
        try:
            result = super().request(method, path, params, json)
            status = result.status
            return result
        finally:
            if self.recorder is not None:
                self.recorder(VenueRequestRow("kalshi", self.env, method.upper(), path,
                                              status, self.ts, 5))


def _mock_public_feeds() -> None:
    """One catch-all for the unauthenticated feeds the tick fetches, so these tests are about
    the limits read and nothing else (the same shape tests/test_tick.py uses)."""
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(
        200, json={"events": [], "markets": [], "trades": []}))


def _recorder(settings, db_session, queued, now=NOW):
    clock = {"now": now}
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: clock["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=settings.kalshi_sleep_s,
                          sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    transport = RecordingFakeTransport(queued=list(queued), recorder=session_recorder(factory))
    rec = Recorder(settings, factory, odds, espn, kalshi, clock=lambda: clock["now"],
                   monotonic=time.monotonic, limits_reader=KalshiReader(transport))
    return rec, transport


@pytest.fixture
def recorder_with_fake(env_settings, db_session):
    # Three bodies queued: the hourly-cadence test reads twice, every other test reads once.
    return _recorder(env_settings, db_session, [_ok(LIMITS_BODY) for _ in range(3)])


@pytest.fixture
def recorder_with_fake_401(env_settings, db_session):
    return _recorder(env_settings, db_session, [_err(401, code="unauthorized")])


@pytest.fixture
def recorder_with_fake_network_error(env_settings, db_session):
    return _recorder(env_settings, db_session,
                     [VenueTransportError("GET", "/account/limits", "ConnectError")])


@respx.mock
def test_the_first_tick_reads_limits_and_writes_the_run_note(db_session, recorder_with_fake):
    _mock_public_feeds()
    rec, t = recorder_with_fake
    run = rec.maybe_tick(force=True)
    assert t.calls[0][1] == "/account/limits"
    note = run.notes["venue_limits"]
    assert note["tier"] == "basic" and note["read_refill_rate"] == 10.0
    assert note["read_capacity"] == 100.0
    assert note["write_refill_rate"] == 5.0 and note["write_capacity"] == 50.0
    assert note["page_pause_s"] == pytest.approx(0.1)
    assert note["read_at"] == NOW.isoformat()


@respx.mock
def test_limits_are_re_read_hourly_not_every_tick(recorder_with_fake):
    _mock_public_feeds()
    rec, t = recorder_with_fake
    rec.maybe_tick(force=True)
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 1
    rec.clock = lambda: NOW + timedelta(seconds=LIMITS_REFRESH_S + 1)
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 2


@respx.mock
def test_a_tick_inside_the_hour_still_reports_the_limits_in_force(recorder_with_fake):
    """The cadence bounds the venue call, not the note: a run written between two reads still
    carries the limits the pause is actually sized from, so the Health block never blinks."""
    _mock_public_feeds()
    rec, _ = recorder_with_fake
    first = rec.maybe_tick(force=True).notes["venue_limits"]
    second = rec.maybe_tick(force=True).notes["venue_limits"]
    assert second == first


@respx.mock
def test_the_read_applies_the_pause_floor_to_the_public_client(recorder_with_fake):
    _mock_public_feeds()
    rec, _ = recorder_with_fake
    assert rec.kalshi._sleep_s == 0.05
    rec.maybe_tick(force=True)
    assert rec.kalshi._sleep_s == pytest.approx(0.1)


@respx.mock
def test_a_fast_venue_bucket_never_lowers_the_configured_pause(env_settings, db_session):
    _mock_public_feeds()
    body = dict(LIMITS_BODY, read={"refill_rate": "100", "capacity": "1000"})
    rec, _ = _recorder(env_settings, db_session, [_ok(body)])
    rec.maybe_tick(force=True)
    assert rec.kalshi._sleep_s == pytest.approx(env_settings.kalshi_sleep_s)


@respx.mock
def test_a_failed_limits_read_leaves_the_pause_at_the_setting(recorder_with_fake_401):
    _mock_public_feeds()
    rec, _ = recorder_with_fake_401
    run = rec.maybe_tick(force=True)
    assert rec.kalshi._sleep_s == 0.05
    assert run.notes["venue_limits"] is None
    assert any("limits" in str(w) for w in run.notes["warnings"])


@respx.mock
def test_a_401_counts_toward_the_auth_error_rule_and_a_success_resets_it(env_settings,
                                                                        db_session):
    """§9.4 counts 401 and 403 only. Task 10's OutageCounter subsumes this integer; the
    outage marking itself is the authenticated path's job (ruling D11)."""
    _mock_public_feeds()
    rec, _ = _recorder(env_settings, db_session,
                       [_err(401, code="unauthorized"), _ok(LIMITS_BODY)])
    rec.maybe_tick(force=True)
    assert rec._limits_auth_errors == 1
    rec.clock = lambda: NOW + timedelta(seconds=LIMITS_REFRESH_S + 1)
    rec.maybe_tick(force=True)
    assert rec._limits_auth_errors == 0


@respx.mock
def test_a_429_does_not_count_toward_the_auth_error_rule(env_settings, db_session):
    _mock_public_feeds()
    rec, _ = _recorder(env_settings, db_session, [_err(429)])
    rec.maybe_tick(force=True)
    assert rec._limits_auth_errors == 0


@respx.mock
def test_a_failed_limits_read_never_fails_the_tick(recorder_with_fake_network_error):
    _mock_public_feeds()
    rec, _ = recorder_with_fake_network_error
    assert rec.maybe_tick(force=True).status != "error"


@respx.mock
def test_a_recorder_without_a_reader_records_no_limits_and_keeps_its_pause(env_settings,
                                                                          db_session):
    _mock_public_feeds()
    rec, _ = _recorder(env_settings, db_session, [])
    rec._limits_reader = None
    run = rec.maybe_tick(force=True)
    assert run.notes["venue_limits"] is None
    assert rec.kalshi._sleep_s == 0.05


@respx.mock
def test_the_run_note_carries_no_raw_venue_body(recorder_with_fake):
    _mock_public_feeds()
    rec, _ = recorder_with_fake
    note = rec.maybe_tick(force=True).notes["venue_limits"]
    assert set(note) == NOTE_FIELDS


@respx.mock
def test_a_hostile_tier_string_is_escaped_and_truncated_in_the_note(env_settings, db_session):
    """`tier` is the one venue *string* that reaches `runs.notes` and the dashboard, so it gets
    the global constraint's treatment: ASCII-escaped, control characters stripped, truncated."""
    _mock_public_feeds()
    body = dict(LIMITS_BODY, tier="ba\nsic\x00é" + "x" * 200)
    rec, _ = _recorder(env_settings, db_session, [_ok(body)])
    note = rec.maybe_tick(force=True).notes["venue_limits"]
    assert "\n" not in note["tier"] and "\x00" not in note["tier"]
    assert note["tier"].isascii() and len(note["tier"]) <= 32


# --- the tripwire is no longer vacuous ---------------------------------------------------------

@respx.mock
def test_the_limits_read_writes_a_prod_get_venue_request_row(db_session, recorder_with_fake):
    _mock_public_feeds()
    rec, _ = recorder_with_fake
    rec.maybe_tick(force=True)
    rows = db_session.execute(select(VenueRequest)).scalars().all()
    assert rows and all(r.env == "prod" and r.method == "GET" for r in rows)
    assert any(r.path == "/account/limits" for r in rows)


@respx.mock
def test_the_real_transport_records_the_prod_get_row_for_the_limits_read(db_session):
    """The fake stands in for the transport everywhere else; here the real `KalshiTransport`
    writes the row, so what Task 16's A-I8 verify row counts is production code."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    route = respx.get(PROD + "/account/limits").mock(
        return_value=httpx.Response(200, json=LIMITS_BODY))
    http = HttpClient(5.0)
    transport = KalshiTransport(http, PROD, "prod", "kid", KEY_PEM, timeout_s=5.0,
                                writes_enabled=False, recorder=session_recorder(factory))
    try:
        limits = KalshiReader(transport).get_account_limits()
    finally:
        transport.close()
        http.close()
    assert route.called and limits.tier == "basic"
    rows = db_session.execute(select(VenueRequest)).scalars().all()
    assert [(r.venue, r.env, r.method, r.path, r.status) for r in rows] == [
        ("kalshi", "prod", "GET", "/account/limits", 200)]
