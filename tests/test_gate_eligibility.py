"""Gate eligibility, dormant: the mechanism is built and verified, and it changes nothing.

The golden literals below are today's SQL, typed out by hand rather than read back from the
module, so the test is not self-comparing (review I-b1). Whitespace is normalized per line and
blank lines are dropped, so re-indenting a constant is free while changing a token is not. The
`-- eligibility:order` and `-- eligibility:run` markers are part of the pinned text: they are
where `eligible_sql` inserts, and a constant that loses its marker silently stops being
filterable.

Editing a literal here is an R1 event, not a tidy-up: a criterion's SQL is its definition.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from harness.report import gate as g
from harness.report.gate import Eligibility, criteria_hash, eligible_sql

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

#: The gate's identity as of 2026-09-11. 6C's `tests/test_readme_gate.py` pins the same value
#: independently and on purpose (review Minor 2): two files that must be changed together are a
#: better guard on an R1 invariant than one. Both move only under a dated user decision.
CRITERIA_HASH = "5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5"


def norm(sql: str) -> str:
    """Per-line whitespace normalization: indentation is free, tokens are pinned. Lines are
    kept because `--` comments run to the end of one."""
    return "\n".join(line.strip() for line in sql.strip().splitlines() if line.strip())


GOLDEN = {
    "_FILL_EVENTS": """
select o.id, o.game_id, coalesce(g.sport, o.sport) as sport, o.book_source,
o.dirty_minutes, o.venue_bid_at_place, o.venue_ask_at_place
from orders o
left join games g on g.id = o.game_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and exists (select 1 from fills f
where f.order_id = o.id and f.fill_method = 'queue_model'
and f.replay = false)
""",
    "_CLV_FILL_EVENTS": """
select o.game_id, c.benchmark_type, c.clv_p_net
from order_clv c
join orders o on o.id = c.order_id
left join games g on g.id = o.game_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and o.game_id is not null and c.stale = false and c.clv_p_net is not null
and c.benchmark_type = any(:benchmarks)
and exists (select 1 from fills f
where f.order_id = o.id and f.fill_method = 'queue_model'
and f.replay = false)
and not exists (select 1 from benchmarks b
where b.game_id = o.game_id and b.kickoff_moved)
""",
    "_MARKOUTS": """
select k.fair_p, k.p_used, k.fee_per_contract, k.fair_changed, o.game_id
from markouts k
join orders o on o.id = k.order_id
left join games g on g.id = o.game_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and o.game_id is not null and k.anchor = :anchor and k.horizon = :horizon
and not exists (select 1 from benchmarks b
where b.game_id = o.game_id and b.kickoff_moved)
""",
    "_DRIFT": """
select k.fair_p, k.fair_changed, o.side, o.fair_p_at_place, o.game_id
from markouts k
join orders o on o.id = k.order_id
left join games g on g.id = o.game_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and o.game_id is not null and o.fair_p_at_place is not null
and k.anchor = :anchor and k.horizon = :horizon
and exists (select 1 from fills f
where f.order_id = o.id and f.fill_method = 'queue_model'
and f.replay = false)
and not exists (select 1 from benchmarks b
where b.game_id = o.game_id and b.kickoff_moved)
""",
    "_EPISODE_ORDERS": """
select o.id, o.variant_id, o.venue_market_id, o.side, o.placed_at, o.cancel_reason,
o.cancelled_at, o.game_id, c.clv_p_net,
exists (select 1 from fills f
where f.order_id = o.id and f.fill_method = 'queue_model'
and f.replay = false) as filled
from orders o
left join games g on g.id = o.game_id
left join order_clv c on c.order_id = o.id and c.benchmark_type = :benchmark
and c.stale = false
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and o.game_id is not null
and not exists (select 1 from benchmarks b
where b.game_id = o.game_id and b.kickoff_moved)
""",
    "_MISMATCHED": """
select count(*) filter (where m.match_key is distinct from o.match_key) as mismatched,
count(*) as orders,
count(distinct o.game_id) as games
from orders o
join venue_markets m on m.id = o.venue_market_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
""",
    "_SETTLEMENT_COVERAGE": """
select count(*) as markets,
count(distinct d.game_id) as games,
count(*) filter (where exists (
select 1 from venue_settlements v
where v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
and v.settled_at <= :now)) as with_venue
from (select distinct s.venue, s.ticker, m.game_id
from venue_settlements s
left join venue_markets m on m.ticker = s.ticker and m.venue = s.venue
where s.source = 'derived' and s.settled_at <= :now
and exists (select 1
from orders o
left join games g on g.id = o.game_id
where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
-- eligibility:order
and o.ticker = s.ticker
and exists (select 1 from fills f
where f.order_id = o.id and f.fill_method = 'queue_model'
and f.replay = false)
)) d
""",
    "_STALENESS": """
select percentile_disc(0.5) within group (order by v.staleness_s) as median,
count(v.staleness_s) as n,
count(distinct v.game_id) as games
from signals s
join market_gap_snapshots gs on gs.id = s.gap_snapshot_id
join fair_values v on v.id = gs.fair_value_id
where s.variant_id = :variant and s.replay = false and s.decision = 'candidate'
and s.created_at <= :now and v.staleness_s is not null
-- eligibility:run
""",
    "_SETTLEMENT_MISMATCHES": """
select count(*)
from venue_settlements d
join venue_settlements v
on v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
where d.source = 'derived' and d.settled_at <= :now
and v.settled_at <= :now
and coalesce(d.result, '') <> coalesce(v.result, '')
""",
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_every_criterion_constant_matches_its_golden_literal(name):
    assert norm(getattr(g, name).text) == norm(GOLDEN[name])


def test_the_mismatch_criterion_carries_no_eligibility_marker():
    """`_SETTLEMENT_MISMATCHES` counts derived-versus-venue disagreements over every settled
    market, not the variant's own orders (`gate.py:534-541`). It has no order and no signal in
    scope, so there is nothing for an eligibility boundary to filter; giving it a marker would
    be a claim about rows it never reads."""
    assert "-- eligibility" not in g._SETTLEMENT_MISMATCHES.text


def test_eligible_sql_is_the_identity_when_both_are_none():
    """The dormant state: not "equivalent", the same string."""
    for name in GOLDEN:
        sql = getattr(g, name).text
        assert eligible_sql(sql, None, None) == sql


def test_eligible_sql_inserts_both_predicates_when_set():
    assert "and o.id >= :eligible_from_order" in eligible_sql(
        g._FILL_EVENTS.text, 4200, None)
    assert "and s.run_id >= :eligible_from_run" in eligible_sql(
        g._STALENESS.text, None, 9100)
    both = eligible_sql(g._FILL_EVENTS.text, 4200, 9100)
    # The order predicate goes where the order alias is in scope; the run predicate has no
    # marker in this constant and so inserts nothing.
    assert "and o.id >= :eligible_from_order" in both
    assert ":eligible_from_run" not in both


def test_criteria_hash_is_pinned():
    """R1: the criteria's identity does not move because a filter was added around them."""
    assert criteria_hash() == CRITERIA_HASH


def test_eligibility_defaults_are_dormant():
    e = Eligibility()
    assert e.from_order_id is None and e.from_run_id is None
    assert e.active is False and e.params() == {}


@pytest.mark.parametrize("name", sorted(n for n in GOLDEN if n != "_SETTLEMENT_MISMATCHES"))
def test_a_filtered_statement_still_parses(db_session, name):
    """`explain` on the test database: an inserted predicate must leave valid SQL.

    A marker in the wrong place -- after a `group by`, or where its alias is out of scope --
    produces a string that only fails the day someone switches the setting on. This is the test
    that makes "reviewed and verified" (U8) true of the off state.
    """
    sql = eligible_sql(getattr(g, name).text, 4200, 9100)
    params = {"variant": "v1", "now": NOW, "eligible_from_order": 4200,
              "eligible_from_run": 9100, "benchmarks": ["pinnacle_t5"],
              "anchor": "nw_fill", "horizon": "30m", "benchmark": "pinnacle_t5"}
    bound = {k: v for k, v in params.items() if f":{k}" in sql}
    db_session.execute(text("explain " + sql), bound)


def test_a_gate_row_carries_eligibility_only_when_it_is_set(db_session):
    """Verification row (ii): `criteria_json ? 'eligibility'` is 0 while the settings are unset."""
    from harness.db.models import GateReport, StrategyVariant
    from harness.report.gate import evaluate_all

    db_session.add(StrategyVariant(variant_id="p00000000001", name="sharp_direct",
                                   tier="primary", config_json={}, active=True,
                                   registered_at=NOW))
    db_session.flush()

    evaluate_all(db_session, NOW, ["p00000000001"], "sharp_direct")
    dormant = db_session.query(GateReport).order_by(GateReport.id.desc()).first()
    assert "eligibility" not in dormant.criteria_json

    later = NOW.replace(hour=13)
    evaluate_all(db_session, later, ["p00000000001"], "sharp_direct",
                 Eligibility(from_order_id=4200, from_run_id=9100))
    active = db_session.query(GateReport).order_by(GateReport.id.desc()).first()
    assert active.criteria_json["eligibility"] == {"from_order_id": 4200,
                                                   "from_run_id": 9100}
    assert active.criteria_hash == CRITERIA_HASH


def test_render_gate_prints_the_boundary_only_when_it_is_set():
    from harness.report.gate import GateResult, render_gate

    result = GateResult(variant_id="v1", gate_variant=True, criteria={},
                        criteria_hash=CRITERIA_HASH, passed=False)
    assert "eligibility=" not in render_gate([result], {}, {})
    line = render_gate([result], {}, {}, Eligibility(4200, 9100))
    assert "eligibility=from_order_id:4200 from_run_id:9100" in line
