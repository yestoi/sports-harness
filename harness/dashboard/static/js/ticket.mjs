// Ticket: the week's fun parlays.
// Spec §2.5. This is the one surface that shows real money -- the owner's weekly fun budget,
// placed by hand -- and the one that is allowed to be loud. It never names a strategy
// configuration, never shows an execution figure, and never shows a fee-net trading edge: this
// page and the harness's own research stay visibly apart, and the module never even spells the
// word that names the other side of that line -- a reviewer can grep this file for it and find
// nothing.
//
// No place button anywhere here: placement is confirmed by hand, off this page.

import { el, label, sentences, svg } from "./components.mjs";
import { legProbHistory } from "./charts.mjs";

export const LABELS = [
  { plain: "What still needs to happen", technical: "needs" },
  { plain: "Sharps say", technical: "sharp_p" },
];

const LAMP_GLYPH = { pending: "○", alive: "●", hit: "●", miss: "●" };

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

function legRow(leg) {
  const glyph = LAMP_GLYPH[leg.status] || "○";
  const score = leg.home_score !== null && leg.home_score !== undefined
    ? `${leg.away_score}-${leg.home_score} (${leg.period ?? "--"}, ${leg.clock || "--"})`
    : "not started";
  const sharp = leg.sharp_p !== null && leg.sharp_p !== undefined
    ? `${Math.round(leg.sharp_p * 100)} %` : "--";
  const historyHolder = el("div", { class: "spark" });
  if ((leg.sharp_p_history || []).length) {
    requestAnimationFrame(() => legProbHistory(historyHolder, leg.sharp_p_history));
  }
  const stamp = leg.status === "hit" ? el("span", { class: "badge ok", text: "HIT" })
    : leg.status === "miss" ? el("span", { class: "badge bad", text: "torn" }) : null;
  return el("div", { class: "row spread" },
    el("span", { class: `lamp ${leg.status}`, "aria-hidden": "true", text: glyph }),
    el("span", { class: "n", text: leg.status }),
    el("span", { class: "row" },
      el("span", { text: leg.plain_text }),
      el("span", { class: "n", text: String(leg.dk_american ?? "--") })),
    el("span", { class: "n", text: score }),
    el("span", { class: "n", text: leg.needs || "--" }),
    el("span", {}, label("sharps say", "sharp_p"), el("span", { class: "val num", text: sharp }),
       historyHolder),
    stamp);
}

function ticketSlip(card) {
  const dim = card.legs_remaining > 0 && card.legs?.length
    ? Math.max(0.4, card.legs_remaining / card.legs.length) : 1;
  const stampWord = card.status === "cashed" ? "CASHED" : card.status === "busted" ? "BUSTED" : null;
  return el("div", { class: "slip" },
    perforation(360),
    sentences(card.sentences),
    el("div", { class: "row spread" },
      el("span", { class: "payout", style: `opacity:${dim.toFixed(2)}`,
                   text: String(card.payout ?? "--") }),
      el("span", { class: "muted n", text: `stake ${card.stake ?? "--"}` }),
      stampWord ? el("span", { class: `badge ${card.status === "cashed" ? "ok" : "bad"}`,
                                text: stampWord }) : null),
    el("div", { class: "col" }, (card.legs || []).map(legRow)),
    perforation(360));
}

function liveTickets(payload) {
  const cards = payload.cards || [];
  const smart = cards.filter((c) => c.kind === "smart");
  const rest = cards.filter((c) => c.kind !== "smart");
  const ordered = [...smart, ...rest];
  return el("div", { class: "card" }, el("h3", { text: "Live tickets" }),
    ordered.length ? el("div", { class: "col" }, ordered.map(ticketSlip))
                   : el("p", { class: "grey", text: "no ticket live right now" }));
}

// --- season strip --------------------------------------------------------------------------

function seasonStrip(payload) {
  const season = payload.season || {};
  const strip = season.strip || [];
  const chips = strip.map((row) => el("span",
    { class: `badge ${row.status === "cashed" ? "ok" : row.status === "busted" ? "bad" : "dim"}`,
      text: `${row.year}-${row.week} ${row.status}` }));
  return el("div", { class: "card" }, el("h3", { text: "Season" }),
    el("div", { class: "grid3" },
      el("div", { class: "tile" }, el("span", { class: "lbl", text: "staked" }),
        el("span", { class: "val num", text: String(season.staked ?? "--") })),
      el("div", { class: "tile" }, el("span", { class: "lbl", text: "returned" }),
        el("span", { class: "val num", text: String(season.returned ?? "--") })),
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

export function render(root, payload, _envelope) {
  const cards = payload.cards || [];
  // Unlike `el`'s own children, `replaceChildren` does not filter falsy arguments -- it
  // stringifies whatever it is given, so a bare `null` here would show up as the literal text
  // "null" rather than simply being skipped.
  const children = [liveTickets(payload), seasonStrip(payload)];
  if (!cards.length) children.push(betweenCards(payload));
  root.replaceChildren(...children);
}
