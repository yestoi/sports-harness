"""t7 and t10: what they say about themselves, and what they count.

**Each fixture seeds** ISO week 38 of 2026. `veto_week`: `games`, `venue_markets`, `signals`,
`intents`, `orders`, `order_clv` rows at `pinnacle_t5`, and a `veto_decisions` plus a paired
`research_notes` row per signal, all `kind = 'veto'`, `replay = false`, the primary
`claude-opus-5`. `veto_week_with_noise`: the same, plus a `claude-sonnet-5`-only call, a
`replay = true` call, and one `veto_skipped_budget` decision with a null `call_id`; it returns
`decided_primary_count`. `veto_signal_with_reprice_chain` (T19 fix round 1): one decided signal
whose intent carries two orders, linked as a `reprice` chain, each with its own `order_clv` row.
`veto_week_two_games_one_decision` (T19 fix round 1): three decided `proceed` signals, two on one
game and one on another. `rfq_week`: `rfqs` rows received in the week with `rfq_quotes` covering
`quoted`, `same_game`, `no_fair`, `collateral` and `single_leg`. `rfq_week_with_stale`: one more
quote with `closing_stale = true` and null `closing_fair`/`pnl_yes`/`pnl_no`. `hostile_rfq_week`:
one `rfqs` row whose `market_ticker` carries markup and a control character.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from harness.db.models import (
    Game,
    Intent,
    Order,
    OrderClv,
    ResearchNote,
    Rfq,
    RfqQuote,
    Signal,
    VenueMarket,
    VetoDecision,
)
from harness.report.tables import NOT_COLLECTED, weekly_tables

YEAR, WEEK = 2026, 38
WEEK_START = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
WED = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19

_runs = iter(range(1, 100_000))


def _tables(session, settings):
    return weekly_tables(session, YEAR, WEEK, settings)


# --- shared seeding helpers -------------------------------------------------------------------


def _game(session, kickoff=None) -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or WED + timedelta(hours=6), status="scheduled")
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker) -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-EVT",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type="moneyline",
                    side_team_id=HOME, match_confidence=Decimal("1.00"), match_status="matched",
                    first_seen_raw_id=1, last_seen_at=WED)
    session.add(m)
    session.flush()
    return m


def _signal(session, market, created_at=None) -> Signal:
    s = Signal(run_id=next(_runs), variant_id="v_base", gap_snapshot_id=1,
              venue_market_id=market.id, side="yes", fair_p=Decimal("0.5100"),
              fair_source="direct", price_target=Decimal("0.4800"), edge=Decimal("0.0300"),
              edge_min=Decimal("0.0100"), stake=Decimal("10.00"), contracts=20,
              decision="candidate", labels={}, created_at=created_at or WED)
    session.add(s)
    session.flush()
    return s


def _intent(session, signal, market, created_at=None) -> Intent:
    # T19 fix round 1 (Important): `game_id` is a real, populated-in-production column
    # (`harness/execution/store.py`) that t7's `clusters` column (`count(distinct i.game_id)`)
    # reads. Leaving it unset made every `clusters` cell a silent 0 in this module's tests.
    i = Intent(id=uuid.uuid4(), signal_id=signal.id, variant_id="v_base", venue="kalshi",
              venue_market_id=market.id, ticker=market.ticker, side="yes",
              target_prob=Decimal("0.4800"), game_id=market.game_id,
              signal_created_at=created_at or WED, created_at=created_at or WED, replay=False)
    session.add(i)
    session.flush()
    return i


def _order(session, intent, market, placed_at=None) -> Order:
    # `uq_open_order` allows only one open/partially_filled order per (venue, ticker, side,
    # variant); every fixture in this module reuses one variant and one side, so orders here
    # are `filled` -- settled, and exempt from that index -- rather than left `open`.
    o = Order(intent_id=intent.id, variant_id="v_base", venue="kalshi", mode="paper",
             client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
             venue_market_id=market.id, side="yes", prob=Decimal("0.4800"),
             contracts=Decimal("10.00"), status="filled", placed_at=placed_at or WED,
             sport="nfl", replay=False)
    session.add(o)
    session.flush()
    return o


def _order_clv(session, order, clv_p_net="0.0200", benchmark_type="pinnacle_t5") -> OrderClv:
    c = OrderClv(order_id=order.id, benchmark_type=benchmark_type, p_bench=Decimal("0.5600"),
                p_used=order.prob, p_used_kind="order", clv_p=Decimal("0.0250"),
                clv_p_net=Decimal(clv_p_net), clv_roi_net=Decimal("0.0400"), stale=False)
    session.add(c)
    session.flush()
    return c


def _veto_decision(session, signal, *, decision, call_id, decided_at=None, from_cache=False,
                   reason_code=None) -> VetoDecision:
    v = VetoDecision(signal_id=signal.id, call_id=call_id, decision=decision,
                     confidence=Decimal("0.7500"), from_cache=from_cache, feature_delta={},
                     signal_created_at=signal.created_at,
                     decided_at=decided_at or (signal.created_at + timedelta(seconds=30)),
                     reason_code=reason_code)
    session.add(v)
    session.flush()
    return v


def _research_note(session, *, call_id, subject_id, model="claude-opus-5", created_at=None,
                   replay=False) -> ResearchNote:
    n = ResearchNote(call_id=call_id, model=model, kind="veto", subject_id=str(subject_id),
                     effort="low", prompt_hash="h" * 16, features={}, snippets={},
                     tool_calls=[], output={}, usage={}, cost_usd=Decimal("0.012000"),
                     latency_ms=800, created_at=created_at or WED, replay=replay)
    session.add(n)
    session.flush()
    return n


# --- veto fixtures (t7, H9) -------------------------------------------------------------------


def _seed_veto_signal(session, market, *, decision, clv_p_net, lag_s=30, from_cache=False):
    """One signal with an intent, an order, an order_clv row and a decided, primary,
    non-replay `veto_decisions`/`research_notes` pair -- one full `veto_h9` row."""
    signal = _signal(session, market, created_at=WED)
    intent = _intent(session, signal, market, created_at=WED)
    order = _order(session, intent, market, placed_at=WED)
    _order_clv(session, order, clv_p_net=clv_p_net)
    call_id = uuid.uuid4()
    _veto_decision(session, signal, decision=decision, call_id=call_id,
                   decided_at=WED + timedelta(seconds=lag_s), from_cache=from_cache)
    _research_note(session, call_id=call_id, subject_id=signal.id, model="claude-opus-5",
                   created_at=WED)
    return signal


@pytest.fixture
def veto_week(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id, "VETOMKT-BASE")
    _seed_veto_signal(db_session, market, decision="proceed", clv_p_net="0.0300", lag_s=10)
    _seed_veto_signal(db_session, market, decision="reduce", clv_p_net="0.0100", lag_s=60,
                      from_cache=True)
    _seed_veto_signal(db_session, market, decision="veto", clv_p_net="-0.0200", lag_s=120)
    return SimpleNamespace(game=game, market=market)


@pytest.fixture
def veto_week_with_noise(db_session, veto_week):
    market = veto_week.market

    # A claude-sonnet-5-only call: no matching opus row for this call_id, so `veto_h9`'s inner
    # join to `research_notes` never matches it (the view's population is the primary's rows).
    shadow_signal = _signal(db_session, market, created_at=WED)
    _intent(db_session, shadow_signal, market, created_at=WED)
    shadow_call = uuid.uuid4()
    _veto_decision(db_session, shadow_signal, decision="proceed", call_id=shadow_call)
    _research_note(db_session, call_id=shadow_call, subject_id=shadow_signal.id,
                   model="claude-sonnet-5")

    # A replay = true call: the primary model answered, but as a frozen study re-run, so
    # `veto_h9`'s `n.replay = false` filter excludes it.
    replay_signal = _signal(db_session, market, created_at=WED)
    _intent(db_session, replay_signal, market, created_at=WED)
    replay_call = uuid.uuid4()
    _veto_decision(db_session, replay_signal, decision="proceed", call_id=replay_call)
    _research_note(db_session, call_id=replay_call, subject_id=replay_signal.id,
                   model="claude-opus-5", replay=True)

    # One veto_skipped_budget decision with a null call_id: a call-less label the inner join to
    # `research_notes` can never match (NULL never equals a call_id), and t7 reports it in the
    # note rather than as a decision.
    budget_signal = _signal(db_session, market, created_at=WED)
    _veto_decision(db_session, budget_signal, decision="veto_skipped_budget", call_id=None,
                   reason_code="daily_cap")

    return SimpleNamespace(decided_primary_count=3)


@pytest.fixture
def veto_signal_with_reprice_chain(db_session):
    """One decided `proceed` signal whose intent was repriced once: the executor cancelled the
    first order for `reprice` and placed a second under the same intent (the shape
    `order_episodes`/`episode_of` exist for -- see `harness/db/schema.py`'s
    `_ORDER_EPISODES_VIEW` and `harness/report/gate.py`'s `filled_vs_unfilled`). Both orders
    carry their own `order_clv` row with deliberately different `clv_p_net` values, so a query
    that joined every order under the intent (rather than resolving to the episode's terminal
    order) would count this one decided signal twice and blend the superseded order's CLV into
    the mean (T19 fix round 1, Critical)."""
    game = _game(db_session)
    market = _market(db_session, game.id, "VETOMKT-REPRICE")
    signal = _signal(db_session, market, created_at=WED)
    intent = _intent(db_session, signal, market, created_at=WED)
    first = _order(db_session, intent, market, placed_at=WED)
    first.status = "cancelled"
    first.cancel_reason = "reprice"
    first.cancelled_at = WED + timedelta(minutes=1)
    db_session.flush()
    _order_clv(db_session, first, clv_p_net="0.9000")     # the superseded order: must not surface
    second = _order(db_session, intent, market, placed_at=WED + timedelta(minutes=2))
    _order_clv(db_session, second, clv_p_net="0.0300")    # the episode's resolved order
    call_id = uuid.uuid4()
    _veto_decision(db_session, signal, decision="proceed", call_id=call_id)
    _research_note(db_session, call_id=call_id, subject_id=signal.id, model="claude-opus-5")
    return SimpleNamespace(resolved_clv=Decimal("0.0300"))


@pytest.fixture
def veto_week_two_games_one_decision(db_session):
    """Two decided `proceed` signals on one game and a third on a second game: `clusters`
    counts distinct games, not signals, so this decision's row must read 2, never 3."""
    game1 = _game(db_session)
    market1 = _market(db_session, game1.id, "VETOMKT-CLUSTER-A")
    game2 = _game(db_session)
    market2 = _market(db_session, game2.id, "VETOMKT-CLUSTER-B")
    _seed_veto_signal(db_session, market1, decision="proceed", clv_p_net="0.0100")
    _seed_veto_signal(db_session, market1, decision="proceed", clv_p_net="0.0200")
    _seed_veto_signal(db_session, market2, decision="proceed", clv_p_net="0.0300")
    return SimpleNamespace()


# --- RFQ fixtures (t10, H5) ---------------------------------------------------------------------


def _rfq(session, rfq_id, *, market_ticker=None, received_at=None) -> Rfq:
    r = Rfq(id=rfq_id, received_at=received_at or WED, market_ticker=market_ticker or rfq_id,
           legs=[], raw={"msg": {}, "truncated": False}, status="open")
    session.add(r)
    session.flush()
    return r


def _quoted_rfq_quote(session, rfq_id, *, margin="0.0300", fair="0.4000", yes_bid="0.3700",
                      no_bid="0.5700", pnl_yes="0.0500", pnl_no="-0.0500") -> RfqQuote:
    q = RfqQuote(rfq_id=rfq_id, computed_at=WED, legs=2, fair=Decimal(fair),
                margin_per_leg=Decimal(margin), yes_bid=Decimal(yes_bid),
                no_bid=Decimal(no_bid), fee_branch_game=True, fee_branch_event=None,
                fee_subtracted=Decimal("0.0100"), declined_reason=None, unmatched_legs=0,
                graded_at=WED + timedelta(days=1), closing_fair=Decimal("0.4200"),
                closing_stale=False, pnl_yes=Decimal(pnl_yes), pnl_no=Decimal(pnl_no),
                voided=False)
    session.add(q)
    session.flush()
    return q


def _declined_rfq_quote(session, rfq_id, *, declined_reason, margin="0.0300") -> RfqQuote:
    q = RfqQuote(rfq_id=rfq_id, computed_at=WED, legs=2, fair=None, margin_per_leg=Decimal(margin),
                yes_bid=None, no_bid=None, declined_reason=declined_reason, unmatched_legs=0,
                voided=False)
    session.add(q)
    session.flush()
    return q


@pytest.fixture
def rfq_week(db_session):
    _rfq(db_session, "RFQ-QUOTED")
    _quoted_rfq_quote(db_session, "RFQ-QUOTED")

    _rfq(db_session, "RFQ-SAME-GAME")
    _declined_rfq_quote(db_session, "RFQ-SAME-GAME", declined_reason="same_game")

    _rfq(db_session, "RFQ-NO-FAIR")
    _declined_rfq_quote(db_session, "RFQ-NO-FAIR", declined_reason="no_fair")

    _rfq(db_session, "RFQ-COLLATERAL")
    _declined_rfq_quote(db_session, "RFQ-COLLATERAL", declined_reason="collateral")

    _rfq(db_session, "RFQ-SINGLE-LEG")
    _declined_rfq_quote(db_session, "RFQ-SINGLE-LEG", declined_reason="single_leg")
    return SimpleNamespace()


@pytest.fixture
def rfq_week_with_stale(db_session, rfq_week):
    _rfq(db_session, "RFQ-STALE")
    q = RfqQuote(rfq_id="RFQ-STALE", computed_at=WED, legs=2, fair=Decimal("0.4000"),
                margin_per_leg=Decimal("0.0300"), yes_bid=Decimal("0.3700"),
                no_bid=Decimal("0.5700"), declined_reason=None, unmatched_legs=0,
                graded_at=WED + timedelta(days=1), closing_fair=None, closing_stale=True,
                pnl_yes=None, pnl_no=None, voided=False)
    db_session.add(q)
    db_session.flush()
    return rfq_week


@pytest.fixture
def hostile_rfq_week(db_session):
    """One RFQ whose `market_ticker` carries markup and a control character, quoted (not
    declined) so the table renders a real row rather than only the empty placeholder."""
    hostile_ticker = "KXNFLGAME-<script>alert(1)</script>-\x07-T1"
    _rfq(db_session, "RFQ-HOSTILE", market_ticker=hostile_ticker)
    _quoted_rfq_quote(db_session, "RFQ-HOSTILE")
    return SimpleNamespace()


# --- tests ------------------------------------------------------------------------------------


def test_t7_is_no_longer_a_placeholder(db_session, env_settings, veto_week):
    table = _tables(db_session, env_settings)["t7"]
    assert NOT_COLLECTED not in (table.note or "")
    assert table.rows and table.rows[0][0] != "veto"


def test_t7_declares_the_decisions_post_hoc(db_session, env_settings, veto_week):
    """0.1 and ruling B-I1: the decisions are post-hoc, so H9 is an upper bound on what an
    enforcing veto could deliver, and the table has to say so where the number is read."""
    header = _tables(db_session, env_settings)["t7"].header.lower()
    assert "post-hoc" in header or "post hoc" in header
    assert "upper bound" in header


def test_t7_reports_the_lag_distribution_and_the_cached_share(db_session, env_settings,
                                                              veto_week):
    table = _tables(db_session, env_settings)["t7"]
    assert "lag_p50_s" in table.columns and "lag_p95_s" in table.columns
    assert "cached_share" in table.columns


def test_t7_rows_are_the_decision_labels(db_session, env_settings, veto_week):
    table = _tables(db_session, env_settings)["t7"]
    assert {row[0] for row in table.rows} <= {"proceed", "reduce", "veto"}


def test_t7_excludes_the_shadow_and_the_replays(db_session, env_settings, veto_week_with_noise):
    """The `veto_h9` view's three filters, exercised end to end: a shadow row, a replay row and a
    `veto_skipped_budget` decision must not reach a cell."""
    table = _tables(db_session, env_settings)["t7"]
    total = sum(row[1] for row in table.rows if isinstance(row[1], int))
    assert total == veto_week_with_noise.decided_primary_count


def test_t7_notes_the_two_call_less_labels(db_session, env_settings, veto_week_with_noise):
    note = _tables(db_session, env_settings)["t7"].note or ""
    assert "veto_skipped_budget" in note and "veto_error" in note


def test_t7_resolves_a_repriced_intent_to_its_episode_terminal_order(
        db_session, env_settings, veto_signal_with_reprice_chain):
    """T19 fix round 1 (Critical): one decided signal is one row, whatever its intent's reprice
    count. The superseded order's CLV (0.90) must never surface or blend into an average; only
    the episode's resolved (terminal) order's CLV (0.03) does."""
    table = _tables(db_session, env_settings)["t7"]
    row = next(r for r in table.rows if r[0] == "proceed")
    assert row[table.columns.index("n")] == 1
    assert row[table.columns.index("clv_pinnacle_t5")] == \
        float(veto_signal_with_reprice_chain.resolved_clv)


def test_t7_clusters_counts_distinct_games_not_signals(
        db_session, env_settings, veto_week_two_games_one_decision):
    """T19 fix round 1 (Important): three decided `proceed` signals, but only two distinct
    games -- `clusters` must read 2, not 3 and not 0."""
    table = _tables(db_session, env_settings)["t7"]
    row = next(r for r in table.rows if r[0] == "proceed")
    assert row[table.columns.index("n")] == 3
    assert row[table.columns.index("clusters")] == 2


def test_t10_is_no_longer_a_placeholder(db_session, env_settings, rfq_week):
    table = _tables(db_session, env_settings)["t10"]
    assert NOT_COLLECTED not in (table.note or "")


def test_t10_declares_the_counterfactual(db_session, env_settings, rfq_week):
    """Ruling B-I6: no fill, no adverse selection, an upper bound, and the scored side named."""
    header = _tables(db_session, env_settings)["t10"].header.lower()
    assert "counterfactual" in header and "upper bound" in header
    assert "no fill" in header or "no-fill" in header


def test_t10_reports_the_stale_quotes_separately_with_no_pnl(db_session, env_settings,
                                                             rfq_week_with_stale):
    """Ruling B-I6: reported separately, and with no number invented. `rfq_grade` leaves
    `closing_fair` and both P&L columns null on a stale close, so the row prints the placeholder
    rather than an average of a substituted value."""
    from harness.report.tables import PLACEHOLDER

    table = _tables(db_session, env_settings)["t10"]
    stale = [row for row in table.rows if str(row[0]) == "stale close"]
    assert stale, {str(row[0]) for row in table.rows}
    assert stale[0][table.columns.index("pnl_yes")] == PLACEHOLDER
    assert stale[0][table.columns.index("pnl_no")] == PLACEHOLDER


def test_t10_counts_arrivals_declines_and_quotes(db_session, env_settings, rfq_week):
    """The decline mix is H5's question -- which RFQs we would and would not have answered and
    why -- so `collateral` and `single_leg` are their own groups and not folded into `no_fair`."""
    table = _tables(db_session, env_settings)["t10"]
    labels = {str(row[0]) for row in table.rows}
    assert {"quoted", "same_game", "no_fair", "collateral", "single_leg"} & labels


def test_t10_shows_the_margin_distribution(db_session, env_settings, rfq_week):
    assert "margin_per_leg" in _tables(db_session, env_settings)["t10"].columns


def test_t10_never_renders_venue_free_text_unquoted(db_session, env_settings, hostile_rfq_week):
    """F60 allows a 120-character quoted excerpt of `market_ticker`; this table renders **less**
    than that -- its first column is an outcome group (`quoted`, `same_game`, `stale close`) and
    no ticker reaches a cell at all. The assertion is kept anyway, as the guard that stays true
    if a later phase adds a per-ticker row."""
    table = _tables(db_session, env_settings)["t10"]
    rendered = " ".join(str(value) for row in table.rows for value in row)
    assert "<script>" not in rendered and "\x00" not in rendered
    assert all(len(str(value)) <= 120 for row in table.rows for value in row
               if isinstance(value, str))


def test_t10_counts_voided_rows_apart_from_pnl(db_session, env_settings, rfq_week):
    """Interface addendum (T14 final shape): a voided quote's game was postponed or canceled and
    can never settle. It carries `graded_at` and no P&L, and must never dilute the `quoted`
    line's average with a null -- it is its own line."""
    from harness.report.tables import PLACEHOLDER

    db_session.add(Rfq(id="RFQ-VOIDED", received_at=WED, market_ticker="RFQ-VOIDED", legs=[],
                       raw={"msg": {}, "truncated": False}, status="open"))
    db_session.add(RfqQuote(rfq_id="RFQ-VOIDED", computed_at=WED, legs=2, fair=Decimal("0.4000"),
                            margin_per_leg=Decimal("0.0300"), yes_bid=Decimal("0.3700"),
                            no_bid=Decimal("0.5700"), declined_reason=None, unmatched_legs=0,
                            graded_at=WED + timedelta(days=1), closing_fair=None,
                            closing_stale=False, pnl_yes=None, pnl_no=None, voided=True))
    db_session.flush()
    table = _tables(db_session, env_settings)["t10"]
    voided = [row for row in table.rows if str(row[0]) == "voided"]
    assert voided, {str(row[0]) for row in table.rows}
    assert voided[0][table.columns.index("pnl_yes")] == PLACEHOLDER
    assert voided[0][table.columns.index("pnl_no")] == PLACEHOLDER
    quoted = [row for row in table.rows if str(row[0]) == "quoted"]
    assert quoted and quoted[0][1] == 1     # the voided row never joins the quoted line's n


def test_t9_is_still_a_placeholder(db_session, env_settings):
    """0.14 puts t7 and t10 in scope and nothing else: H3's flow imbalance stays a later phase."""
    assert NOT_COLLECTED in (_tables(db_session, env_settings)["t9"].note or "")


def test_the_table_order_is_unchanged():
    from harness.report.tables import TABLE_KEYS

    assert TABLE_KEYS == ("t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t11", "t9",
                          "t10", "t12", "t13")
