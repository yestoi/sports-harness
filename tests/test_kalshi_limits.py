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
from harness.recorder.tick import (
    LIMITS_REFRESH_S, PAGE_PAUSE_CEILING_S, Recorder, page_pause_s,
)
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

NOTE_FIELDS = {"tier", "read_refill_rate", "read_capacity", "write_refill_rate",
               "write_capacity", "page_pause_s", "read_at", "age_s"}


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


@pytest.mark.parametrize("rate,expected", [
    (Decimal("0.000001"), PAGE_PAUSE_CEILING_S),   # the ruled case: 1e6 s becomes the ceiling
    (Decimal("0.2"), PAGE_PAUSE_CEILING_S),        # exactly at the ceiling, not clamped further
    (Decimal("0.5"), 2.0),                         # under the ceiling: untouched
])
def test_page_pause_is_capped_by_the_ceiling(rate, expected):
    """Fix round 1, Important 2. `KalshiPublic._pause` sleeps this after every page and the
    paging loops have no budget check inside them, so an unbounded venue number would stop the
    recorder's Kalshi fetching indefinitely."""
    assert page_pause_s(0.05, rate) == pytest.approx(expected)


def test_the_ceiling_never_lowers_the_operator_setting():
    # The ceiling bounds the venue's contribution, not the operator's own trusted setting.
    assert page_pause_s(9.0, Decimal("0.000001")) == pytest.approx(9.0)


@pytest.mark.parametrize("rate", [
    Decimal("1e-400"),   # finite as a Decimal, underflows to 0.0 as a float -> used to divide by zero
    Decimal("1e400"),    # finite as a Decimal, overflows to inf as a float
    Decimal("0"),
    Decimal("-1"),
    float("nan"),
])
def test_a_pathological_rate_leaves_the_setting_rather_than_raising(rate):
    assert page_pause_s(0.05, rate) == pytest.approx(0.05)


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
    """A recorder whose wall clock and monotonic clock are both controllable. The limits cadence
    runs on the monotonic one (fix round 1, Minor), so `_advance` moves both together."""
    clock = {"now": now, "mono": 1000.0}
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: clock["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=settings.kalshi_sleep_s,
                          sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    transport = RecordingFakeTransport(queued=list(queued), recorder=session_recorder(factory))
    rec = Recorder(settings, factory, odds, espn, kalshi, clock=lambda: clock["now"],
                   monotonic=lambda: clock["mono"], limits_reader=KalshiReader(transport))
    rec._test_clock = clock
    return rec, transport


def _advance(rec, seconds: float) -> None:
    rec._test_clock["now"] = rec._test_clock["now"] + timedelta(seconds=seconds)
    rec._test_clock["mono"] = rec._test_clock["mono"] + seconds


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
    _advance(rec, LIMITS_REFRESH_S + 1)
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 2


@respx.mock
def test_the_hour_boundary_is_inclusive(recorder_with_fake):
    _mock_public_feeds()
    rec, t = recorder_with_fake
    rec.maybe_tick(force=True)
    _advance(rec, LIMITS_REFRESH_S - 1)
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 1
    _advance(rec, 1)                                    # exactly LIMITS_REFRESH_S since the read
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 2


@respx.mock
def test_the_cadence_survives_a_backwards_wall_clock_step(recorder_with_fake):
    """Fix round 1, Minor: the cadence runs on the monotonic clock, so an NTP step backwards
    cannot defer the next read by the size of the jump."""
    _mock_public_feeds()
    rec, t = recorder_with_fake
    rec.maybe_tick(force=True)
    rec._test_clock["now"] = NOW - timedelta(hours=6)    # the wall clock jumps backwards
    rec._test_clock["mono"] += LIMITS_REFRESH_S + 1      # an hour of real time still passed
    rec.maybe_tick(force=True)
    assert sum(1 for c in t.calls if c[1] == "/account/limits") == 2


@respx.mock
def test_a_tick_inside_the_hour_still_reports_the_limits_in_force(recorder_with_fake):
    """The cadence bounds the venue call, not the note: a run written between two reads still
    carries the limits the pause is actually sized from, so the Health block never blinks."""
    _mock_public_feeds()
    rec, _ = recorder_with_fake
    first = rec.maybe_tick(force=True).notes["venue_limits"]
    _advance(rec, 120)
    second = rec.maybe_tick(force=True).notes["venue_limits"]
    assert first["age_s"] == 0.0 and second["age_s"] == pytest.approx(120.0)
    assert {k: v for k, v in second.items() if k != "age_s"} == \
           {k: v for k, v in first.items() if k != "age_s"}


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
    _advance(rec, LIMITS_REFRESH_S + 1)
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


@respx.mock
@pytest.mark.parametrize("rate", ["1e-400", "1e400", "0", "-1", "NaN"])
def test_a_pathological_venue_rate_never_aborts_the_tick(env_settings, db_session, rate):
    """Fix round 1, Important 1. `1e-400` and `1e400` are finite Decimals, so `dec` passes them
    through; the first used to underflow to 0.0 and raise `ZeroDivisionError` out of `maybe_tick`,
    the second used to reach `runs.notes` as `inf`, which Postgres rejects for `jsonb` and which
    would have failed `finish_run` and left the run row unfinished."""
    _mock_public_feeds()
    body = dict(LIMITS_BODY, read={"refill_rate": rate, "capacity": "100"})
    rec, _ = _recorder(env_settings, db_session, [_ok(body)])
    run = rec.maybe_tick(force=True)
    assert run.status != "error"
    assert run.notes["venue_limits"] is None       # an unusable number is a failed read
    assert rec.kalshi._sleep_s == 0.05             # the pause is untouched
    assert run.finished_at is not None             # the run row was finished, not abandoned


@respx.mock
def test_an_undecodable_venue_rate_reads_as_a_missing_bucket(env_settings, db_session):
    """`dec` returns None for text it cannot parse at all, which is the venue not sending a
    usable number rather than sending an out-of-range one: the note carries null for the field
    and the pause stays at the setting."""
    _mock_public_feeds()
    body = dict(LIMITS_BODY, read={"refill_rate": "not-a-number", "capacity": "100"})
    rec, _ = _recorder(env_settings, db_session, [_ok(body)])
    note = rec.maybe_tick(force=True).notes["venue_limits"]
    assert note["read_refill_rate"] is None and rec.kalshi._sleep_s == 0.05


@respx.mock
def test_a_pathological_venue_capacity_is_a_failed_read(env_settings, db_session):
    _mock_public_feeds()
    body = dict(LIMITS_BODY, read={"refill_rate": "10", "capacity": "1e400"})
    rec, _ = _recorder(env_settings, db_session, [_ok(body)])
    run = rec.maybe_tick(force=True)
    assert run.notes["venue_limits"] is None and rec.kalshi._sleep_s == 0.05


@respx.mock
def test_a_missing_bucket_is_not_a_failed_read(env_settings, db_session):
    """An absent field is not an out-of-range one: the note carries null for it and the pause
    stays at the setting, which is the brief's `page_pause_s(setting, None)` case."""
    _mock_public_feeds()
    rec, _ = _recorder(env_settings, db_session, [_ok({"tier": "basic"})])
    note = rec.maybe_tick(force=True).notes["venue_limits"]
    assert note is not None and note["read_refill_rate"] is None
    assert note["page_pause_s"] == pytest.approx(0.05) and rec.kalshi._sleep_s == 0.05


@respx.mock
def test_the_run_note_is_json_serialisable_for_jsonb(recorder_with_fake):
    import json

    _mock_public_feeds()
    rec, _ = recorder_with_fake
    note = rec.maybe_tick(force=True).notes["venue_limits"]
    # allow_nan=False is what a jsonb-safe encoder does: bare Infinity/NaN are not valid JSON.
    json.dumps(note, allow_nan=False)


# --- an unusable credential costs the reader, never the process (fix round 1, Important 3) ----

def test_an_unreadable_key_file_leaves_the_recorder_without_a_reader(env_settings_with_keys):
    key_id = env_settings_with_keys.kalshi_key_id_file
    key_id.chmod(0o000)
    try:
        assert env_settings_with_keys.has_kalshi_credentials() is True   # is_file() is still True
        rec = build_recorder(env_settings_with_keys)
    finally:
        key_id.chmod(0o600)
    assert rec._limits_reader is None                                    # and the process started


def test_a_malformed_pem_leaves_the_recorder_without_a_reader(env_settings_with_keys):
    env_settings_with_keys.kalshi_private_key_file.write_bytes(b"-----BEGIN PRIVATE KEY-----\nnope\n")
    rec = build_recorder(env_settings_with_keys)
    assert rec._limits_reader is None


def test_close_releases_the_limits_transport(env_settings_with_keys):
    rec = build_recorder(env_settings_with_keys)
    assert rec._limits_reader is not None
    rec.close()
    rec.close()          # idempotent: the one-shot CLI path calls it from a finally


def test_close_without_a_reader_is_a_no_op(env_settings):
    build_recorder(env_settings).close()


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
