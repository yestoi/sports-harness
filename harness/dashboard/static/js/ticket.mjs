// Ticket: the week's fun parlays.
// Spec §2.5, phase 4.6 addendum §1 and design §2: three sections in the design's order --
// this week's ideas, live tickets, season -- with one slip object in two states, so there is
// one thing to learn. This is the one surface that shows real money -- the owner's weekly fun
// budget, placed by hand -- and the one that is allowed to be loud. It never names a strategy
// configuration, never shows an execution figure, and never shows a fee-net trading edge: this
// page and the harness's own research stay visibly apart, and the module never even spells the
// word that names the other side of that line -- a reviewer can grep this file for it and find
// nothing.
//
// Nothing here reaches a sportsbook. `I placed this` records a slip the owner already placed by
// hand, through the two owner routes of addendum §5; every refusal those routes can answer with
// has a fixed sentence below, and `api.mjs` is still the only module that talks to the network.

import { el, label, sentences, svg } from "./components.mjs";
import { legProbHistory } from "./charts.mjs";
import { postJson } from "./api.mjs";

export const LABELS = [
  { plain: "What still needs to happen", technical: "needs" },
  { plain: "Sharps say", technical: "sharp_p" },
  // Phase 4.6 (addendum §8): the four Ticket terms. Task 16 owns the Floor half of
  // `glossary.json` and this task adds only these four entries beside it.
  { plain: "A replacement is being built", technical: "replacement pending" },
  { plain: "Expected return", technical: "expected return" },
  { plain: "Confirmed return", technical: "confirmed return" },
  { plain: "A leg whose final stat never arrived", technical: "hung leg" },
];

const LAMP_GLYPH = { pending: "○", alive: "●", hit: "●", miss: "●" };
//: A draft's legs carry a hollow ring where a placed card has a lamp, and no lamp animation
//: runs on a draft (design §2.1, mark 3 of 3).
const DRAFT_RING = "○";

//: A guarded section can arrive as `{"error": "<ClassName>"}` rather than a list; `.filter` or
//: `.length` on that dict throws before `render` ever replaces the surface's children. The two
//: helpers keep "nothing is live" and "the read failed" from being said in the same words.
const listOf = (value) => (Array.isArray(value) ? value : []);
const sectionFailed = (value) => Boolean(value) && !Array.isArray(value) && value.error;

//: Money reaches this page as a decimal string with two places (`"25.00"`, plan review MI-6).
//: The dollar sign is the only thing added to it here: the figure itself is never re-rounded,
//: re-parsed or arithmetic'd in the browser.
const dollars = (value) => (value === null || value === undefined ? "--" : `$${value}`);

function unavailableCard(title) {
  return el("div", { class: "card" }, el("h3", { text: title }),
    el("p", { class: "grey", text: "unavailable" }));
}

// --- the draft slip and the live slip ---------------------------------------------------------

function perforation(width) {
  const dots = [];
  const step = 10;
  for (let x = 4; x < width; x += step) {
    dots.push(svg("circle", { cx: x, cy: 4, r: 1.6, style: "fill:var(--ground)" }));
    dots.push(svg("circle", { cx: x, cy: 20, r: 1.6, style: "fill:var(--ground)" }));
  }
  return svg("svg", { viewBox: `0 0 ${width} 24`, width: "100%", height: "24",
                      "aria-hidden": "true", class: "figure" }, dots);
}

//: `SMART CARD · $25`, `LOTTERY CARD · $5`, `LOTTERY · SAME GAME · $5` (design §2.1). Built from
//: the card's own recorded kind, correlation flag and stake, never from the slot it sits in.
function kindBadgeText(card) {
  const head = card.kind === "smart" ? "SMART CARD"
    : card.correlated ? "LOTTERY · SAME GAME" : "LOTTERY CARD";
  return card.stake_text ? `${head} · ${dollars(card.stake_text)}` : head;
}

//: The same text for a slot that has no card to show it on: the shape the builder was asked for.
function shapeBadgeText(slot) {
  if (slot.shape === "smart") return "SMART CARD";
  return slot.shape === "lottery_same_game" ? "LOTTERY · SAME GAME" : "LOTTERY CARD";
}

function legRow(leg, draft) {
  const glyph = draft ? DRAFT_RING : (LAMP_GLYPH[leg.status] || "○");
  const lampClass = draft ? "lamp pending" : `lamp ${leg.status}`;
  const score = leg.home_score !== null && leg.home_score !== undefined
    ? `${leg.away_score}-${leg.home_score} (${leg.period ?? "--"}, ${leg.clock || "--"})`
    : "not started";
  const sharp = leg.sharp_p !== null && leg.sharp_p !== undefined
    ? `${Math.round(leg.sharp_p * 100)} %` : "--";
  const historyHolder = el("div", { class: "spark" });
  // A draft has nothing to animate and no history to draw: the bar belongs to a leg being
  // watched, not to one being offered.
  if (!draft && (leg.sharp_p_history || []).length) {
    requestAnimationFrame(() => legProbHistory(historyHolder, leg.sharp_p_history));
  }
  const stamp = !draft && leg.status === "hit" ? el("span", { class: "badge ok", text: "HIT" })
    : !draft && leg.status === "miss" ? el("span", { class: "badge bad", text: "torn" }) : null;
  const rows = [
    el("div", { class: "row" },
      el("span", { class: lampClass, "aria-hidden": "true", text: glyph }),
      el("span", { text: leg.plain_text }),
      draft ? null : el("span", { class: "n", text: String(leg.dk_american ?? "--") }),
      stamp),
    // The exact selection in small technical type: market, period, the DraftKings price and
    // that price's age, exactly as the builder composed it (design §2.1).
    el("div", { class: "n", text: leg.selection || "--" }),
  ];
  // The context line: `sharps say NN % at build`, `avg 262 · last 241 · ESPN`, or `no season
  // data yet`. One recorded sentence, rendered verbatim.
  if (leg.context_text) rows.push(el("div", { class: "n", text: leg.context_text }));
  if (leg.price_note) rows.push(el("div", { class: "n warn", text: leg.price_note }));
  if (!draft) {
    if (leg.stat_line) rows.push(el("div", { class: "n", text: leg.stat_line }));
    // The number moves down with no animation and the lamp does not replay (design §2.3).
    if (leg.correction_note) rows.push(el("div", { class: "n", text: leg.correction_note }));
    rows.push(el("div", { class: "row spread" },
      el("span", { class: "n", text: score }),
      el("span", { class: "row" }, label("what still needs to happen", "needs"),
         el("span", { class: "n", text: leg.needs || "--" }))));
    rows.push(el("div", { class: "row" }, label("sharps say", "sharp_p"),
      el("span", { class: "val num", text: sharp }), historyHolder));
  }
  return el("div", { class: "leg col" }, rows);
}

//: The four footer lines of design §2.1, each already written by the builder (`card.footer`):
//: the combined price with its kind, the chance line with its source, the hold line only when
//: the payload carries one, and the week line. Nothing here is composed from a code.
function cardFooter(card) {
  const footer = card.footer || {};
  return el("div", { class: "col" },
    el("div", { class: "n", text: footer.combined || "--" }),
    el("div", { class: "n", text: footer.chance || "--" }),
    footer.hold ? el("div", { class: "n", text: `hold ${footer.hold}` }) : null,
    footer.age_warning ? el("div", { class: "n warn", text: footer.age_warning }) : null,
    el("div", { class: "n", text: footer.week || "--" }));
}

//: The two returns, side by side (design §2.3, addendum §1.3): the harness's own arithmetic on
//: what the owner recorded, and the figure the owner typed off their DraftKings slip. They are
//: different kinds of fact and are never folded into one another.
function settlementRow(card) {
  const settlement = card.settlement || {};
  const expected = settlement.kind === "computed" ? dollars(settlement.amount) : "--";
  const confirmed = settlement.kind === "confirmed" ? dollars(settlement.amount) : "--";
  if (expected === "--" && confirmed === "--") return null;
  return el("div", { class: "row spread" },
    el("span", { class: "row" }, label("expected return", "expected return"),
       el("span", { class: "val num", text: expected })),
    el("span", { class: "row" }, label("confirmed return", "confirmed return"),
       el("span", { class: "val num", text: confirmed })));
}

//: `corrected` beside the stake, with the original figures behind a native disclosure (design
//: §2.3). The values are the strings the write route recorded -- what the owner typed.
function correctionsDetails(card) {
  const rows = listOf(card.corrections);
  if (!rows.length) return null;
  return el("details", { class: "event" },
    el("summary", { text: "corrected" }),
    el("div", { class: "col" }, rows.map((row) => el("span", { class: "n",
      text: `${row.field}: ${row.old_value ?? "--"} to ${row.new_value ?? "--"}` }))));
}

const STAMP_CLASS = { CASHED: "cashed", BUSTED: "busted", VOID: "muted" };

//: One slip, in whichever state the payload says it is in (design §8.1). A draft and a live
//: ticket differ in exactly five things -- the perforation, the badge, the rings, the payout
//: line and the actions -- and in nothing else, so they are built here by one function.
function ticketSlip(card) {
  const draft = card.status === "proposed";
  const dim = !draft && card.legs_remaining > 0 && card.legs?.length
    ? Math.max(0.4, card.legs_remaining / card.legs.length) : 1;
  // The stamp is the builder's (addendum §1.3): CASHED and VOID are claims about what a book
  // paid and land only on a confirmed row, which this renderer cannot know for itself.
  const stampWord = card.stamp || null;
  // `would pay $137.50 at +450` (design §2.1): the price is `card.combined_american`, the same
  // string the builder wrote the footer's combined sentence from, carried as its own key so
  // this line never parses a sentence. A card with no recorded price prints the payout alone.
  const payoutText = card.combined_american
    ? `would pay ${dollars(card.payout_text)} at ${card.combined_american}`
    : `would pay ${dollars(card.payout_text)}`;
  const payoutRow = draft
    // The body-size payout line, not the 40 px live payout: the large one is earned by
    // placement (design §2.1). The footer below still carries the price's own sentence.
    ? el("div", { class: "row spread" },
        el("span", { text: payoutText }),
        el("span", { class: "muted n", text: `stake ${dollars(card.stake_text)}` }))
    : el("div", { class: "row spread" },
        el("span", { class: "payout", style: `opacity:${dim.toFixed(2)}`,
                     text: dollars(card.payout_text) }),
        el("span", { class: "muted n", text: `stake ${dollars(card.stake_text)}` }),
        card.corrected ? el("span", { class: "badge dim", text: "corrected" }) : null);
  const slip = el("div", { class: draft ? "slip slip-draft" : "slip",
                           id: `ticket-card-${card.card_id}` },
    draft ? el("div", { class: "perforation" }) : perforation(360),
    el("div", { class: "row spread" },
      el("span", { class: "badge dim", text: kindBadgeText(card) }),
      draft ? el("span", { class: "badge dim mark", text: "PROPOSED · NOT PLACED" })
            : el("span", { class: "n", text: `legs · ${card.legs_remaining ?? "--"} to go` })),
    // The anchor the card was built around, under the badge (design §2). `card.anchor.text` is
    // the leg's own `plain_text`, so this line and the leg below it say the same thing in the
    // same words; nothing is composed here.
    card.anchor ? el("div", { class: "n", text: `carries ${card.anchor.text}` }) : null,
    sentences(card.sentences),
    // The builder's own rationale, in the fan voice, on the draft it belongs to: a live
    // slip's prose is `card.sentences`, and saying both would be saying it twice.
    draft && card.rationale ? el("p", { text: card.rationale }) : null,
    payoutRow,
    el("div", { class: "col" }, (card.legs || []).map((leg) => legRow(leg, draft))),
    cardFooter(card),
    draft ? null : settlementRow(card),
    draft ? null : correctionsDetails(card),
    actionsSection(card),
    draft ? el("div", { class: "perforation bottom" }) : perforation(360),
    stampWord ? el("div", { class: `stamp ${STAMP_CLASS[stampWord] || "muted"}`,
                            text: stampWord }) : null);
  return slip;
}

// --- the three actions (design §2.1, addendum §5) ----------------------------------------------

const OPEN_LABEL = { selection: "Open selection", event: "Open event", none: "Copy selections" };

//: Every control this surface draws meets the 44 px tap-target floor (design §6). `app.css` is
//: Task 14's file and is not edited by this task, so the floor travels with the control rather
//: than with a new class nobody else would know to apply.
const TAP_TARGET = "min-height:44px";

//: The one scheme a deep link may carry. The link itself is validated at normalize time and is
//: deliberately never re-sanitized here (addendum §1.1) -- this is a rendering decision, not a
//: second sanitizer: a link that is not HTTPS is not opened at all, and the button falls back to
//: copying the selections, which is what a card with no usable link gets anyway.
const SAFE_SCHEME = "https:";

function linkHref(url) {
  if (!url) return null;
  try {
    return new URL(String(url)).protocol === SAFE_SCHEME ? String(url) : null;
  } catch (error) {
    return null;
  }
}

//: `Open selection` / `Open event` / `Copy selections`, labelled by the capability the builder
//: recorded (already dropped to `none` when any leg is no longer offered). The anchor carries
//: both link guards, so DraftKings never learns which page sent the owner.
function openControl(card, message) {
  const capability = card.link_capability;
  const href = capability === "none" ? null
    : linkHref((card.legs || []).map((leg) => leg.dk_link).find(Boolean));
  if (href) {
    return el("a", { class: "action tab", href, target: "_blank", style: TAP_TARGET,
                     rel: "noopener noreferrer", referrerpolicy: "no-referrer",
                     text: OPEN_LABEL[capability] || OPEN_LABEL.none });
  }
  const button = el("button", { class: "action tab", type: "button", style: TAP_TARGET,
                                text: OPEN_LABEL.none });
  button.addEventListener("click", () => {
    const lines = (card.legs || []).map((leg) => leg.selection).filter(Boolean).join("\n");
    const clipboard = navigator.clipboard;
    if (!clipboard || typeof clipboard.writeText !== "function") {
      message.textContent = "This browser will not let the page copy. The selections are on "
        + "the slip above.";
      return;
    }
    clipboard.writeText(lines).then(
      () => { message.textContent = "Selections copied. Copying recorded nothing."; },
      () => { message.textContent = "The copy did not go through. The selections are on the "
        + "slip above."; });
  });
  return button;
}

//: `Not this one` cannot be undone from the page -- there is no undo route -- so it asks twice:
//: the first tap arms the button, the second one writes the decline (addendum §5.4).
function declineButton(card, message) {
  const button = el("button", { class: "action tab", type: "button", style: TAP_TARGET,
                               text: "Not this one" });
  let armed = false;
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = "Tap again: not this one";
      message.textContent = "Declining cannot be undone from this page; a replacement is built "
        + "in its place.";
      return;
    }
    button.disabled = true;
    const answer = await postJson("/api/parlay/correct",
      { card_id: card.card_id, field: "status", new_value: "declined" });
    button.disabled = false;
    armed = false;
    button.textContent = "Not this one";
    if (answer.status === 200) {
      message.textContent = "Declined. A replacement is being built; this page shows it at the "
        + "next refresh.";
      return;
    }
    showRefusal(message, answer);
  });
  // Arming expires when focus leaves: a button left half-pressed while the owner read something
  // else must not decline a card on the next stray tap.
  button.addEventListener("blur", () => {
    if (!armed) return;
    armed = false;
    button.textContent = "Not this one";
  });
  return button;
}

function actionsSection(card) {
  const actions = listOf(card.actions);
  // A payload with no actions is a read-only slip: the loopback listener has no write route at
  // all, and a placed card's money has already moved.
  if (!actions.length) return null;
  const message = el("p", { class: "n" });
  const row = el("div", { class: "row" });
  if (actions.includes("open")) row.appendChild(openControl(card, message));
  if (actions.includes("placed")) row.appendChild(placedButton(card, message));
  if (actions.includes("decline")) row.appendChild(declineButton(card, message));
  return el("div", { class: "col" }, row, message);
}

// --- the confirm sheet (design §3, addendum §5.1) ----------------------------------------------

//: Every refusal the two routes can answer with, in plain words. The route sends a code and the
//: figures addendum §5.1 names -- never a sentence -- so the sentence lives here, fixed, and a
//: body is never rendered as text. `internal_error` is a 500 with nothing in it at all; it gets
//: the same treatment as the rest rather than a blank sheet.
const REFUSAL_TEXT = {
  session_required: "Your session has ended. Nothing was recorded.",
  forbidden: "This page is not allowed to write from here. Nothing was recorded.",
  rate_limited: "Too many writes in a minute. Wait a moment and try again; nothing was recorded.",
  body_too_large: "That was more than the listener accepts. Shorten the note; nothing was "
    + "recorded.",
  bad_value: "One of those values is not one the listener accepts. Nothing was recorded.",
  confirmation_reused: "That confirmation belongs to another card. Nothing was recorded.",
  card_not_placeable: "This card is no longer open. Nothing was recorded.",
  budget_exceeded: "This week's budget is already spoken for. Nothing was recorded.",
  line_moved: "DraftKings moved a line. Type the line you got, or cancel.",
  corrections_capped: "This card already carries every correction it can. Nothing was recorded.",
  correction_not_allowed: "That is not a correction the table allows. Nothing was recorded.",
  internal_error: "The listener refused without saying why. Nothing was recorded.",
};

const NO_ANSWER = "The listener did not answer. Nothing was recorded here; check Live tickets "
  + "before trying again.";
const UNNAMED_REFUSAL = "The listener refused. Nothing was recorded.";

//: `7:41 PM` in the reader's own clock, from an ISO instant the route recorded.
function shortTime(iso) {
  if (!iso) return "--";
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "--"
    : when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

//: `Sat 6:30 PM` -- a build time can be days out, so the day travels with the clock.
function whenText(iso) {
  if (!iso) return "--";
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "--"
    : when.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function americanText(value) {
  if (value === null || value === undefined) return "--";
  const number = Number(value);
  return Number.isFinite(number) ? `${number > 0 ? "+" : ""}${number}` : String(value);
}

//: The figures a refusal carries, appended to its fixed sentence: what is already recorded on
//: this card, what the week has left, which legs moved. Figures only -- the sentence above is
//: the only prose.
function refusalDetail(body) {
  if (!body) return "";
  if (body.refusal === "card_not_placeable" && body.placement) {
    const placement = body.placement;
    return ` It is already recorded at ${shortTime(placement.placed_at)} · `
      + `${dollars(placement.stake_actual)} at ${americanText(placement.dk_odds_actual)}.`;
  }
  if (body.refusal === "budget_exceeded") {
    return ` ${dollars(body.recorded)} recorded this week, ${dollars(body.left)} left.`;
  }
  if (body.refusal === "line_moved" && body.moved) {
    const legs = Object.keys(body.moved).sort().map(
      (seq) => `leg ${seq} from ${body.moved[seq][0]} to ${body.moved[seq][1]}`);
    return ` ${legs.join(", ")}.`;
  }
  return "";
}

//: The one place a refused write becomes something a reader can act on. A 401 is the only
//: refusal with somewhere to go, so it offers the login page rather than a dead sentence
//: (Task 10's `/login`, on the LAN listener).
function showRefusal(node, answer) {
  if (answer.status === 0) {
    node.textContent = NO_ANSWER;
    return;
  }
  const code = answer.body && typeof answer.body.refusal === "string"
    ? answer.body.refusal : null;
  if (code === "session_required" || answer.status === 401) {
    node.replaceChildren(el("span", { text: `${REFUSAL_TEXT.session_required} ` }),
                         el("a", { href: "/login", text: "Sign in again" }));
    return;
  }
  const sentence = (code && REFUSAL_TEXT[code]) || UNNAMED_REFUSAL;
  node.textContent = sentence + refusalDetail(answer.body);
}

//: One sheet at a time, and its Escape handler with it.
let openSheet = null;
let escapeHandler = null;

function attachEscape(onEscape) {
  detachEscape();
  escapeHandler = (event) => { if (event.key === "Escape") onEscape(); };
  document.addEventListener("keydown", escapeHandler);
}

//: Removed when the sheet closes, every time. A handler left on `document` would be one more
//: listener per open, all of them still firing on a page that has no sheet (Task 16's review).
function detachEscape() {
  if (escapeHandler) {
    document.removeEventListener("keydown", escapeHandler);
    escapeHandler = null;
  }
}

function closeSheet() {
  detachEscape();
  if (!openSheet) return;
  const { dialog, opener } = openSheet;
  openSheet = null;
  if (dialog.open) dialog.close();
  dialog.remove();
  // Focus goes back where it came from, so a keyboard reader is not dropped at the top of the
  // page after recording a slip.
  if (opener && opener.isConnected) opener.focus();
}

const SECURE_CONTEXT_REFUSAL = "Nothing can be recorded until this page is opened over HTTPS "
  + "at the LAN address. Open it there and the sheet will work.";

//: `crypto.randomUUID` is undefined outside a secure context, so the sheet refuses to open and
//: says why rather than throwing on the owner's first tap (A-Minor 2).
function canMintId() {
  return Boolean(window.isSecureContext && window.crypto
                 && typeof window.crypto.randomUUID === "function");
}

//: A fresh id, whenever the sheet is opened and whenever the sheet is *edited after a refusal*
//: (Task 11's carried item). An identical resend therefore carries the id the first submit did
//: and is idempotent; a corrected resend carries a new one and is never refused as
//: `confirmation_reused` by accident. The id is shown back as text and nothing else.
function mintConfirmationId(state) {
  state.confirmationId = window.crypto.randomUUID();
  state.refused = false;
  state.idNode.textContent = `confirmation ${state.confirmationId}`;
}

function sheetField(labelText, input) {
  return el("div", { class: "row spread" }, el("span", { class: "lbl", text: labelText }), input);
}

function legLineInputs(card, state) {
  const rows = (card.legs || []).map((leg) => {
    const input = el("input", { type: "text", inputmode: "text",
                                value: leg.threshold === null || leg.threshold === undefined
                                       ? "" : String(leg.threshold),
                                "aria-label": `line you got for ${leg.plain_text}` });
    const was = el("span", { class: "n" });
    input.addEventListener("input", () => { if (state.refused) mintConfirmationId(state); });
    state.legInputs.push({ seq: leg.seq, input, was, original: leg.threshold });
    return el("div", { class: "col" },
      el("span", { text: leg.plain_text }),
      el("span", { class: "n", text: leg.selection || "--" }),
      el("div", { class: "row" }, input, was));
  });
  return el("div", { class: "col" }, rows);
}

//: The submitted body of addendum §5.1. The stake and the lines go as the strings they were
//: typed as -- money is a decimal string on the wire, never a float -- and the odds as the
//: integer the route's own validator expects.
function submitBody(state) {
  const lines = {};
  for (const entry of state.legInputs) {
    const typed = entry.input.value.trim();
    if (typed === "" || typed === String(entry.original ?? "")) continue;
    lines[String(entry.seq)] = typed;
  }
  const note = state.noteInput.value.trim();
  return {
    card_id: state.card.card_id,
    confirmation_id: state.confirmationId,
    stake: state.stakeInput.value.trim(),
    accepted_odds: Number.parseInt(state.oddsInput.value.trim(), 10),
    leg_lines: Object.keys(lines).length ? lines : null,
    note: note === "" ? null : note,
  };
}

//: A moved line is a different bet: the fields the route named are filled in with what
//: DraftKings actually has and each one says what it was, so the resend is the owner's own
//: correction rather than a retry of the same figures (design §3.2).
function applyMovedLines(state, moved) {
  for (const entry of state.legInputs) {
    const move = moved[String(entry.seq)];
    if (!move) continue;
    entry.input.value = String(move[1]);
    entry.was.textContent = `was ${move[0]}`;
  }
}

async function record(state) {
  const body = submitBody(state);
  // Two fields the sheet can check for itself, so an empty one costs a refusal round trip and a
  // spent write allowance rather than a sentence. Everything else is the route's own judgement.
  if (body.stake === "") {
    state.message.textContent = "Type the stake DraftKings took. Nothing was recorded.";
    return;
  }
  if (!Number.isFinite(body.accepted_odds)) {
    state.message.textContent = "Type the price DraftKings accepted, as an American price "
      + "(for example +450). Nothing was recorded.";
    return;
  }
  state.recordButton.disabled = true;
  state.message.textContent = "Recording ...";
  const answer = await postJson("/api/parlay/placed", body);
  state.recordButton.disabled = false;
  if (answer.status === 200 && answer.body && answer.body.placement) {
    const placement = answer.body.placement;
    state.done(`Recorded ${dollars(placement.stake_actual)} at `
      + `${americanText(placement.dk_odds_actual)} at ${shortTime(placement.placed_at)}. `
      + "This slip moves to Live tickets at the next refresh.");
    return;
  }
  state.refused = true;
  showRefusal(state.message, answer);
  if (answer.body && answer.body.refusal === "line_moved" && answer.body.moved) {
    applyMovedLines(state, answer.body.moved);
  }
}

//: The sheet itself: a native `<dialog>`, which brings its own focus trap and its own backdrop
//: (`dialog.sheet` is a centred sheet at 1440 px and a bottom sheet under 720 px, Task 14's
//: app.css). It is appended to the document rather than to the surface root, so a poll that
//: re-renders the page underneath does not tear it out from under the owner mid-typing.
function buildSheet(card, opener, slipMessage) {
  const state = { card, legInputs: [], refused: false, confirmationId: null };
  state.stakeInput = el("input", { type: "text", inputmode: "decimal",
                                   value: card.stake_text || "", "aria-label": "Stake" });
  state.oddsInput = el("input", { type: "text", inputmode: "text", placeholder: "required",
                                  "aria-label": "Accepted odds" });
  state.noteInput = el("input", { type: "text", maxlength: 200, "aria-label": "Note, optional" });
  state.message = el("p", { class: "n warn" });
  state.idNode = el("p", { class: "n" });
  state.recordButton = el("button", { class: "action tab", type: "button",
                                      style: TAP_TARGET, text: "Record" });
  const cancel = el("button", { class: "action tab", type: "button", style: TAP_TARGET,
                                text: "Cancel" });
  // A fresh id on the first edit after a refusal, from whichever field was corrected.
  state.stakeInput.addEventListener("input", () => { if (state.refused) mintConfirmationId(state); });
  state.oddsInput.addEventListener("input", () => { if (state.refused) mintConfirmationId(state); });
  const body = el("div", { class: "card col" },
    el("h3", { text: "I placed this" }),
    el("p", { class: "n", text: "Record what DraftKings actually accepted." }),
    state.message,
    sheetField("Stake", state.stakeInput),
    sheetField("Accepted odds", state.oddsInput),
    el("h4", { text: "Legs · line you got" }),
    legLineInputs(card, state),
    sheetField("Note · optional", state.noteInput),
    el("div", { class: "row" }, state.recordButton, cancel),
    el("p", { class: "n", text: "Opening DraftKings recorded nothing. Only Record writes the "
      + "stake, and a repeat tap records nothing twice." }),
    state.idNode);
  const dialog = el("dialog", { class: "sheet", "aria-label": "I placed this" }, body);
  state.dialog = dialog;
  state.done = (sentence) => {
    slipMessage.textContent = sentence;
    closeSheet();
  };
  state.recordButton.addEventListener("click", () => { record(state); });
  cancel.addEventListener("click", () => closeSheet());
  // The browser's own Escape on a modal dialog fires `cancel`; the listener below is the one
  // the walkthrough greps for, and both end at the same close.
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeSheet(); });
  return { state, dialog, opener };
}

function placedButton(card, message) {
  const button = el("button", { class: "action tab", type: "button", style: TAP_TARGET,
                               text: "I placed this" });
  button.addEventListener("click", () => {
    if (!canMintId()) {
      message.textContent = SECURE_CONTEXT_REFUSAL;
      return;
    }
    closeSheet();
    const sheet = buildSheet(card, button, message);
    openSheet = { dialog: sheet.dialog, opener: button };
    document.body.appendChild(sheet.dialog);
    mintConfirmationId(sheet.state);
    sheet.dialog.showModal();
    attachEscape(closeSheet);
  });
  return button;
}

// --- this week's ideas (addendum §1.1, §1.2) ---------------------------------------------------

//: A slot with no draft carries one sentence the builder wrote for its reason code. It is
//: rendered verbatim: the vocabulary lives in `sentences.py`, and a second copy of it in the
//: browser would be a second thing to keep in step (spec §1.2, Task 12's carry-forward).
function slotCard(slot) {
  if (slot.card) return ticketSlip(slot.card);
  return el("div", { class: "slip slip-draft" },
    el("div", { class: "perforation" }),
    el("div", { class: "row spread" },
      el("span", { class: "badge dim", text: shapeBadgeText(slot) }),
      el("span", { class: "n", text: String(slot.sport || "--").toUpperCase() })),
    el("p", { text: slot.reason_text || "--" }),
    el("div", { class: "n", text: `next build ${whenText(slot.next_build_at)}` }),
    el("div", { class: "perforation bottom" }));
}

function ideasSection(payload) {
  if (sectionFailed(payload.ideas)) return unavailableCard("This week's ideas");
  const ideas = payload.ideas || {};
  const slots = listOf(ideas.slots);
  return el("div", { class: "card" }, el("h3", { text: "This week's ideas" }),
    el("div", { class: "row spread" },
      el("span", { class: "row" }, el("span", { class: "lbl", text: "recorded this week" }),
         el("span", { class: "val num", text: dollars(ideas.week_recorded) })),
      el("span", { class: "row" }, el("span", { class: "lbl", text: "left this week" }),
         el("span", { class: "val num", text: dollars(ideas.week_left) }))),
    slots.length
      // Three across at 1440 px and stacked at 390 px, through the shipped grid alone.
      ? el("div", { class: "grid3" }, slots.map(slotCard))
      : el("p", { class: "grey", text: "no idea for this week yet" }));
}

// --- live tickets ------------------------------------------------------------------------------

function liveTickets(payload) {
  if (sectionFailed(payload.cards)) {
    return el("div", { class: "card" }, el("h3", { text: "Live tickets" }),
      el("p", { class: "grey", text: "unavailable" }));
  }
  const cards = listOf(payload.cards);
  const smart = cards.filter((c) => c.kind === "smart");
  const rest = cards.filter((c) => c.kind !== "smart");
  const ordered = [...smart, ...rest];
  return el("div", { class: "card" }, el("h3", { text: "Live tickets" }),
    ordered.length ? el("div", { class: "col" }, ordered.map(ticketSlip))
                   : el("p", { class: "grey", text: "no ticket live right now" }));
}

// --- season strip --------------------------------------------------------------------------

//: A declined or expired card moved no money: it is a grey chip carrying its own reason, beside
//: the cashed and busted ones, so the offered history stays visible (addendum §1.4).
function seasonChip(row) {
  const level = row.status === "cashed" ? "ok" : row.status === "busted" ? "bad" : "dim";
  const tail = row.declined_reason ? row.declined_reason : row.status;
  return el("span", { class: `badge ${level}`, text: `${row.year}-${row.week} ${tail}` });
}

function seasonStrip(payload) {
  const season = payload.season || {};
  const strip = season.strip || [];
  const chips = strip.map(seasonChip);
  return el("div", { class: "card" }, el("h3", { text: "Season" }),
    el("div", { class: "grid3" },
      el("div", { class: "tile" }, el("span", { class: "lbl", text: "staked" }),
        el("span", { class: "val num", text: String(season.staked ?? "--") })),
      el("div", { class: "tile" }, el("span", { class: "lbl", text: "returned (includes stake)" }),
        el("span", { class: "val num", text: String(season.returned ?? "--") }),
        // Beside the confirmed figure, never inside it: a return the owner has not confirmed is
        // a different kind of fact (addendum §1.4, D18).
        season.expected ? el("span", { class: "row" },
          label("expected return", "expected return"),
          el("span", { class: "n", text: dollars(season.expected) })) : null),
      el("div", { class: "tile" }, el("span", { class: "lbl", text: "net" }),
        el("span", { class: "val num", text: String(season.net ?? "--") }))),
    season.best_hit ? el("div", { class: "n", text:
      `best hit: week ${season.best_hit.week}, ${season.best_hit.amount}` }) : null,
    el("div", { class: "n", text: `streak: ${season.streak ?? 0}` }),
    chips.length ? el("div", { class: "row" }, chips) : null);
}

// --- between cards ---------------------------------------------------------------------------

function betweenCards(payload) {
  const between = payload.between || {};
  return el("div", { class: "card" }, el("h3", { text: "Between cards" }),
    sentences(payload.sentences?.between),
    el("div", { class: "n", text: `next card built: ${between.next_build_day || "--"}` }),
    el("div", { class: "n", text: between.anchor_rule || "--" }),
    el("div", { class: "n", text:
      `budget left this week: ${between.budget_left ?? "--"} of ${between.weekly_budget ?? "--"}` }));
}

// --- the one-slip route ------------------------------------------------------------------------

//: `#ticket/card/<card_id>` scrolls to and expands one slip (addendum §1.3). The id is a
//: database serial the builder writes as an integer; anything else in the hash -- a property
//: name, an empty segment, free text -- is rejected here rather than handed to a lookup.
const CARD_ID_PATTERN = /^[0-9]{1,10}$/;

function routeCardId() {
  const parts = String(location.hash || "").replace(/^#/, "").split("/");
  if (parts[0] !== "ticket" || parts[1] !== "card") return null;
  const id = parts[2] || "";
  return CARD_ID_PATTERN.test(id) ? id : null;
}

//: The slip the route names is scrolled to once per route, not once per poll: this surface
//: re-renders on its own cadence, and a scroll on every payload would drag the page back under
//: a reader who had scrolled away from the card they arrived at.
let revealed = null;

function revealRoutedCard() {
  const id = routeCardId();
  if (id === null) {
    revealed = null;
    return;
  }
  const node = document.getElementById(`ticket-card-${id}`);
  if (!node) return;
  for (const details of node.querySelectorAll("details")) details.open = true;
  if (revealed === id) return;
  revealed = id;
  node.scrollIntoView({ block: "start" });
}

export function render(root, payload, _envelope) {
  const cards = listOf(payload.cards);
  // Unlike `el`'s own children, `replaceChildren` does not filter falsy arguments -- it
  // stringifies whatever it is given, so a bare `null` here would show up as the literal text
  // "null" rather than simply being skipped. "Between cards" only replaces a genuinely empty
  // list, never a failed read -- `liveTickets` already says "unavailable" for that case, and
  // stacking "nothing is live" under it would be saying two different things at once.
  const children = [ideasSection(payload), liveTickets(payload), seasonStrip(payload)];
  if (!cards.length && !sectionFailed(payload.cards)) children.push(betweenCards(payload));
  root.replaceChildren(...children);
  revealRoutedCard();
}
