"""Task 16 (addendum §7.1, §7.3; design §4): the Floor board button and the game detail.

This file carries only this task's new cases; Task 14 keeps `tests/test_dashboard_static.py`
and Task 15 writes its own file (plan review IM-8).

Fix round 1 (review CHANGES_REQUIRED): the `partial` story row's own label and detail-header
note, and the route-id validation guard against a plain-object bracket lookup.
"""

import json

from tests.test_dashboard_static import STATIC, _js_files  # noqa: F401  (kept for parity)


def test_a_board_card_is_a_button_whose_accessible_name_is_the_matchup():
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert 'el("button"' in body and "board-card" in body
    assert "aria-label" in body


def test_the_detail_opens_from_the_hash_route_and_closes_with_escape():
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "#floor/game/" in body and "Escape" in body
    assert "one open at a time" in body or "closeDetail" in body


def test_a_game_outside_the_detail_set_says_when_its_detail_arrives():
    assert "detail is built inside 6 h of kickoff" in (STATIC / "js" / "floor.mjs").read_text()


def test_the_floor_module_names_no_fun_money_amount():
    """F02 on the render side: the only crossing is the one line linking to the ticket.

    Two literal assertions and no scoping games (plan review IM-11). The strong check is T13's
    payload test, which asserts no fun-money key appears anywhere in the Floor payload; this one
    catches a renderer that reached for a field the payload does not carry.
    """
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "on your ticket" in body
    assert "card.stake" not in body
    assert "payout" not in body


def test_the_floor_module_writes_only_text_nodes():
    body = (STATIC / "js" / "floor.mjs").read_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in body

def test_the_partial_story_row_gets_its_own_label_and_glossary_entry():
    """Omarchy header ruling, fix round 1: `partial` is never left as a bare, unglossaried
    string beside the explained `gap` and `not_evaluated` kinds."""
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert 'technical: "partial"' in body
    assert 'glossaryTerm("partial", "partial")' in body
    glossary = json.loads((STATIC / "glossary.json").read_text())
    assert "partial" in glossary
    assert glossary["partial"].get("plain") and glossary["partial"].get("what")


def test_a_partial_detail_carries_a_visible_note_on_its_own_header():
    """The row's own badge is not enough on its own (fix round 1): a detail that hit a
    per-partition read cap must say so on the detail header too, so it is never presented as
    the complete story."""
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "partialNotice" in body
    assert "not the complete story" in body
    assert 'row.kind === "partial"' in body


def test_a_route_id_is_validated_before_any_object_lookup():
    """Fix round 1 (Important): `#floor/game/constructor` (or `__proto__`, an empty id, or any
    other non-numeric text) must never reach a bracket lookup on a plain object -- `{}` resolves
    `details["constructor"]` to the inherited `Object` function, not `undefined`, which would
    bypass the `!detail` fallback and throw before `replaceChildren` ever runs. The digits-only,
    length-bounded pattern below rejects all four shapes by construction (none of them is
    `^[0-9]{1,10}$`), and `hasOwnProperty` is the second, independent guard even for an id that
    does pass."""
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "GAME_ID_PATTERN" in body
    assert r"/^[0-9]{1,10}$/" in body
    assert "Object.prototype.hasOwnProperty.call(details" in body
    # The three literal danger names named in the ruling appear only in the guard's own
    # explanation, never as a value this module would pass to a bracket lookup unguarded.
    for name in ("constructor", "__proto__", "toString"):
        assert name in body
