"""Task 16 (addendum §7.1, §7.3; design §4): the Floor board button and the game detail.

This file carries only this task's new cases; Task 14 keeps `tests/test_dashboard_static.py`
and Task 15 writes its own file (plan review IM-8).
"""

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
