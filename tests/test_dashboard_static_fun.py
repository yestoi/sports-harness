"""Task 15 (addendum §1, §8; design §2, §3): the Ticket surface, the confirm sheet and
`postJson`.

This file carries only this task's new cases. `tests/test_dashboard_static.py` stays Task 14's
and `tests/test_dashboard_static_floor.py` stays Task 16's, so wave 7's tasks share no file
(plan review IM-8). The module-level helpers are imported from Task 14's file rather than
restated: one definition of `STATIC`, of what counts as a JS file, and of the budget.
"""

import json

from tests.test_dashboard_static import STATIC, _all_files, _js_files, ASSET_BUDGET_BYTES


def test_api_mjs_is_still_the_only_module_that_calls_fetch():
    for path in _js_files():
        if path.name != "api.mjs":
            assert "fetch(" not in path.read_text(), path


def test_post_json_sends_the_csrf_header_and_treats_401_as_session_required():
    body = (STATIC / "js" / "api.mjs").read_text()
    assert "export async function postJson" in body
    assert '"X-Requested-With": "sports-ui"' in body
    assert '"Content-Type": "application/json"' in body
    assert "401" in body


def test_post_json_sends_the_cookie_and_never_names_an_origin_itself():
    """Addendum §5.5 and Task 11's carried item: the routes compare `Origin` against the
    settings' own address and port, so the page sends the cookie with `credentials:
    "same-origin"` and lets the browser write the `Origin` header. A request that set one
    itself would either be refused or -- worse -- be a page pretending to be another origin."""
    body = (STATIC / "js" / "api.mjs").read_text()
    assert 'credentials: "same-origin"' in body
    assert '"Origin"' not in body and "'Origin'" not in body


def test_the_sheet_guards_random_uuid_outside_a_secure_context():
    """A-Minor 2: `crypto.randomUUID` is undefined outside a secure context, so the sheet must
    refuse to open and say why rather than throwing on the owner's first tap."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "isSecureContext" in body and "randomUUID" in body
    assert "opened over HTTPS" in body


def test_the_sheet_mints_a_fresh_confirmation_id_after_a_refusal_is_edited():
    """Task 11's carried item: a *corrected* resend must never be refused as
    `confirmation_reused` by accident, while an identical resend must be. The id is minted when
    the sheet opens and again on the first edit of the stake, the odds or a leg's line after a
    refusal -- so the second submit of an unchanged sheet carries the id the first one did."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "mintConfirmationId" in body
    assert "refused" in body
    assert body.count("randomUUID()") >= 1


def test_the_sheet_writes_the_note_and_the_confirmation_id_as_text_only():
    """Both are owner- or client-supplied strings shown back on the page; both enter the DOM as
    text nodes, never as markup (Task 11's carried item, the DOM rule)."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "textContent" in body


def test_the_ticket_module_writes_only_text_nodes():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in body


def test_the_sheet_and_the_draft_slip_are_keyboard_operable():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "<dialog" in body or 'el("dialog"' in body
    assert "Escape" in body
    assert body.count('tabindex="-1"') == 0        # nothing focusable is removed from the order


def test_the_escape_listener_is_removed_when_the_sheet_closes():
    """Task 16's reviewer flagged the pattern: a keydown handler left on `document` after the
    sheet closes is a listener per open, all of them still firing."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "addEventListener(\"keydown\"" in body
    assert "removeEventListener(\"keydown\"" in body
    assert "detachEscape" in body


def test_the_sheet_returns_focus_to_the_button_that_opened_it():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "opener" in body and ".focus()" in body


def test_every_refusal_code_the_routes_return_has_a_sentence():
    """All eleven codes `harness/dashboard/app.py` can answer with, not the plan's nine: the
    Task 11 review added `internal_error` (a 500 with no text) and `bad_value` to the contract.
    `forbidden` is the twelfth the shared `write_rules` dependency can raise, and it is covered
    too -- a page that showed nothing for it would look broken rather than refused."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    for code in ("card_not_placeable", "budget_exceeded", "line_moved", "confirmation_reused",
                 "corrections_capped", "correction_not_allowed", "session_required",
                 "rate_limited", "body_too_large", "bad_value", "internal_error", "forbidden"):
        assert code in body, code
    assert "already recorded at" in body


def test_a_refused_session_shows_the_login_prompt_rather_than_a_silent_failure():
    """Task 10's login page exists on the LAN app; a 401 is the one refusal with somewhere to
    go, so the surface offers the link instead of a dead sentence."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert '"/login"' in body


def test_the_draft_link_is_labelled_by_capability_and_carries_both_link_guards():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "link_capability" in body
    for phrase in ("Open selection", "Open event", "Copy selections"):
        assert phrase in body
    assert "Open full slip" not in body          # never in release one (addendum §1.1, D8)
    assert 'rel: "noopener noreferrer"' in body
    assert 'referrerpolicy: "no-referrer"' in body


def test_the_ideas_section_renders_the_reason_sentence_the_payload_carries():
    """Task 12's carry-forward: `reason_text` is rendered verbatim and the front end never keys
    off `reason_code` to compose prose of its own (spec §1.2)."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "reason_text" in body
    assert "no_anchor_priced" not in body and "week_at_cap" not in body


def test_the_three_sections_are_in_the_designs_order():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    order = [body.index("This week's ideas"), body.index('text: "Live tickets"'),
             body.index('text: "Season"')]
    assert order == sorted(order)


def test_the_draft_slip_carries_exactly_the_three_marks():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "PROPOSED · NOT PLACED" in body
    assert "slip-draft" in body and "perforation" in body
    assert "would pay" in body


def test_a_live_slip_stamps_what_the_payload_says_not_a_verdict_of_its_own():
    """Addendum §1.3: `CASHED`/`VOID` land on a *confirmed* row and the builder decides that,
    not the renderer. The surface draws `card.stamp` and nothing else."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "card.stamp" in body
    assert 'card.status === "cashed"' not in body


def test_the_actions_come_from_the_payload_so_the_loopback_app_stays_read_only():
    """The loopback app has no write route and Task 12 gives it `actions: []`; the slip renders
    what the payload allows rather than deciding for itself that a draft is placeable."""
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "card.actions" in body
    assert '"placed"' in body and '"decline"' in body and '"open"' in body


def test_the_card_route_scrolls_to_one_slip():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "#ticket/card/" in body
    assert "scrollIntoView" in body
    assert "CARD_ID_PATTERN" in body


def test_the_season_shows_expected_beside_returned_and_the_declined_chips():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    assert "season.expected" in body
    assert "declined_reason" in body


def test_the_live_slip_renders_the_recorded_prop_and_correction_lines():
    body = (STATIC / "js" / "ticket.mjs").read_text()
    for key in ("stat_line", "correction_note", "price_note", "context_text", "selection"):
        assert f"leg.{key}" in body, key
    assert "settlement" in body and "corrections" in body


def test_the_glossary_covers_every_label_the_two_fun_modules_use():
    glossary = json.loads((STATIC / "glossary.json").read_text())
    for term in ("replacement pending", "expected return", "confirmed return", "hung leg"):
        assert set(glossary[term]) >= {"plain", "what", "why"}


def test_the_floor_half_of_the_glossary_is_untouched():
    """Task 16 owns those four entries (ledger ruling 2026-09-14); this task adds the four
    Ticket terms beside them and edits nothing else."""
    glossary = json.loads((STATIC / "glossary.json").read_text())
    for term in ("story", "not_evaluated", "gap", "favourite"):
        assert term in glossary


def test_how_it_works_explains_ideas_placement_corrections_and_the_return():
    body = (STATIC / "how.html").read_text()
    for phrase in ("ideas", "I placed this", "correct", "confirm the return"):
        assert phrase in body
    assert "<" not in body                 # the page is fixed text, not markup


def test_the_static_budget_still_holds_with_sixty_kibibytes_of_headroom_spent():
    total = sum(p.stat().st_size for p in _all_files())
    assert total < ASSET_BUDGET_BYTES
    assert total <= 207_330 + 60 * 1024
