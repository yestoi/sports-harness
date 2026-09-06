import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import OddsSnapshot
from harness.matching.games import upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

FIXD = Path(__file__).parent / "fixtures"
FEAT = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ALT = json.loads((FIXD / "odds_alternates_event.json").read_text())
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_parse_featured_rows():
    rows = parse_odds_body(FEAT, "nfl")
    assert len(rows) == 6
    h2h = [r for r in rows if r.market_type == "h2h"]
    assert {r.outcome_name for r in h2h} == {"Seattle Seahawks", "New England Patriots"}
    tot = [r for r in rows if r.market_type == "totals"]
    assert {(r.outcome_name, r.point) for r in tot} == {("Over", Decimal("44.5")), ("Under", Decimal("44.5"))}
    assert rows[0].last_update == datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)


def test_parse_alternates_dict_shape():
    rows = parse_odds_body(ALT, "nfl")
    assert rows and all(r.market_type.startswith("alternate_") for r in rows)


def test_upsert_is_idempotent_and_resolves_teams(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # fixture includes Seahawks(26) and Patriots(17)
    upsert_games_from_odds(db_session, "nfl", FEAT, raw_id=1)
    rows = parse_odds_body(FEAT, "nfl")
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW) == 6
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW) == 0
    snap = db_session.query(OddsSnapshot).filter_by(market_type="spreads").all()
    assert {s.point for s in snap} == {Decimal("-3.5"), Decimal("3.5")}
    assert all(s.outcome_team_id is not None and s.game_id is not None for s in snap)
