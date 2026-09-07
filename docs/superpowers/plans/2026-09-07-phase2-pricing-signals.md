# Phase 2 Pricing-and-Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tick, turn the normalized data into sharp-consensus fair values for every matched Kalshi contract (direct where an exact sharp line exists, model-derived elsewhere), record a gap snapshot per contract per tick, run one primary and up to five pre-registered strategy variants as pure functions that emit fully-labelled signals (nothing is filtered out of the record), make any variant replayable over recorded ticks, and show it all on a one-page dashboard with a kill switch. No orders are placed in this phase.

**Architecture:** New packages `harness/pricing/` (fees, devig, consensus, direct fair, margin model, gap snapshots), `harness/strategy/` (variant registry, pure `run_strategy`, persistence pipeline), `harness/replay.py`, and `harness/dashboard/`. The tick calls `price_and_signal()` after normalization inside its own try/except with a time budget. Fair values and gap snapshots are stored per `run_id`, so replay rebuilds a variant's signals from stored snapshots without touching raw data. Variants are frozen YAML hashed to a `variant_id`. Filters never skip a row; they are labels on the signal, and `decision` is derived from them.

**Tech Stack:** Python 3.12 sync, SQLAlchemy 2, Postgres 16, `scipy` is NOT used (normal CDF via `statistics.NormalDist`), FastAPI + Jinja2, PyYAML, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) §3, §4.1, §5.5 (`fair_values`, `market_gap_snapshots`, `strategy_variants`, `signals`, `kill_switch`, `config_history`), §6.0–6.7, §9.6, §9.7 (H2, H4, H8 measurement), §11 (v1 dashboard), §12 (replay), §15 phase 2. Phase 1 plan and ledger: `docs/superpowers/plans/2026-09-06-phase1-normalize-match.md`, `docs/superpowers/reviews/`.

## Global Constraints

- Python 3.12, sync only; all internal prices and probabilities are `Decimal` in [0,1] at 4 dp (spec §6.0); fees in dollars per order, `ceil_to_cent(rate × multiplier × contracts × p × (1−p))`, per-contract fee = order fee / contracts.
- Kalshi football fees (verified 2026-09-06 via `GET /series/{ticker}`, `quadratic_with_maker_fees`, multiplier 1): taker rate 0.07, maker rate 0.0175.
- Consensus: weighted mean in log-odds space; groups `pinnacle` (weight 0.65) and `bol` = {betonlineag, lowvig} (weight 0.35, averaged inside the group); Pinnacle must be present or the contract is `no_sharp`; `disagreement` = population std of group fair probabilities; `edge_min = clamp(edge_floor + 1.5·disagreement, edge_floor, edge_ceiling)` with floor 0.02 and ceiling 0.06 (spec §6.2).
- Direct fair requires the exact half-point line quoted by both groups; otherwise the contract gets a `derived` fair from the margin model (spec §6.2/6.3). Primary variant trades `direct` only; `derived` is a labelled secondary hypothesis (H4).
- Filters are labels; every matched contract with a quote gets a signal row per variant every tick (spec §9.6). `decision ∈ {candidate, rejected}`; `rejection_reason` is the first failing label in a fixed order.
- Sizing: `p_cond = fair − AS` (AS seeded 0.01 until phase 3 measures it); `f* = (p_cond − cost)/(1 − cost)`; stake = `kelly_fraction · f* · bankroll`; caps per bet 3%, per game 5% (same-side moneyline and spread are one position), daily 15%, max open 25, floor $10; paper bankroll fixed from the variant config (spec §6.5).
- A variant is a frozen YAML config hashed (sha256 of canonical JSON, first 12 hex) to `variant_id`; one `primary` plus at most five `secondary` live variants; everything else by replay (spec §6.7).
- Dashboard: kill switch ON is unauthenticated; OFF and any config write require `DASHBOARD_TOKEN`; bind 127.0.0.1 in compose (already the case) (spec §11).
- `raw_responses` never modified; every new table rebuildable from normalized tables plus variants. Test output pristine. Commit trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Wp1gV1T1tYrgYDP3EJALci
  ```
- Dev: venv `.venv/` (uv, 3.12); `DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test`; manual runs use `harness_dev` on the same host, never the test DB; the NAS runs the deployed stack (`make deploy-nas` reruns `init-db`, `seed-teams`, and after this phase `variants register`).

## Verified data facts (NAS database, 2026-09-07 00:45 UTC)

- Books present in `odds_snapshots`: pinnacle (h2h/spreads/totals rich; alternates sparse: 54 rows), betonlineag, lowvig (no alternates), draftkings, fanduel, novig (rich alternate ladders in half points), and `kalshi` itself (h2h/spreads/totals as seen by The Odds API). Median `fetched_at − book_last_update`: pinnacle 3 s, betonlineag 12 s, lowvig 32 s.
- Pinnacle covers 16/16 NFL and 46/54 NCAAF games in the next 8 days.
- Kalshi ladder rungs with an exact sharp (Pinnacle/BOL) line at the same half point: NFL spreads 8/302, NCAAF spreads 37/1455, NFL totals 9/228, NCAAF totals 18/1026. Moneylines: 64 NFL and 106 NCAAF matched contracts, all priceable from h2h. Hence: direct fair is the moneyline product; ladders are the derived-fair product.
- `odds_snapshots.point` is signed from the team's perspective for spreads (home −3.5 / away +3.5) and the total line for totals with `outcome_side ∈ {over, under}`; Kalshi `venue_markets.threshold` is `k.5` with `side_team_id` (spread: YES = team margin > k.5) or `side="over"`.
- Line equivalence: Kalshi spread contract (team T, threshold k.5) ≡ book spreads outcome (T, point −k.5) with the opponent at (+k.5). Kalshi total (over, k.5) ≡ book totals (over, k.5)/(under, k.5). Moneyline (T) ≡ h2h (T)/(opponent).
- `venue_quotes` carries `yes_bid, yes_ask, no_bid, no_ask, yes_bid_size, yes_ask_size, volume_24h, open_interest` per market per markets page; markets pages arrive every 2 min in game windows, 5 min on weekend days, 15 min otherwise.

## File structure

```
harness/db/models.py                 + FairValue, MarketGapSnapshot, StrategyVariant, Signal, KillSwitch, ConfigHistory
harness/db/schema.py                 + indexes / unique constraints
harness/pricing/__init__.py
harness/pricing/fees.py              FeeModel, fee_for_order, fee_per_contract, ceil_to_cent (pure)
harness/pricing/devig.py             power_devig, proportional_devig, devig (pure)
harness/pricing/consensus.py         BookFair, GROUPS, consensus (pure)
harness/pricing/lines.py             latest_book_lines(session, game_id, since) -> BookLines; contract_pair lookups (DB read + pure)
harness/pricing/direct.py            direct_fair_for_contract (pure over BookLines)
harness/pricing/margin_model.py      MarginModel.from_lines, p_margin_over, p_total_over (pure)
harness/pricing/fair.py              compute_fair_values(session, run_id, now) -> counts (persists FairValue rows)
harness/pricing/gaps.py              build_gap_snapshots(session, run_id, now, tz) -> count (persists MarketGapSnapshot rows)
harness/pricing/popularity.yaml      {sport: {espn_id: tier}} for LSU, Saints, marquee programs
harness/strategy/__init__.py
harness/strategy/variants.py         load_variants(dir) -> [Variant]; variant_id; register_variants(session, ...)
harness/strategy/run.py              run_strategy(rows, variant, now) -> [SignalRow] (pure); LABEL_ORDER
harness/strategy/pipeline.py         price_and_signal(session, run_id, now, settings, budget_s) -> counts
harness/variants/*.yaml              committed variant configs (1 primary + 5 secondaries)
harness/replay.py                    replay(session, from_run, to_run, variant_name) -> counts
harness/dashboard/__init__.py
harness/dashboard/app.py             create_dashboard(session_factory, settings) -> FastAPI (mounts /healthz too)
harness/dashboard/templates/index.html
harness/cli.py                       + variants register|list, replay, price-once
harness/recorder/tick.py             + price_and_signal after normalize
harness/config/settings.py           + dashboard_token_file, variants_dir, price_budget_s
tests/test_fees.py, test_devig.py, test_consensus.py, test_lines.py, test_direct.py, test_margin_model.py,
      test_fair.py, test_gaps.py, test_variants.py, test_strategy.py, test_pipeline.py, test_replay.py, test_dashboard.py
tests/fixtures/variants/*.yaml (small), tests/fixtures/odds_lines_game.json
```

---

### Task 1: Phase 2 schema

**Files:**
- Modify: `harness/db/models.py`, `harness/db/schema.py`
- Test: `tests/test_schema_phase2.py`

**Interfaces (all `Base` models):**
- `FairValue(id BigInteger PK, run_id, game_id, market_type ∈ {moneyline, spread, total}, outcome_team_id: int|None, outcome_side: str|None ('over'), threshold: Numeric(6,1)|None, fair_p: Numeric(6,4), fair_source ∈ {direct, derived}, n_groups: int, disagreement: Numeric(6,4)|None, newest_book_ts: timestamptz|None, staleness_s: int|None, model_json: JSONB|None, created_at)`; unique functional index `(run_id, game_id, market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), coalesce(threshold,0), fair_source)`; index `(game_id, market_type, created_at)`.
- `MarketGapSnapshot(id BigInteger PK, run_id, venue_market_id, fair_value_id: BigInteger|None, fair_source: str|None, fair_p, prev_fair_p: Numeric|None, prev_fair_ts, venue_mid, best_bid, best_ask, bid_size, ask_size, n_groups, disagreement, staleness_s, gap_mid, gap_taker_net, gap_maker_net, ttk_minutes: int, dow: int, hour_ct: int, price_bucket: int, volume_24h, open_interest, soft_minus_sharp: Numeric|None, home_popularity_tier: int, away_popularity_tier: int, created_at)`; unique `(run_id, venue_market_id)`; index `(venue_market_id, created_at)`.
- `StrategyVariant(variant_id: str PK (12 hex), name unique, tier ∈ {primary, secondary, replay}, config_json JSONB, registered_at, active bool)`.
- `Signal(id BigInteger PK, run_id, variant_id, gap_snapshot_id, venue_market_id, side: str = 'yes', fair_p, fair_source, venue_best_bid, venue_best_ask, price_target, fee_at_target, as_estimate, edge, edge_min, stake, contracts, decision ∈ {candidate, rejected}, rejection_reason: str|None, labels JSONB, replay bool default false, created_at)`; unique `(run_id, variant_id, venue_market_id, side, replay)`; index `(variant_id, created_at)`, `(venue_market_id, created_at)`.
- `KillSwitch(id int PK, active bool, reason text, set_at)` (single row id=1). `ConfigHistory(config_hash str PK, config_json JSONB, first_seen)`.

- [ ] **Step 1: Write the failing test**

`tests/test_schema_phase2.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness.db.models import FairValue, KillSwitch, MarketGapSnapshot, Signal, StrategyVariant

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_phase2_tables_and_uniques(db_session):
    names = set(db_session.execute(text("select tablename from pg_tables where schemaname='public'")).scalars())
    assert {"fair_values", "market_gap_snapshots", "strategy_variants", "signals", "kill_switch", "config_history"} <= names
    db_session.add(StrategyVariant(variant_id="abc123abc123", name="v", tier="primary", config_json={"a": 1}, registered_at=NOW, active=True))
    fv = FairValue(run_id=1, game_id=1, market_type="moneyline", outcome_team_id=19, fair_p=Decimal("0.5500"), fair_source="direct",
                   n_groups=2, disagreement=Decimal("0.0100"), created_at=NOW)
    db_session.add(fv)
    db_session.flush()
    dup = FairValue(run_id=1, game_id=1, market_type="moneyline", outcome_team_id=19, fair_p=Decimal("0.5600"), fair_source="direct",
                    n_groups=2, created_at=NOW)
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add(KillSwitch(id=1, active=False, reason="", set_at=NOW))
    db_session.flush()
```

- [ ] **Step 2: Run to verify it fails** — `DATABASE_URL_TEST=... .venv/bin/pytest tests/test_schema_phase2.py -v` → ImportError.

- [ ] **Step 3: Implement** — append to `harness/db/models.py`:
```python
class FairValue(Base):
    __tablename__ = "fair_values"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game_id: Mapped[int] = mapped_column(Integer, nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome_team_id: Mapped[int | None] = mapped_column(Integer)
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    fair_p: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    fair_source: Mapped[str] = mapped_column(String(8), nullable=False)
    n_groups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagreement: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    newest_book_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staleness_s: Mapped[int | None] = mapped_column(Integer)
    model_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_fair_game_type_created", "game_id", "market_type", "created_at"),)


class MarketGapSnapshot(Base):
    __tablename__ = "market_gap_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fair_value_id: Mapped[int | None] = mapped_column(BigInteger)
    fair_source: Mapped[str | None] = mapped_column(String(8))
    fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    prev_fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    prev_fair_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    venue_mid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    bid_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    ask_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    n_groups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagreement: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    staleness_s: Mapped[int | None] = mapped_column(Integer)
    gap_mid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    gap_taker_net: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    gap_maker_net: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    ttk_minutes: Mapped[int | None] = mapped_column(Integer)
    dow: Mapped[int] = mapped_column(Integer, nullable=False)
    hour_ct: Mapped[int] = mapped_column(Integer, nullable=False)
    price_bucket: Mapped[int | None] = mapped_column(Integer)
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    soft_minus_sharp: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    home_popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    away_popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "venue_market_id", name="uq_gap_run_market"),
                      Index("ix_gap_market_created", "venue_market_id", "created_at"))


class StrategyVariant(Base):
    __tablename__ = "strategy_variants"
    variant_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    gap_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(4), default="yes", nullable=False)
    fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    fair_source: Mapped[str | None] = mapped_column(String(8))
    venue_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    venue_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    price_target: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    fee_at_target: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    as_estimate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    edge: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    edge_min: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    stake: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    contracts: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(48))
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "variant_id", "venue_market_id", "side", "replay", name="uq_signal_key"),
                      Index("ix_signal_variant_created", "variant_id", "created_at"),
                      Index("ix_signal_market_created", "venue_market_id", "created_at"))


class KillSwitch(Base):
    __tablename__ = "kill_switch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigHistory(Base):
    __tablename__ = "config_history"
    config_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```
In `schema.py` `create_schema` add:
```python
        conn.execute(text(
            "create unique index if not exists uq_fair_value_row on fair_values "
            "(run_id, game_id, market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), coalesce(threshold,0), fair_source)"))
```
and add the six tables to `drop_schema`.

- [ ] **Step 4: Run tests; full suite; commit** `feat: phase 2 schema (fair values, gap snapshots, variants, signals, kill switch)`.

---

### Task 2: Fee model (pure)

**Files:** Create `harness/pricing/__init__.py`, `harness/pricing/fees.py`; Test `tests/test_fees.py`.

**Interfaces:**
- `ceil_to_cent(x: Decimal) -> Decimal` (round up to 0.01).
- `FeeModel(maker_rate: Decimal, taker_rate: Decimal, multiplier: int = 1)`; `KALSHI_FOOTBALL = FeeModel(Decimal("0.0175"), Decimal("0.07"), 1)`; `fee_model_for(fee_type: str | None, multiplier: int | None) -> FeeModel` returning maker 0 for `"quadratic"` (no maker fees) and `KALSHI_FOOTBALL` for `"quadratic_with_maker_fees"`; unknown → `KALSHI_FOOTBALL` (conservative).
- `fee_for_order(model, role: str ('maker'|'taker'), p: Decimal, contracts: int) -> Decimal` (dollars, ceil to cent, minimum 0).
- `fee_per_contract(model, role, p, contracts) -> Decimal` = order fee / contracts, 4 dp.
- `cost(model, role, p, contracts) -> Decimal` = `p + fee_per_contract`.

- [ ] **Step 1: Failing tests**
```python
from decimal import Decimal
from harness.pricing.fees import KALSHI_FOOTBALL, ceil_to_cent, cost, fee_for_order, fee_model_for, fee_per_contract


def test_ceil_to_cent():
    assert ceil_to_cent(Decimal("0.4374")) == Decimal("0.44") and ceil_to_cent(Decimal("0.44")) == Decimal("0.44")


def test_taker_and_maker_fee_at_50c_for_100_contracts():
    assert fee_for_order(KALSHI_FOOTBALL, "taker", Decimal("0.50"), 100) == Decimal("1.75")
    assert fee_for_order(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 100) == Decimal("0.44")
    assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 100) == Decimal("0.0044")


def test_tiny_order_rounds_up_to_full_cent():
    assert fee_for_order(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 1) == Decimal("0.01")
    assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 1) == Decimal("0.0100")


def test_fee_model_for():
    assert fee_model_for("quadratic", 1).maker_rate == 0
    assert fee_model_for("quadratic_with_maker_fees", 1) == KALSHI_FOOTBALL
    assert fee_model_for(None, None) == KALSHI_FOOTBALL


def test_cost():
    assert cost(KALSHI_FOOTBALL, "taker", Decimal("0.35"), 20) == Decimal("0.35") + fee_per_contract(KALSHI_FOOTBALL, "taker", Decimal("0.35"), 20)
```

- [ ] **Step 3: Implement**
```python
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
FOUR = Decimal("0.0001")


def ceil_to_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class FeeModel:
    maker_rate: Decimal
    taker_rate: Decimal
    multiplier: int = 1


KALSHI_FOOTBALL = FeeModel(Decimal("0.0175"), Decimal("0.07"), 1)


def fee_model_for(fee_type: str | None, multiplier: int | None) -> FeeModel:
    m = int(multiplier or 1)
    if fee_type == "quadratic":
        return FeeModel(Decimal("0"), Decimal("0.07"), m)
    return FeeModel(Decimal("0.0175"), Decimal("0.07"), m)


def fee_for_order(model: FeeModel, role: str, p: Decimal, contracts: int) -> Decimal:
    rate = model.maker_rate if role == "maker" else model.taker_rate
    raw = rate * model.multiplier * Decimal(contracts) * p * (Decimal(1) - p)
    return max(ceil_to_cent(raw), Decimal("0.00")) if raw > 0 else Decimal("0.00")


def fee_per_contract(model: FeeModel, role: str, p: Decimal, contracts: int) -> Decimal:
    if contracts <= 0:
        return Decimal("0.0000")
    return (fee_for_order(model, role, p, contracts) / Decimal(contracts)).quantize(FOUR, rounding=ROUND_HALF_UP)


def cost(model: FeeModel, role: str, p: Decimal, contracts: int) -> Decimal:
    return (p + fee_per_contract(model, role, p, contracts)).quantize(FOUR, rounding=ROUND_HALF_UP)
```

- [ ] **Step 4–5: run, commit** `feat: kalshi fee model`.

---

### Task 3: Devig and consensus (pure)

**Files:** Create `harness/pricing/devig.py`, `harness/pricing/consensus.py`; Tests `tests/test_devig.py`, `tests/test_consensus.py`.

**Interfaces:**
- `implied(price_decimal: Decimal) -> Decimal` = 1/price.
- `proportional_devig(implieds: list[Decimal]) -> list[Decimal]`.
- `power_devig(implieds, tol=1e-9, max_iter=100) -> list[Decimal]`: find k>0 with Σ pᵢ^k = 1 by bisection on k ∈ [0.5, 5]; return pᵢ^k (4 dp). For a two-way market this equals the standard power method.
- `devig(prices: list[Decimal], method: str = "power") -> list[Decimal]`; falls back to proportional when the solver fails.
- `BookFair(book: str, fair_p: Decimal, last_update: datetime | None)`.
- `GROUPS = {"pinnacle": ("pinnacle",), "bol": ("betonlineag", "lowvig")}`, `WEIGHTS = {"pinnacle": Decimal("0.65"), "bol": Decimal("0.35")}`.
- `Consensus(fair_p: Decimal, n_groups: int, disagreement: Decimal, newest_ts: datetime | None, group_fairs: dict[str, Decimal])`.
- `consensus(fairs: list[BookFair], require: str = "pinnacle") -> Consensus | None`: group members averaged in probability; groups combined as weighted mean of logit(p) with weights renormalized over present groups; `None` if the required group is absent; disagreement = population std of group fair probabilities (0 when one group); fair clipped to [0.0001, 0.9999].

- [ ] **Step 1: Failing tests**
`tests/test_devig.py`:
```python
from decimal import Decimal
from harness.pricing.devig import devig, implied, power_devig, proportional_devig


def test_proportional_two_way():
    out = proportional_devig([Decimal("0.5263"), Decimal("0.5263")])
    assert out == [Decimal("0.5000"), Decimal("0.5000")]


def test_power_two_way_sums_to_one_and_favours_favourite_less_than_proportional():
    imp = [implied(Decimal("1.30")), implied(Decimal("3.80"))]  # 0.7692 + 0.2632 = 1.0324
    prop = proportional_devig(imp)
    pw = power_devig(imp)
    assert abs(sum(pw) - 1) < Decimal("0.0002")
    assert pw[0] > prop[0]  # power method takes more vig off the longshot


def test_devig_fallback_on_degenerate():
    assert devig([Decimal("1.0"), Decimal("1.0")]) == [Decimal("0.5000"), Decimal("0.5000")]
```
`tests/test_consensus.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal
from harness.pricing.consensus import BookFair, consensus

T = datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc)


def test_consensus_requires_pinnacle():
    assert consensus([BookFair("betonlineag", Decimal("0.55"), T)]) is None


def test_consensus_weights_and_disagreement():
    c = consensus([BookFair("pinnacle", Decimal("0.60"), T), BookFair("betonlineag", Decimal("0.56"), T), BookFair("lowvig", Decimal("0.54"), T)])
    assert c.n_groups == 2
    assert c.group_fairs["bol"] == Decimal("0.5500")
    assert Decimal("0.57") < c.fair_p < Decimal("0.59")  # log-odds weighted toward pinnacle
    assert c.disagreement == Decimal("0.0250")  # population std of {0.60, 0.55}
    assert c.newest_ts == T


def test_consensus_pinnacle_only():
    c = consensus([BookFair("pinnacle", Decimal("0.60"), T)])
    assert c.fair_p == Decimal("0.6000") and c.disagreement == Decimal("0.0000") and c.n_groups == 1
```

- [ ] **Step 3: Implement**
`harness/pricing/devig.py`:
```python
from decimal import Decimal, ROUND_HALF_UP

FOUR = Decimal("0.0001")


def implied(price_decimal: Decimal) -> Decimal:
    return (Decimal(1) / Decimal(price_decimal)).quantize(FOUR, rounding=ROUND_HALF_UP)


def proportional_devig(implieds: list[Decimal]) -> list[Decimal]:
    total = sum(implieds)
    if total <= 0:
        return [Decimal("0.5000")] * len(implieds) if len(implieds) == 2 else implieds
    return [(p / total).quantize(FOUR, rounding=ROUND_HALF_UP) for p in implieds]


def power_devig(implieds: list[Decimal], tol: float = 1e-9, max_iter: int = 100) -> list[Decimal]:
    ps = [float(p) for p in implieds]
    if any(p <= 0 or p >= 1 for p in ps):
        raise ValueError("power devig needs implieds strictly inside (0,1)")
    lo, hi = 0.5, 5.0
    f = lambda k: sum(p ** k for p in ps) - 1.0  # noqa: E731
    if f(lo) < 0 or f(hi) > 0:
        raise ValueError("no root in bracket")
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    return [Decimal(str(p ** k)).quantize(FOUR, rounding=ROUND_HALF_UP) for p in ps]


def devig(prices: list[Decimal], method: str = "power") -> list[Decimal]:
    imps = [implied(p) for p in prices]
    if method == "power":
        try:
            return power_devig(imps)
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
    return proportional_devig(imps)
```
`harness/pricing/consensus.py`:
```python
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import pstdev

FOUR = Decimal("0.0001")
GROUPS = {"pinnacle": ("pinnacle",), "bol": ("betonlineag", "lowvig")}
WEIGHTS = {"pinnacle": Decimal("0.65"), "bol": Decimal("0.35")}


@dataclass(frozen=True)
class BookFair:
    book: str
    fair_p: Decimal
    last_update: datetime | None


@dataclass(frozen=True)
class Consensus:
    fair_p: Decimal
    n_groups: int
    disagreement: Decimal
    newest_ts: datetime | None
    group_fairs: dict


def _clip(p: float) -> float:
    return min(max(p, 0.0001), 0.9999)


def consensus(fairs: list[BookFair], require: str = "pinnacle") -> Consensus | None:
    by_group: dict[str, list[Decimal]] = {}
    newest: datetime | None = None
    for bf in fairs:
        for g, members in GROUPS.items():
            if bf.book in members:
                by_group.setdefault(g, []).append(bf.fair_p)
        if bf.last_update and (newest is None or bf.last_update > newest):
            newest = bf.last_update
    if require not in by_group:
        return None
    group_fairs = {g: (sum(v) / len(v)).quantize(FOUR, rounding=ROUND_HALF_UP) for g, v in by_group.items()}
    wsum = sum(WEIGHTS[g] for g in group_fairs)
    logit = sum(float(WEIGHTS[g] / wsum) * math.log(_clip(float(p)) / (1 - _clip(float(p)))) for g, p in group_fairs.items())
    fair = Decimal(str(_clip(1 / (1 + math.exp(-logit))))).quantize(FOUR, rounding=ROUND_HALF_UP)
    dis = Decimal(str(pstdev([float(p) for p in group_fairs.values()]))).quantize(FOUR, rounding=ROUND_HALF_UP) if len(group_fairs) > 1 else Decimal("0.0000")
    return Consensus(fair, len(group_fairs), dis, newest, group_fairs)
```

- [ ] **Step 4–5: run, commit** `feat: devig and sharp consensus`.

---

### Task 4: Book lines loader and direct fair values

**Files:** Create `harness/pricing/lines.py`, `harness/pricing/direct.py`; Tests `tests/test_lines.py`, `tests/test_direct.py`; fixture `tests/fixtures/odds_lines_game.json` (a list of dicts `{book, market_type, outcome_team_id, outcome_side, point, price_decimal, book_last_update, fetched_at}` for one game with pinnacle/betonlineag/lowvig/draftkings h2h, a main spread at ±3.5, alternates at ±6.5 for pinnacle and betonlineag, totals at 44.5, and a stale lowvig row).

**Interfaces:**
- `LineKey(book, market_type, outcome_team_id, outcome_side, point)`; `Line(key, price, last_update, fetched_at)`.
- `latest_book_lines(session, game_id: int, now: datetime, lookback_s: int = 1200) -> dict[LineKey, Line]`: newest row per key among `odds_snapshots` with `fetched_at >= now − lookback_s` (SQL `DISTINCT ON`).
- `spread_pair(lines, team_id, opp_id, threshold) -> dict[book, (Line, Line)]`: per book, the pair `(team, −threshold)` and `(opp, +threshold)` from `spreads` or `alternate_spreads`.
- `total_pair(lines, threshold) -> dict[book, (over, under)]`; `ml_pair(lines, team_id, opp_id) -> dict[book, (team, opp)]`.
- `direct_fair(pairs: dict[str, tuple[Line, Line]], now) -> tuple[Consensus, list[BookFair]] | None`: devig each book pair, build `BookFair`s for sharp books only (pinnacle, betonlineag, lowvig), `consensus(...)`; returns None when no Pinnacle pair.
- `soft_fair(pairs, book="draftkings") -> Decimal | None`: devigged first-leg fair from one soft book (for `soft_minus_sharp`).

- [ ] **Step 1: Failing tests** (`tests/test_direct.py` uses the fixture to assert: ml consensus exists with n_groups 2; spread at 3.5 has n_groups 2; spread at 6.5 has n_groups 2 (pinnacle + betonlineag alternates) and at 9.5 returns None; total 44.5 works; stale lowvig row (fetched 40 min ago) is excluded by `lookback_s`). `tests/test_lines.py` inserts the fixture rows into `odds_snapshots` via the DB and asserts `latest_book_lines` returns one row per key and honours the lookback.

- [ ] **Step 3: Implement** (`lines.py` with a `DISTINCT ON (book, market_type, outcome_team_id, outcome_side, point) ... ORDER BY ..., fetched_at DESC` query; `direct.py` composing `devig` + `consensus`). Sharp set constant `SHARP_BOOKS = ("pinnacle", "betonlineag", "lowvig")`.

- [ ] **Step 4–5: run, commit** `feat: book lines loader and direct fair values`.

---

### Task 5: Margin model (derived fair, pure)

**Files:** Create `harness/pricing/margin_model.py`; Test `tests/test_margin_model.py`.

**Interfaces:**
- `SIGMA_MARGIN = {"nfl": 13.5, "ncaaf": 17.0}`, `SIGMA_TOTAL = {"nfl": 10.0, "ncaaf": 13.0}`.
- `MarginModel(sport, mu_home_margin: float, sigma_margin: float, mu_total: float | None, sigma_total: float | None, source: dict)`.
- `MarginModel.from_main_lines(sport, home_point: Decimal, p_home_cover: Decimal, total_line: Decimal | None, p_over: Decimal | None) -> MarginModel`: `mu_home_margin = −home_point + σ·Φ⁻¹(p_home_cover)` (so a −3.5 line with 50% cover gives μ=3.5); `mu_total = total_line + σ_t·Φ⁻¹(p_over)`.
- `p_margin_over(model, team_is_home: bool, threshold: Decimal) -> Decimal` = P(team margin > threshold) = 1 − Φ((threshold − μ_team)/σ) with μ_team = μ_home if home else −μ_home; `p_total_over(model, threshold) -> Decimal`.
- `p_moneyline(model, team_is_home) -> Decimal` = P(margin > 0) (tie mass ignored; NFL ties settle at 50c on Kalshi, noted in model_json).
Uses `statistics.NormalDist`.

- [ ] **Step 1: Failing tests** (symmetry: home −3.5 at 50% → μ 3.5 → P(home > 3.5) = 0.5000; P(home > 0.5) ≈ 0.5877 for NFL σ 13.5 (compute expected with NormalDist in the test); away P(margin > −3.5) equals 1 − P(home > 3.5); totals monotone decreasing in threshold; `from_main_lines` with p=0.52 shifts μ by σ·Φ⁻¹(0.52)).

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: margin distribution model for derived fair values`.

---

### Task 6: Fair value computation per run (persist)

**Files:** Create `harness/pricing/fair.py`; Test `tests/test_fair.py`.

**Interfaces:**
- `compute_fair_values(session, run_id: int, now: datetime, lookback_s: int = 1200, game_ids: list[int] | None = None) -> FairCounts(direct: int, derived: int, no_sharp: int, games: int)`:
  1. Candidate games: games with kickoff in `[now − 4h, now + 8d]` that have at least one matched `venue_markets` row (or the explicit list).
  2. For each game: `lines = latest_book_lines(...)`; for each distinct matched contract shape among its venue markets (moneyline per side team; spread per (side_team, threshold); total per threshold): try `direct_fair`; on success insert `FairValue(fair_source="direct", n_groups, disagreement, newest_book_ts, staleness_s = now − newest)`.
  3. Build one `MarginModel` per game from the sharp consensus main spread (the spreads line with the smallest |point| that has a Pinnacle pair) and main total (the totals line nearest the median of available totals with a Pinnacle pair); for every contract shape lacking a direct fair, insert `FairValue(fair_source="derived", n_groups = groups of the main line, disagreement = main-line disagreement, model_json = {mu, sigma, source_line, source_p})`. Contract shapes with no model (no Pinnacle main line) count as `no_sharp` and get no row.
  4. `ON CONFLICT DO NOTHING` on the functional unique index; returns counts.

- [ ] **Step 1: Failing test** (`tests/test_fair.py`): seed teams, a game with kickoff in 2 days, matched venue markets (moneyline both sides, spread rungs 3.5/6.5/9.5 for the favourite, total 44.5/47.5), odds rows from the Task 4 fixture; assert direct rows for both moneylines, spread 3.5 and 6.5, total 44.5; derived rows for spread 9.5 and total 47.5 with `model_json["mu"]` close to 3.5; second call inserts 0.

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: per-run fair values (direct + derived)`.

---

### Task 7: Gap snapshots (persist)

**Files:** Create `harness/pricing/gaps.py`, `harness/pricing/popularity.yaml`; Test `tests/test_gaps.py`.

**Interfaces:**
- `popularity.yaml`: `{ncaaf: {99: 3, 333: 2, 2390: 2, 61: 2, 2633: 2, 130: 2, 194: 2, 2483: 1, 2, ...}, nfl: {18: 3, 12: 2, 21: 2, ...}}` — LSU 3, Saints 3, a dozen marquee programs at 2, playoff teams at 1; loaded once into a dict.
- `build_gap_snapshots(session, run_id, now, tz: str, fee_model=KALSHI_FOOTBALL) -> int`: for each `venue_quotes` row of this `run_id` whose market is matched and whose game kicks off after `now − 4h`: pick the fair (direct preferred, else derived) from this run's `fair_values` for the contract shape; `prev_fair` = the most recent earlier `fair_values` row for the same shape and source within 15 min; compute `venue_mid = (yes_bid + yes_ask)/2`, `gap_mid = fair − mid`, `gap_taker_net = fair − yes_ask − fee_per_contract(taker, yes_ask, 100)`, `gap_maker_net = fair − yes_bid − fee_per_contract(maker, yes_bid, 100)` (joining the bid, 100-contract reference size), `ttk_minutes`, `dow`/`hour_ct` in `tz`, `price_bucket = int(mid*100)//5*5`, `soft_minus_sharp` for moneylines when a DraftKings h2h pair exists (`soft_fair − fair`), popularity tiers from the YAML; insert with `ON CONFLICT DO NOTHING` on `(run_id, venue_market_id)`. Rows with no fair still get a snapshot with `fair_source=None` (they are the `no_sharp` population).

- [ ] **Step 1: Failing test**: after Task 6's seed plus a venue_quotes row per market for the run, assert snapshot count equals matched markets with quotes, that the moneyline snapshot has `soft_minus_sharp` set, that `gap_maker_net` equals fair − bid − maker fee, and that a market with no fair gets a row with `fair_source is None`.

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: market gap snapshots per run`.

---

### Task 8: Variant registry and pure strategy

**Files:** Create `harness/strategy/__init__.py`, `harness/strategy/variants.py`, `harness/strategy/run.py`, `harness/variants/sharp_direct.yaml` (+ five secondaries: `sharp_plus_derived.yaml`, `no_velocity.yaml`, `wide_band.yaml`, `nfl_only.yaml`, `constrained.yaml`); Tests `tests/test_variants.py`, `tests/test_strategy.py`; fixture `tests/fixtures/variants/tiny.yaml`.

**Interfaces:**
- Variant YAML schema (all keys required; defaults shown for the primary):
```yaml
name: sharp_direct
tier: primary            # primary | secondary
sports: [nfl, ncaaf]
sources_allowed: [direct]        # direct | derived
edge_floor: 0.02
edge_ceiling: 0.06
disagreement_mult: 1.5
as_seed: 0.01
price_band: [0.20, 0.80]
min_ttk_min: 20
max_spread_c: {nfl: 8, ncaaf: 20}
min_volume_24h: 50
velocity_max_pts: 0.02
stale_s: 180
kelly_fraction: 0.25
bankroll: 3000
per_bet_cap: 0.03
per_game_cap: 0.05
daily_cap: 0.15
max_open: 25
floor_stake: 10
apply_caps: false        # caps are labels unless true (the `constrained` variant)
```
- `Variant(name, tier, config: dict, variant_id: str)`; `variant_id = sha256(json.dumps(config, sort_keys=True, separators=(",",":")))[:12]`.
- `load_variants(dir: Path) -> list[Variant]` (validates keys; raises on unknown/missing keys; enforces ≤1 primary and ≤5 secondaries).
- `register_variants(session, variants, now) -> RegisterResult(added, unchanged, deactivated)`: upsert by name; a changed config gets a new `variant_id` (old row deactivated); also writes `config_history`.
- `GapRow` (the input record; a plain dataclass mirroring `MarketGapSnapshot` plus `sport`, `game_id`, `market_type`, `side_team_id`, `fee_type`, `fee_multiplier`).
- `LABEL_ORDER = ["has_fair", "source_allowed", "sport_allowed", "not_stale", "price_band", "ttk", "spread", "volume", "velocity", "disagreement_ok", "edge", "min_contracts", "cap_per_bet", "cap_per_game", "cap_daily", "max_open"]`.
- `run_strategy(rows: list[GapRow], variant: Variant, now: datetime, state: StrategyState | None = None) -> list[SignalRow]` (pure): for every row emits a `SignalRow` with `labels: dict[str, bool]`, `decision`, `rejection_reason` (first False label in `LABEL_ORDER`), and the numbers: `edge_min = clamp(edge_floor + disagreement_mult·disagreement, edge_floor, edge_ceiling)`; `AS = as_seed`; `p0 = fair − edge_min − AS`; `price_target = floor_cents(p0 − fee_per_contract(maker, p0, 100))`; `edge = fair − price_target − fee_per_contract(maker, price_target, 100)`; label `edge` = edge ≥ edge_min and price_target < best_ask; sizing per Global Constraints with `p_cond = fair − AS`, `cost = price_target + fee_per_contract`, `contracts = floor(stake/price_target)`; cap labels computed against `state` (per-game and daily exposure accumulated across candidates in this call in row order; same-side moneyline+spread for a game count once, keeping the higher edge); when `apply_caps` is false the cap labels are recorded but do not affect `decision`.
- `SignalRow` fields match `Signal` columns (minus ids).

- [ ] **Step 1: Failing tests**: `test_variants.py` (id stable across key order; unknown key raises; two primaries raise; register twice → unchanged; config change → new id and old deactivated). `test_strategy.py` with hand-built `GapRow`s: (a) a clean candidate (fair 0.55, bid 0.50, ask 0.52, NFL, ttk 120, volume 500, disagreement 0.01) → decision candidate, edge_min 0.025, price_target = floor_cents(0.55−0.025−0.01−fee), stake ≈ 0.25·f*·3000 capped at 90; (b) `derived` source rejected under `sharp_direct` with reason `source_allowed` but candidate under `sharp_plus_derived`; (c) stale (staleness 600) → `not_stale`; (d) price band; (e) two spread rungs on one game plus its moneyline: with `apply_caps: true` per-game cap keeps only the higher-edge same-side pair member; (f) `no_velocity` variant ignores a 3-pt jump that `sharp_direct` rejects; (g) every row yields exactly one SignalRow even when `fair_p` is None (`has_fair` False).

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: variant registry and pure strategy`.

---

### Task 9: Pipeline, tick integration, CLI (`variants`, `price-once`)

**Files:** Create `harness/strategy/pipeline.py`; Modify `harness/recorder/tick.py`, `harness/cli.py`, `harness/config/settings.py` (`variants_dir: Path = Path(__file__).parent.parent / "variants"` resolved via `importlib.resources`, `price_budget_s: int = 20`, `dashboard_token_file: Path = Path("/run/secrets/dashboard_token")`), `pyproject.toml` package-data `variants/*.yaml`, `Makefile`/runbook (deploy runs `variants register`); Tests `tests/test_pipeline.py`, extend `tests/test_tick.py`, `tests/test_packaging.py` (yaml members include variants).

**Interfaces:**
- `price_and_signal(session, run_id, now, settings, budget_s: float) -> dict`: `compute_fair_values` → `build_gap_snapshots` → load active variants (`StrategyVariant.active`) → for each: load this run's gap snapshots joined to markets/games into `GapRow`s → `run_strategy` → insert `Signal`s (`ON CONFLICT DO NOTHING`), commit per variant; stops between stages when the budget is spent and records `{"budget_exhausted": true}`; returns counts `{fair_direct, fair_derived, no_sharp, gaps, signals: {name: {candidate, rejected}}}`.
- Tick: after normalization (own try/except with rollback; warnings on failure), only when this tick refreshed Kalshi markets (i.e. `summaries` non-empty) so gaps align with fresh quotes; counts into `notes["pricing"]`.
- CLI: `harness variants register` (loads `variants_dir`, registers, prints table), `harness variants list`, `harness price-once [--run-id N]` (runs the pipeline for the latest run; for manual checks against `harness_dev`).

- [ ] **Step 1: Failing tests**: `test_pipeline.py` end-to-end on the DB (Task 6/7 seed + variants registered from the fixture dir) asserting fair/gap/signal counts and that a second call is idempotent; `test_tick.py` addition: a tick with the KM fixture and registered variants writes `notes["pricing"]["gaps"] >= 0` and never fails the run when pricing raises (monkeypatch `price_and_signal` to raise → status degraded, warning key `pricing`).

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: pricing pipeline in the tick, variants CLI`.

---

### Task 10: Replay

**Files:** Create `harness/replay.py`; Modify `harness/cli.py`; Test `tests/test_replay.py`.

**Interfaces:**
- `replay(session, from_run: int, to_run: int, variant_name: str, variants_dir: Path | None = None) -> ReplayCounts(runs, signals_candidate, signals_rejected)`: loads the variant (from the registry by name, or from a YAML path when `--file` is given, registering it with tier `replay`), iterates `run_id` in range over stored `market_gap_snapshots` (grouped per run), builds `GapRow`s, runs `run_strategy`, inserts signals with `replay=True`.
- CLI: `harness replay --from-run A --to-run B --variant NAME [--file path.yaml]` printing counts and the candidate rate.

- [ ] **Step 1: Failing test**: after the pipeline test's data, replay the same variant over the same run → identical candidate/rejected counts and `replay=True` rows; replaying a `--file` variant with `price_band [0.10, 0.90]` yields ≥ as many candidates.

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: replay variants over recorded gap snapshots`.

---

### Task 11: One-page dashboard with kill switch

**Files:** Create `harness/dashboard/__init__.py`, `harness/dashboard/app.py`, `harness/dashboard/templates/index.html`; Modify `harness/cli.py` (`serve` mounts the dashboard app, which includes `/healthz` from `harness/health.py`), `pyproject.toml` (`jinja2>=3.1`, package-data `dashboard/templates/*.html`), `docker-compose.yml` (mount `secrets/dashboard_token` read-only into `app-serve`), runbook; Test `tests/test_dashboard.py`.

**Interfaces:**
- `create_dashboard(session_factory, settings, clock) -> FastAPI` with routes: `GET /` (HTML), `GET /healthz` (existing JSON), `POST /kill` (sets active=True with `reason` form field; no auth), `POST /unkill` (requires header `X-Dashboard-Token` equal to the secret file's content; 403 otherwise), `GET /api/summary` (JSON used by the page).
- Page sections (server-rendered, no JS beyond a fetch-free form): Health (last run, status, seconds since, credits remaining, kill switch state + buttons); Funnel last 24 h (raw rows by source, matched/unmatched markets per sport, fair direct/derived/no_sharp, gaps, signals per active variant with candidate counts); Match report (per sport: matched %, top 10 unmatched reasons); Last 100 signals of the primary variant (time, game, contract, fair, source, bid/ask, target, edge, decision, reason); Unmatched markets (top 50 by volume); WebSocket (orderbook_events last hour, ws trades last hour, last event time); Data quality (feed staleness by book median last hour, trade gaps in notes last 24 h).
- All queries read-only except the two kill-switch routes; every query bounded by time windows and `LIMIT`.

- [ ] **Step 1: Failing test** (TestClient): page renders with a seeded run and signal; `/kill` flips state; `/unkill` without token → 403, with token → 200 and inactive.

- [ ] **Step 3: Implement**; **Step 4–5: run, commit** `feat: one-page dashboard with kill switch`.

---

### Task 12: Register variants on deploy and pre-registration record

**Files:** Modify `Makefile` (`deploy-nas` runs `docker compose run --rm app-run variants register` after `seed-teams`), `docs/runbooks/phase0-deploy.md` (upgrade section), Create `docs/superpowers/reviews/2026-09-XX-phase2-preregistration.md` recording the six variant ids and the date they went live (must precede Week 2 kickoff, Sept 12, per spec §6.7).

- [ ] **Step 1**: Makefile + runbook edits; **Step 2**: run `make deploy-nas`, then `make ssh-nas` → `docker compose run --rm app-run variants list` and paste the table into the preregistration doc; commit `chore: pre-register phase 2 variants`.

---

## Self-review

**Spec coverage:** §6.0 units (Task 2/8 Decimal, fees), §6.1 devig (T3), §6.2 consensus/groups/edge_min/staleness/`no_sharp` (T3/T6/T8), §6.3 target price one-pass and derived fair logged not traded by the primary (T5/T6/T8), §6.4 filters as labels incl. velocity and scope (T8; the "ranked/power-conference" scope is approximated by popularity tiers and noted as a deviation), §6.5 sizing/caps/paper bankroll (T8), §6.6 consistency signals (deferred to phase 3 — noted), §6.7 variants/pre-registration (T8/T12), §9.6 nothing filtered out of the record (T7/T8), §9.7 H2/H4 measurable from gaps + fair_source (T6/T7), §11 v1 dashboard + kill switch auth (T11), §12 replay (T10), §5.5 tables (T1), §15 phase 2 deliverables (all).

**Deliberate deviations:** derived fair values ARE emitted as signals for the `sharp_plus_derived` secondary (labelled, never the primary) because ladder rungs almost never have an exact sharp line; the Kalshi maker fee is modelled at a 100-contract reference size for gap metrics (exact per-order fee is computed at sizing); no key-number adjustment in the margin model (v1, recorded in `model_json` for later comparison); AS is the seed value until phase 3 measures markouts.

**Placeholder scan:** Task 4, 5, 6, 7, 9, 10, 11 give interfaces and test intent rather than full code listings where the code is a direct composition of earlier tasks' interfaces; every name, signature, and formula an implementer needs is stated. Fixture contents for Task 4 are specified by shape and by the assertions they must satisfy.

**Type consistency:** `Consensus` and `BookFair` (T3) consumed by T4/T6; `FeeModel`/`fee_per_contract` (T2) by T7/T8; `Variant`, `GapRow`, `SignalRow`, `run_strategy` (T8) by T9/T10; `FairValue`/`MarketGapSnapshot`/`Signal` column names (T1) by T6/T7/T9/T10/T11.
