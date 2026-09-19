"""§1.6(e)-(i): a bounded cohort, a per-sport endpoint, and a refusal that never retries."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from harness.experiments.execution_viability import observer
from harness.experiments.execution_viability.observer import (CREDITS_PER_CALL, SKIPPED_BUDGET,
                                                              budget_ok, observe_once,
                                                              observer_pass, select_cohort)
from harness.experiments.execution_viability.storage import ExperimentWriter
from harness.feeds.http import FetchResult

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
SPORTS = ["americanfootball_nfl", "americanfootball_ncaaf"]
RUN = "0198e2b0-0000-7000-8000-000000000007"
SEED = 20260916
#: The recorder's freshest balance, the number the observer's first call is judged against
#: (§3 row 5's `recorder.credits_remaining`); an unknown balance is a refusal (I9), so a run
#: that has never seen one makes no call at all and these fixtures would observe nothing.
RECORDER_BALANCE = 5_000_000
_PAYLOAD = json.dumps([{"id": "e1", "sport_key": "americanfootball_nfl",
                        "commence_time": "2026-09-20T17:00:00Z", "home_team": "DET",
                        "away_team": "BAL", "bookmakers": []}]).encode()


class _FakeOdds:
    """An `OddsApiClient` stand-in. `fetch_featured` returns a real `FetchResult`
    (harness/feeds/http.py:17) -- the same object the production client returns, so the code
    under test parses the same `.headers` and `.body` it will parse live."""

    def __init__(self, *, remaining: int, last: int = 3):
        self.calls: list[str] = []
        self._headers = {"x-requests-last": str(last), "x-requests-used": "12",
                         "x-requests-remaining": str(remaining)}

    def fetch_featured(self, sport: str) -> FetchResult:
        self.calls.append(sport)
        return FetchResult(status=200, headers=dict(self._headers), body=_PAYLOAD,
                           fetched_at=NOW, url=f"/v4/sports/{sport}/odds", elapsed_s=0.12)


def _rows(session, run_id):
    return session.execute(text(
        "select status, credits, credits_last, credits_remaining, body_path, body_sha256 "
        "from exp_observation where run_id = :r order by id"), {"r": run_id}).all()


# --- the file's three helpers -----------------------------------------------------------------


def writer_for(session):
    """T1's `ExperimentWriter` on the test session: `open()` connects as §1.1(b)'s role, which
    no test of this milestone may need, and the writer object itself is what the observer uses."""
    return ExperimentWriter(session, run_id=RUN, batch_rows=20_000)


def seed_eligible_games(session, *, now, nfl, ncaaf):
    """`nfl` + `ncaaf` games inside §1.6(f)'s 24-120 hour window, each with one confidently
    matched moneyline venue market (`plan.confidently_matched`)."""
    made = []
    for sport, count in (("nfl", nfl), ("ncaaf", ncaaf)):
        for i in range(count):
            kickoff = now + timedelta(hours=24) + timedelta(hours=4 * i)
            home, away = 1000 + i, 2000 + i
            game_id = session.execute(text(
                "insert into games (sport, home_team_id, away_team_id, kickoff_utc, "
                "odds_api_event_id, status) values (:s, :h, :a, :k, :e, 'scheduled') "
                "returning id"),
                {"s": sport, "h": home, "a": away, "k": kickoff,
                 "e": f"evt-{sport}-{i}"}).scalar()
            session.execute(text(
                "insert into venue_markets (venue, ticker, event_ticker, series_ticker, "
                "game_id, market_type, side_team_id, side, match_confidence, match_status, "
                "match_reason, first_seen_raw_id, last_seen_at, exchange_index) values "
                "('kalshi', :t, :et, 'KXNFLGAME', :g, 'moneyline', :team, 'yes', 1.00, "
                "'matched', 'fixture', 1, :seen, 0)"),
                {"t": f"KX-{sport}-{i}", "et": f"KXE-{sport}-{i}", "g": game_id,
                 "team": home, "seen": now})
            made.append(game_id)
    session.flush()
    return made


def seed_run_and_cohort(session, settings, *, games):
    """A frozen `exp_run` row and its cohort (§1.6g), plus the recorder's freshest balance.

    `games` is split evenly between the two sports, so `games=8` is §1.6(f)'s four and four.
    """
    if str(settings.exp_dir).startswith("/srv"):
        # The fixture tree, not the runtime's: `env_settings` already owns a `tmp_path`.
        object.__setattr__(settings, "exp_dir",
                           settings.odds_api_key_file.parent / "exp")
    per_sport = max(1, games // 2)
    seed_eligible_games(session, now=NOW, nfl=per_sport, ncaaf=per_sport)
    session.execute(text(
        "insert into metric_samples (ts, source, name, labels, value) values "
        "(:ts, 'recorder', 'recorder.credits_remaining', '{}'::jsonb, :v)"),
        {"ts": NOW - timedelta(seconds=30), "v": Decimal(RECORDER_BALANCE)})
    selection = select_cohort(session, now=NOW, seed=SEED, freeze_at=NOW)
    manifest = {"selection_seed": SEED,
                "cohort": [g.canonical_game_id for g in selection.games]}
    session.execute(text(
        "insert into exp_run (run_id, created_at, manifest_hash, manifest, code_sha, "
        "clock_mode, status) values (:r, :c, :h, cast(:m as jsonb), :sha, "
        "'retained_action_instants', 'frozen')"),
        {"r": RUN, "c": NOW - timedelta(minutes=5), "h": "a" * 64,
         "m": json.dumps(manifest), "sha": "0" * 40})
    session.flush()
    return observer.begin_run(session, settings, run_id=RUN, now=NOW)


# --- §1.6(e)-(i) --------------------------------------------------------------------------------


def test_the_endpoint_is_per_sport_so_the_cohort_size_does_not_change_the_cost(db_session,
                                                                              env_settings):
    # §10 bullet 2: two calls per interval regardless of how many games are in the cohort.
    run = seed_run_and_cohort(db_session, env_settings, games=8)     # 4 nfl + 4 ncaaf
    client = _FakeOdds(remaining=4_000_000)
    observe_once(db_session, NOW, env_settings, client, run=run, writer=writer_for(db_session))
    assert client.calls == SPORTS


def test_each_interval_costs_six_credits(db_session, env_settings):
    run = seed_run_and_cohort(db_session, env_settings, games=8)
    client = _FakeOdds(remaining=4_000_000)
    credits_written = observe_once(db_session, NOW, env_settings, client, run=run,
                                   writer=writer_for(db_session))
    assert credits_written == 2 * CREDITS_PER_CALL == 6
    assert sum(r.credits for r in _rows(db_session, run.run_id) if r.credits) == 6


def test_the_run_cap_stops_the_observer_and_labels_the_unmade_reads(db_session, env_settings):
    object.__setattr__(env_settings, "exp_observer_credit_cap", 6)
    run = seed_run_and_cohort(db_session, env_settings, games=8)
    client = _FakeOdds(remaining=4_000_000)
    writer = writer_for(db_session)
    observe_once(db_session, NOW, env_settings, client, run=run, writer=writer)
    observe_once(db_session, NOW + timedelta(minutes=2), env_settings, client, run=run,
                 writer=writer)
    rows = _rows(db_session, run.run_id)
    assert rows[-1].status == SKIPPED_BUDGET == "exp_skipped_budget"
    assert rows[-1].credits_remaining is None
    assert client.calls == SPORTS                       # the second interval made no call at all


def test_the_recorders_own_guard_refuses_on_the_providers_balance(env_settings):
    # tick.py:1411's expression, on the provider's number: 0.40 x 5,000,000 = 2,000,000.
    ok, reason = budget_ok(env_settings, credits_used=0, remaining=1_999_999)
    assert ok is False and reason == "credits_watch_fraction"
    assert budget_ok(env_settings, credits_used=0, remaining=2_000_001)[0] is True
    assert budget_ok(env_settings, credits_used=0, remaining=None) == (
        False, "credits_watch_fraction")                # unknown is a refusal, not a pass


def test_every_row_carries_the_providers_last_and_remaining(db_session, env_settings):
    run = seed_run_and_cohort(db_session, env_settings, games=2)
    client = _FakeOdds(remaining=4_000_000, last=3)
    observe_once(db_session, NOW, env_settings, client, run=run, writer=writer_for(db_session))
    rows = _rows(db_session, run.run_id)
    assert rows[0].credits_last == 3 and rows[0].credits_remaining == 4_000_000
    assert all(r.credits_remaining == 4_000_000 for r in rows if r.status != SKIPPED_BUDGET)


def test_the_observer_never_writes_source_state():
    # I9: the shared aggregate is the provider's balance, not a row in our database.
    source = Path(observer.__file__).read_text()
    assert "source_state" not in source
    assert "set_source_state" not in source


def test_nothing_writes_fair_values_or_calls_a_compute_entry_point():
    # I12: the pure arithmetic only.
    source = Path(observer.__file__).read_text()
    assert "compute_" not in source and "fair_values" not in source


def test_the_raw_body_goes_to_the_file_tree_and_the_row_carries_its_hash(tmp_path, db_session,
                                                                        env_settings):
    object.__setattr__(env_settings, "exp_dir", tmp_path)
    run = seed_run_and_cohort(db_session, env_settings, games=2)
    observe_once(db_session, NOW, env_settings, _FakeOdds(remaining=4_000_000), run=run,
                 writer=writer_for(db_session))
    row = _rows(db_session, run.run_id)[0]
    assert row.body_path.startswith(str(tmp_path)) and len(row.body_sha256) == 64
    assert Path(row.body_path).read_bytes() == _PAYLOAD


def test_the_pass_is_inert_without_the_setting(db_session, env_settings, monkeypatch):
    object.__setattr__(env_settings, "exp_observer_enabled", False)
    client = _FakeOdds(remaining=4_000_000)
    monkeypatch.setattr(observer, "_client", lambda s: client)
    observer_pass(db_session, NOW, env_settings)
    assert client.calls == []


def test_the_pass_is_inert_without_a_frozen_run(db_session, env_settings, monkeypatch):
    object.__setattr__(env_settings, "exp_observer_enabled", True)   # but no frozen exp_run row
    client = _FakeOdds(remaining=4_000_000)
    monkeypatch.setattr(observer, "_client", lambda s: client)
    observer_pass(db_session, NOW, env_settings)
    assert client.calls == []
    assert db_session.execute(text("select count(*) from exp_observation")).scalar() == 0


def test_the_cohort_is_at_most_eight_games_four_per_sport_between_24_and_120_hours(db_session):
    seed_eligible_games(db_session, now=NOW, nfl=12, ncaaf=12)
    picked = select_cohort(db_session, now=NOW, seed=20260916, freeze_at=NOW).games
    assert len(picked) <= 8 and sum(1 for g in picked if g.sport == "nfl") <= 4
    assert all(timedelta(hours=24) <= g.kickoff_utc - NOW <= timedelta(hours=120) for g in picked)


def test_the_cohort_selection_is_seed_deterministic_and_records_an_empty_stratum(db_session):
    seed_eligible_games(db_session, now=NOW, nfl=12, ncaaf=0)
    first = select_cohort(db_session, now=NOW, seed=1, freeze_at=NOW)
    second = select_cohort(db_session, now=NOW, seed=1, freeze_at=NOW)
    assert first.games == second.games
    # an unavailable stratum is recorded as unavailable, never backfilled with anchor teams
    assert first.strata["ncaaf"] == "unavailable: 0 eligible games in the window"
    assert all(g.sport == "nfl" for g in first.games)
