"""The two owner write routes on the LAN listener (addendum 5.1, 5.2, 5.5; D5).

`POST /api/parlay/placed` records a slip the owner placed by hand and `POST /api/parlay/correct`
corrects one through the closed table of 5.2. Both exist only on the LAN app: the loopback app
is byte-for-byte the app it was, which the last case in this file asserts (invariant 9).

**No secret material is created or read here.** Each client writes a throwaway hash line into
`tmp_path` -- never into `secrets/` -- and nothing in this file reads the owner's real files, a
cookie value or a key. The clock is fixed (`T0`, tz-aware) and injected through
`create_dashboard(..., clock=...)`, so nothing depends on the day the suite runs on.

The fixtures commit: the routes run on their own sessions (a sessionmaker over the test engine,
which is what production hands `create_dashboard`), so a card that is still in `db_session`'s
transaction is a card the route cannot see. Committing also makes the rollback a refusal path
performs mean what it means in production -- it discards the request's work and nothing else.
"""
import json
from datetime import datetime, timedelta, timezone
from itertools import count
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from harness.dashboard import app as app_module
from harness.dashboard import snapshots
from harness.dashboard.app import BODY_MAX_BYTES, WRITE_LIMIT_PER_MINUTE, create_dashboard
from harness.dashboard.auth import hash_password
from harness.dashboard.snapshots import ticket as _ticket_builder  # noqa: F401 - registers it
from harness.db.models import (DashboardSnapshot, ParlayCard, ParlayLedger, ParlayLeg,
                               ParlayPlacement)
from harness.parlay.placement import mark_placed
from tests.conftest import _make_parlay_card, _one_leg_game

#: The instant every case runs at: five minutes after `conftest`'s placement prices, so the
#: newest DraftKings row is inside `leg_max_age_minutes` and a line is "moved" only when a
#: fixture moved it. ISO week 38 of 2026, which is the week each card below is keyed to (D20).
T0 = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
YEAR, WEEK = 2026, 38

#: A throwaway password generated for this process. Not the owner's, and never written outside
#: `tmp_path`.
PASSWORD = "a throwaway password for this test process"

#: The LAN address the settings carry in these tests. The origin the routes compare against is
#: derived from *these*, never from the request's `Host` (A-I8).
LAN_ADDR, LAN_PORT = "192.168.12.127", 8443
ORIGIN = f"https://{LAN_ADDR}:{LAN_PORT}"

#: What the owner's own page sends: the CSRF header, the settings origin and a JSON body.
HEADERS = {"X-Requested-With": "sports-ui", "Origin": ORIGIN,
           "Content-Type": "application/json"}

#: Two ids of the shape `crypto.randomUUID()` mints on the confirm sheet (5.1).
CID = "11111111-1111-4111-8111-111111111111"
CID2 = "22222222-2222-4222-8222-222222222222"

#: Distinct team abbreviations per card. `db_session` truncates between tests, so this only has
#: to be unique inside one test.
_cards = count(1)


@pytest.fixture
def lan_settings(env_settings, tmp_path):
    """`env_settings` with the three LAN paths pointed at throwaway names under `tmp_path` and
    the owner's hash line written into one of them. The real paths are container paths the user
    owns; no test ever touches them, and this file creates no secret material (D7)."""
    hash_file = tmp_path / "owner_password_hash"
    hash_file.write_text(hash_password(PASSWORD))
    (tmp_path / "lan_tls.crt").write_text("not a certificate")
    (tmp_path / "lan_tls.key").write_text("not a key")
    return env_settings.model_copy(update={
        "owner_password_hash_file": hash_file,
        "lan_tls_cert_file": tmp_path / "lan_tls.crt",
        "lan_tls_key_file": tmp_path / "lan_tls.key",
        "lan_addr": LAN_ADDR, "lan_port": LAN_PORT,
        "snapshots_enabled": False,
    })


def _client(db_session, settings, *, lan: bool = True, login: bool = True) -> TestClient:
    """A `TestClient` over the app, on its own session factory.

    `https://` because the session cookie is `Secure`: a client would not send it back over
    plain http, which is the point of the attribute. `expire_on_commit=False` so a route can
    serialize the row it just wrote without a second round trip.
    """
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    app = create_dashboard(factory, settings, clock=lambda: T0, lan=lan)
    client = TestClient(app, base_url="https://testserver")
    if login:
        assert client.post("/login", data={"password": PASSWORD},
                           follow_redirects=False).status_code == 303
    return client


@pytest.fixture
def lan_client(db_session, lan_settings):
    """A logged-in client over the LAN app -- the owner's phone."""
    return _client(db_session, lan_settings)


@pytest.fixture
def second_lan_client(db_session, lan_settings):
    """A second logged-in client on its own app and its own sessions -- the laptop to
    `lan_client`'s phone. Each request opens its own transaction, so the week advisory lock in
    `mark_placed` is a real serialization point rather than one transaction meeting itself.
    Reads the same hash file `lan_settings` wrote into `tmp_path`: no second hash is created."""
    return _client(db_session, lan_settings)


# --- fixtures' data ---------------------------------------------------------------------------


def _card(db_session, *, status: str = "proposed", moved_to: Decimal | None = None) -> ParlayCard:
    """One committed single-leg card in week 38, priced from `conftest`'s placement helpers.

    `moved_to` is the line the newest DraftKings row carries when it is not the card's own, i.e.
    a leg whose line moved after the card was built.
    """
    team, game = _one_leg_game(db_session, f"R{next(_cards)}")
    card = _make_parlay_card(db_session, status=status, built_at=T0,
                             legs=[(game.id, team.id, Decimal("-3.5"),
                                    moved_to if moved_to is not None else Decimal("-3.5"))])
    db_session.commit()
    return card


def _placed_card(db_session, *, status: str = "placed", stake: Decimal = Decimal("25"),
                 confirmation_id: str | None = None) -> ParlayCard:
    """A card that went through `mark_placed` and was then driven to `status`.

    Every state after `proposed` has a `parlay_placements` row behind it in production, and 9's
    invariant forbids a corrections row on a card that has none, so the correction cases build
    their cards this way rather than by writing a status onto a bare card.
    """
    card = _card(db_session)
    mark_placed(db_session, card.id, 400, stake, T0, confirmation_id=confirmation_id)
    card.status = status
    db_session.commit()
    return card


def _stake_this_week(db_session, amount: str) -> None:
    """A stake already recorded against week 38, on its own card, the way `mark_placed` records
    one: `source = 'confirmed'` and the card's own `(year, week)`."""
    card = _card(db_session, status="placed")
    db_session.add(ParlayLedger(ts=T0, card_id=card.id, kind="stake", amount=Decimal(amount),
                                year=YEAR, week=WEEK, source="confirmed"))
    db_session.commit()


def _integrity_error(*_args, **_kwargs):
    """`mark_placed` as the request that lost a race sees it: the insert tripped an index.

    Provoked rather than raced, because a genuine race needs a second transaction to commit
    between this request's own read and its flush. What each case then pins is what the route
    does with it, which is where the three answers differ (review I-1).
    """
    raise IntegrityError("insert into parlay_placements", {},
                         Exception("duplicate key value violates a unique constraint"))


def _body(card, **over) -> dict:
    body = {"card_id": card.id, "confirmation_id": CID, "stake": "25.00",
            "accepted_odds": 450, "leg_lines": None, "note": "typed on the phone"}
    body.update(over)
    return body


# --- the placement route (5.1) -----------------------------------------------------------------


def test_a_placement_records_one_stake_row_and_returns_the_placement(lan_client, db_session):
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["placement"]["stake_actual"] == "25.00"
    assert response.json()["placement"]["placed_at"] == T0.isoformat()
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").count() == 1
    # The route committed on its own session; this one still holds the card it built.
    db_session.expire_all()
    assert db_session.get(ParlayCard, card.id).status == "placed"


def test_the_same_confirmation_id_twice_is_one_placement_and_a_200(lan_client, db_session):
    card = _card(db_session)
    first = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    second = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["placement"] == first.json()["placement"]
    assert db_session.query(ParlayPlacement).filter_by(card_id=card.id).count() == 1
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").count() == 1


def test_a_second_id_on_a_placed_card_is_409_carrying_the_existing_placement(lan_client,
                                                                             db_session):
    """A-I17: a retry after a lost response must show the owner what was recorded, so the sheet
    can say `already recorded at 7:41 PM . $25 at +450` instead of a bare refusal."""
    card = _card(db_session)
    lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    again = lan_client.post("/api/parlay/placed", json=_body(card, confirmation_id=CID2),
                            headers=HEADERS)
    assert again.status_code == 409
    assert again.json()["refusal"] == "card_not_placeable"
    assert again.json()["placement"]["placed_at"] is not None
    assert again.json()["placement"]["stake_actual"] == "25.00"


def test_an_id_reused_on_another_card_is_409_confirmation_reused(lan_client, db_session):
    card, other_card = _card(db_session), _card(db_session)
    lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    other = lan_client.post("/api/parlay/placed", json=_body(other_card), headers=HEADERS)
    assert other.status_code == 409
    assert other.json() == {"refusal": "confirmation_reused"}
    db_session.expire_all()
    assert db_session.get(ParlayCard, other_card.id).status == "proposed"


def test_a_moved_line_is_409_with_the_moved_map_then_records_on_resubmit(lan_client, db_session):
    card = _card(db_session, moved_to=Decimal("-4.5"))
    refused = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert refused.status_code == 409 and refused.json()["refusal"] == "line_moved"
    assert refused.json()["moved"] == {"1": ["-3.5", "-4.5"]}
    ok = lan_client.post("/api/parlay/placed",
                         json=_body(card, leg_lines={"1": "-4.5"}), headers=HEADERS)
    assert ok.status_code == 200
    db_session.expire_all()
    assert db_session.query(ParlayLeg).filter_by(card_id=card.id,
                                                 seq=1).one().threshold == Decimal("-4.5")


def test_two_different_cards_at_the_cap_record_one_and_refuse_one(lan_client, second_lan_client,
                                                                  db_session):
    """5.3, through the routes this time: the week lock is in `mark_placed`, so a phone and a
    laptop submitting different cards at the cap serialize and exactly one is recorded."""
    _stake_this_week(db_session, "25.00")
    card, other_card = _card(db_session), _card(db_session)
    first = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    second = second_lan_client.post(
        "/api/parlay/placed", json=_body(other_card, confirmation_id=CID2), headers=HEADERS)
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert {r.json().get("refusal") for r in (first, second)} == {None, "budget_exceeded"}
    refused = first if first.status_code == 409 else second
    assert (refused.json()["recorded"], refused.json()["left"]) == ("50.00", "0.00")
    assert db_session.query(ParlayPlacement).count() == 1


def test_an_integrity_error_on_the_unique_index_returns_the_existing_placement(lan_client,
                                                                               db_session,
                                                                               monkeypatch):
    """The partial unique index on `confirmation_id` is the backstop, not the check: a request
    that lost the race answers with what is recorded rather than with a 500."""
    card = _placed_card(db_session, confirmation_id=CID2)
    monkeypatch.setattr(app_module, "mark_placed", _integrity_error)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["placement"]["stake_actual"] == "25.00"


def test_an_integrity_error_with_nothing_recorded_is_never_a_200(lan_client, db_session,
                                                                 monkeypatch):
    """I-1: a 200 whose `placement` is null would tell the owner's phone that a $25 slip is in
    the ledger when the transaction wrote nothing -- and the week's cap would then let another
    $25 through. The unique index is one integrity error; every other one is a failure."""
    card = _card(db_session)
    monkeypatch.setattr(app_module, "mark_placed", _integrity_error)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"refusal": "internal_error"}
    assert db_session.query(ParlayPlacement).count() == 0
    db_session.expire_all()
    assert db_session.get(ParlayCard, card.id).status == "proposed"


def test_an_integrity_error_on_an_id_another_card_holds_is_confirmation_reused(lan_client,
                                                                               db_session,
                                                                               monkeypatch):
    """The one integrity error that has a name of its own: the id landed on another card while
    this request was in flight, which is 5.3's refusal and not an unnamed failure."""
    _placed_card(db_session, confirmation_id=CID)
    card = _card(db_session)
    monkeypatch.setattr(app_module, "mark_placed", _integrity_error)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 409
    assert response.json() == {"refusal": "confirmation_reused"}


@pytest.mark.parametrize("over,code", [
    ({"card_id": "one"}, 400),
    ({"stake": "twenty"}, 400),
    ({"stake": "NaN"}, 400),
    ({"stake": "-5.00"}, 400),
    ({"confirmation_id": "not-a-uuid"}, 400),
    ({"note": "x" * 201}, 400),
    ({"accepted_odds": "long"}, 400),
    ({"accepted_odds": 0}, 400),                    # not a price at all (M-1)
    ({"accepted_odds": 45}, 400),                   # `450` fat-fingered on the phone
    ({"accepted_odds": -99}, 400),                  # the band no American price lies in
    ({"leg_lines": {"1": "not-a-line"}}, 400),
    ({"nonsense": 1}, 400),
])
def test_every_field_is_type_validated(lan_client, db_session, over, code):
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card, **over), headers=HEADERS)
    assert response.status_code == code
    assert response.json() == {"refusal": "bad_value"}
    assert db_session.query(ParlayPlacement).count() == 0


@pytest.mark.parametrize("odds,payout", [(450, "137.50"), (-110, "47.73"), (100, "50.00")])
def test_an_american_price_at_the_edge_of_the_band_is_recorded(lan_client, db_session, odds,
                                                               payout):
    """The other half of M-1: `+100` and `-110` are prices and are recorded as typed."""
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card, accepted_odds=odds),
                               headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["placement"]["dk_odds_actual"] == odds
    assert response.json()["placement"]["dk_payout_actual"] == payout


def test_a_body_that_is_not_a_json_object_is_a_code_not_a_stack_trace(lan_client):
    response = lan_client.post("/api/parlay/placed", content=b"[1, 2, 3", headers=HEADERS)
    assert response.status_code == 400 and response.json() == {"refusal": "bad_value"}


# --- the correction route (5.2) ----------------------------------------------------------------


@pytest.mark.parametrize("state,field,value,code", [
    ("proposed", "status", "declined", 200),
    ("alive", "return", "137.50", 200),
    ("placed", "status", "cashed", 409),
    ("cashed", "stake", "30.00", 409),
    ("alive", "leg_status:1", "void", 200),
    ("alive", "leg_status:1", "hit", 409),
])
def test_the_correction_route_follows_the_closed_table(lan_client, db_session, state, field,
                                                       value, code):
    card = (_card(db_session) if state == "proposed"
            else _placed_card(db_session, status=state))
    response = lan_client.post("/api/parlay/correct",
                               json={"card_id": card.id, "field": field, "new_value": value},
                               headers=HEADERS)
    assert response.status_code == code
    if code == 409:
        assert response.json() == {"refusal": "correction_not_allowed"}


def test_a_decline_rides_the_correction_table_and_records_void(lan_client, db_session):
    """D5: there is no third route. The confirm sheet's decline is one correction row, and 5.2's
    Effect column records it as `void` while `declined_reason` keeps the owner's own word."""
    card = _card(db_session)
    response = lan_client.post("/api/parlay/correct",
                               json={"card_id": card.id, "field": "status",
                                     "new_value": "declined"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["correction"]["new_value"] == "void"
    assert response.json()["card_status"] == "void"
    db_session.expire_all()
    assert db_session.get(ParlayCard, card.id).declined_reason == "declined"


def test_a_correction_over_the_column_width_is_400_and_never_truncated(lan_client, db_session):
    card = _placed_card(db_session)
    response = lan_client.post("/api/parlay/correct",
                               json={"card_id": card.id, "field": "return",
                                     "new_value": "1" * 33}, headers=HEADERS)
    assert response.status_code == 400 and response.json() == {"refusal": "bad_value"}


def test_a_correction_on_an_absent_card_is_409_not_a_stack_trace(lan_client):
    response = lan_client.post("/api/parlay/correct",
                               json={"card_id": 987654, "field": "status",
                                     "new_value": "declined"}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json() == {"refusal": "card_not_placeable", "placement": None}


def test_the_twenty_first_correction_to_one_card_is_refused(lan_client, db_session):
    card = _placed_card(db_session)
    body = {"card_id": card.id, "field": "leg_line:1", "new_value": "-4.5"}
    codes = [lan_client.post("/api/parlay/correct", json=body, headers=HEADERS).status_code
             for _ in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 409


# --- the request rules (5.5) -------------------------------------------------------------------


@pytest.mark.parametrize("headers,code", [
    ({}, 403),                                              # no CSRF header
    ({"X-Requested-With": "sports-ui"}, 403),               # no Origin
    ({**HEADERS, "Origin": "https://evil.example"}, 403),
    ({**HEADERS, "Content-Type": "text/plain"}, 403),
    ({**HEADERS, "X-Requested-With": "something-else"}, 403),
])
def test_the_request_rules_refuse_everything_but_the_owner_s_own_page(lan_client, db_session,
                                                                      headers, code):
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=headers)
    assert response.status_code == code
    assert response.json() == {"refusal": "forbidden"}
    assert db_session.query(ParlayPlacement).count() == 0


def test_a_request_without_a_session_is_refused_before_any_rule(db_session, lan_settings):
    """The gate (Task 10) answers first, and the routes check the session again themselves: a
    write route that is reachable only because a middleware is installed is one edit away from
    being reachable without it."""
    client = _client(db_session, lan_settings, login=False)
    card = _card(db_session)
    response = client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 401 and response.json() == {"refusal": "session_required"}


def test_the_origin_comes_from_settings_not_from_the_request_host(lan_client, db_session):
    """A-I8: a spoofed `Host:` must not be able to name the origin it is then compared against.
    A route that compared `Origin` with the request's own host would accept this pair."""
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card),
                               headers={**HEADERS, "Origin": "https://evil.example",
                                        "Host": "evil.example"})
    assert response.status_code == 403 and response.json() == {"refusal": "forbidden"}


def test_the_default_https_port_is_left_out_of_the_origin_that_is_compared(db_session,
                                                                           lan_settings):
    """M-3: a browser omits `:443` from `Origin`, so a listener on 443 that compared
    `https://<addr>:443` would refuse every write while the page itself still loaded."""
    client = _client(db_session, lan_settings.model_copy(update={"lan_port": 443}))
    card = _card(db_session)
    headers = {**HEADERS, "Origin": f"https://{LAN_ADDR}"}
    assert client.post("/api/parlay/placed", json=_body(card),
                       headers=headers).status_code == 200
    other = _card(db_session)
    assert client.post("/api/parlay/placed", json=_body(other, confirmation_id=CID2),
                       headers=HEADERS).status_code == 403      # the 8443 origin, on a 443 page


def test_a_body_over_four_kibibytes_is_413_regardless_of_content_length(lan_client):
    response = lan_client.post("/api/parlay/placed",
                               content=b"{" + b"x" * (BODY_MAX_BYTES + 1000),
                               headers={**HEADERS, "Content-Length": "10"})
    assert response.status_code == 413 and response.json() == {"refusal": "body_too_large"}


def test_an_over_cap_body_still_spends_the_session_s_allowance(lan_client, db_session):
    """M-2: a body refused at the cap that the limiter never saw would be an unmetered way to
    make this listener read 4 KiB at a time, over and over."""
    over = b"{" + b"x" * (BODY_MAX_BYTES + 1000)
    for _ in range(WRITE_LIMIT_PER_MINUTE):
        assert lan_client.post("/api/parlay/placed", content=over,
                               headers=HEADERS).status_code == 413
    card = _card(db_session)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 429 and response.json() == {"refusal": "rate_limited"}
    assert db_session.query(ParlayPlacement).count() == 0


def test_a_body_just_under_the_cap_is_read(lan_client, db_session):
    card = _card(db_session)
    body = _body(card, note="x" * 200)
    assert len(json.dumps(body).encode()) < BODY_MAX_BYTES
    assert lan_client.post("/api/parlay/placed", json=body, headers=HEADERS).status_code == 200


def test_thirty_writes_a_minute_then_429(lan_client, db_session):
    card = _placed_card(db_session)
    correction = {"card_id": card.id, "field": "status", "new_value": "void"}
    for _ in range(WRITE_LIMIT_PER_MINUTE):
        lan_client.post("/api/parlay/correct", json=correction, headers=HEADERS)
    response = lan_client.post("/api/parlay/correct", json=correction, headers=HEADERS)
    assert response.status_code == 429 and response.json() == {"refusal": "rate_limited"}


def test_the_write_limiter_is_a_window_not_a_lifetime_budget(db_session, lan_settings):
    """The limiter's window is a minute, not the evening: the owner who spent thirty writes on
    one card gets the next one a minute later."""
    now = [T0]
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    app = create_dashboard(factory, lan_settings, clock=lambda: now[0], lan=True)
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/login", data={"password": PASSWORD},
                       follow_redirects=False).status_code == 303
    card = _placed_card(db_session)
    correction = {"card_id": card.id, "field": "status", "new_value": "void"}

    def _correct():
        return client.post("/api/parlay/correct", json=correction, headers=HEADERS).status_code

    for _ in range(WRITE_LIMIT_PER_MINUTE):
        _correct()
    assert _correct() == 429
    now[0] = T0 + timedelta(seconds=61)
    assert _correct() != 429


def test_no_refusal_body_carries_a_stack_trace(lan_client, db_session, monkeypatch):
    card = _card(db_session)

    def _unexpected(*_args, **_kwargs):
        raise RuntimeError("a path, a query and a value the owner never asked to see")

    monkeypatch.setattr(app_module, "mark_placed", _unexpected)
    response = lan_client.post("/api/parlay/placed", json=_body(card), headers=HEADERS)
    assert response.status_code == 500
    assert set(response.json()) <= {"refusal", "placement", "recorded", "left", "moved"}
    assert "Traceback" not in response.text and "a path, a query" not in response.text


# --- the Ticket payload the two listeners serve (addendum §6) ----------------------------------


def _ticket_cards(payload: dict) -> list[dict]:
    """Every card in a Ticket payload, wherever it is drawn: the live list and the idea slots.

    A `proposed` card is only ever in `ideas`, and `actions` is only ever non-empty on one, but
    a filter that only looked at `cards` would pass this test and still ship buttons, so both
    lists are collected here.
    """
    cards = [card for card in payload["cards"] if isinstance(card, dict)]
    cards += [slot["card"] for slot in payload["ideas"]["slots"] if slot.get("card")]
    return cards


def test_the_loopback_ticket_payload_offers_no_action_and_the_lan_one_offers_three(
        db_session, lan_settings):
    """Addendum §6 (Task 15 review, Important 1): the loopback app has no write route, so a
    draft it serves must carry `actions == []` -- a button there posts to a route that is not
    registered and the owner reads a refusal for an offer that should never have been drawn.
    The LAN app serves the same stored row with the three actions intact.

    One `dashboard_snapshots` row feeds both listeners, so this is decided per request rather
    than at build time; both clients below read the row this test built once.
    """
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    _card(db_session)                     # a proposed card in this week's ncaaf/smart slot
    snapshots.run_builder(factory, "ticket", T0, lan_settings, cadence_s=60)
    lan = _client(db_session, lan_settings)
    loopback = _client(db_session, lan_settings, lan=False, login=False)

    lan_cards = _ticket_cards(lan.get("/api/snap/ticket").json()["payload"])
    loopback_cards = _ticket_cards(loopback.get("/api/snap/ticket").json()["payload"])
    assert [card["actions"] for card in lan_cards] == [["open", "placed", "decline"]]
    assert [card["actions"] for card in loopback_cards] == [[]]
    # The rest of the slip is the same slip: only the offer of a write is withheld.
    assert [card["card_id"] for card in loopback_cards] == [c["card_id"] for c in lan_cards]
    assert [card["stake_text"] for card in loopback_cards] == [c["stake_text"] for c in lan_cards]


def test_the_loopback_listener_leaves_the_stored_snapshot_row_alone(db_session, lan_settings):
    """The filter is on the way out, not on the table: the loopback app must not edit the row
    the scheduler wrote, or the next LAN reader would find the buttons gone from a payload
    nobody rebuilt. Two reads on one row, one per listener, in that order.
    """
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    _card(db_session)
    snapshots.run_builder(factory, "ticket", T0, lan_settings, cadence_s=60)
    loopback = _client(db_session, lan_settings, lan=False, login=False)
    assert _ticket_cards(loopback.get("/api/snap/ticket").json()["payload"])[0]["actions"] == []

    with factory() as session:
        stored = session.get(DashboardSnapshot, "ticket")
        slots = stored.payload["ideas"]["slots"]
    assert [slot["card"]["actions"] for slot in slots if slot.get("card")] == [
        ["open", "placed", "decline"]]
    lan = _client(db_session, lan_settings)
    assert _ticket_cards(lan.get("/api/snap/ticket").json()["payload"])[0]["actions"] == [
        "open", "placed", "decline"]


def test_the_loopback_app_has_neither_route(db_session, lan_settings):
    """Invariant 9: the routes are added inside the `lan` branch, so the loopback listener is
    byte-for-byte the app it was -- no session, no write route, no new refusal."""
    client = _client(db_session, lan_settings, lan=False, login=False)
    card = _card(db_session)
    assert client.post("/api/parlay/placed", json=_body(card), headers=HEADERS).status_code == 404
    assert client.post("/api/parlay/correct",
                       json={"card_id": card.id, "field": "status", "new_value": "declined"},
                       headers=HEADERS).status_code == 404
