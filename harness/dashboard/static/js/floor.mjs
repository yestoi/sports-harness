// Floor: what is it doing right now?
// Spec §2.2's seven blocks in reading order: the game board, the funnel, the resting simulated
// orders with their queue bars, today's fills, the exposure lanes, the executor's vitals, and
// the venue tile (roadmap phase 4.5 item 5).
//
// Floor shows activity, never quality: no figure here ever judges whether a fill was a good
// one, so a good afternoon of fills is never mistaken for edge.

import { el, figure, fmtAge, glossaryTerm, label, queueBar, sentences, svg, table } from
  "./components.mjs";
import { sparkline } from "./charts.mjs";

export const LABELS = [
  { plain: "Live score", technical: "game_score_events" },
  { plain: "Markets we can price", technical: "venue_markets" },
  { plain: "Our resting simulated orders", technical: "orders" },
  { plain: "Wanted to bet", technical: "intent" },
  { plain: "Passed on", technical: "skip" },
  { plain: "Orders ahead of ours", technical: "queue_ahead_at_place" },
  { plain: "Orders still ahead of ours", technical: "queue_remaining" },
  { plain: "Edge now", technical: "fair_values" },
  { plain: "Edge when we placed it", technical: "edge_at_place" },
  { plain: "Filled from the recorded order book", technical: "queue_model" },
  { plain: "A real trade printed at our price", technical: "has_print" },
  { plain: "Simulated cash", technical: "equity_snapshots" },
  { plain: "Authenticated calls to the exchange", technical: "venue_requests" },
  { plain: "Best bid and ask", technical: "order book" },
];

//: `fill_method` values that already carry a glossary entry from the vocabulary table; a value
//: this map has never heard of still shows, its raw code standing in for its own label.
const FILL_METHOD_TERM = { queue_model: "queue_model", no_watcher: "no_watcher",
                           snapshot_cross: "snapshot_cross" };

function section(key) {
  return (payload) => {
    const value = payload[key];
    return value && typeof value === "object" && !value.error ? value : null;
  };
}

//: A guarded section (`snapshots/__init__.py`'s `section()`) writes `{"error": "<ClassName>"}`
//: on a timeout -- a normal outcome under the venue tripwire's 2 s statement timeout, not a
//: rarity -- and `section(key)` above collapses that to `null` indistinguishably from "nothing
//: to show yet". This is the one place that keeps the two apart, mirroring `pulse.mjs` and
//: `ticket.mjs`, so a failed read renders "unavailable" rather than a quiet empty state.
const sectionFailed = (value) => Boolean(value) && !Array.isArray(value) && value.error;

function unavailableCard(title) {
  return el("div", { class: "card" }, el("h3", { text: title }),
    el("p", { class: "grey", text: "unavailable" }));
}

const board = section("board");
const funnelData = section("funnel");
const orders = section("orders");
const fills = section("fills");
const exposure = section("exposure");
const vitals = section("vitals");
const venue = section("venue");

// --- 1. game board ------------------------------------------------------------------------

function gameCard(game) {
  const head = el("div", { class: "row spread" },
    el("span", { class: "team", text: `${game.away} at ${game.home}` }));
  const body = game.status === "in_progress"
    ? el("div", { class: "row" },
        el("span", { class: "val num",
                     text: `${game.away_score ?? "--"}-${game.home_score ?? "--"}` }),
        el("span", { class: "meta",
                     text: `${game.period ?? "--"} · ${game.clock || "--"}` }),
        el("span", { class: "n", text: fmtAge(game.score_age_s) }))
    : el("div", { class: "meta", text: `kicks off in ${fmtAge(game.kickoff_in_s)}` });
  return el("div", { class: "tile" }, head, body,
    el("div", { class: "row spread" },
      label("markets we can price", "venue_markets"),
      el("span", { class: "val num", text: String(game.matched_markets) })),
    el("div", { class: "row spread" },
      label("our resting simulated orders", "orders"),
      el("span", { class: "val num", text: String(game.open_orders) })));
}

function gameBoard(payload) {
  if (sectionFailed(payload.board)) return unavailableCard("Game board");
  const data = board(payload);
  const games = (data && data.games) || [];
  return el("div", { class: "card" },
    el("h3", { text: "Game board" }),
    sentences(payload.sentences?.board),
    games.length ? el("div", { class: "grid3" }, games.map(gameCard))
                 : el("p", { class: "grey", text: "no game to show" }));
}

// --- 2. funnel -----------------------------------------------------------------------------

// Addendum 0.11: each stage carries its unit, and the unit comes from the payload rather than
// being retyped here, so the label and the number cannot drift apart.
const FUNNEL_STAGES = [
  { key: "ticks", plain: "Raw ticks" },
  { key: "gaps", plain: "Gap snapshots" },
  { key: "candidate_signals", plain: "Candidate signals" },
  { key: "intent_verdicts", plain: "Intent verdicts", technical: "intent" },
  { key: "placements", plain: "Orders placed" },
  { key: "orders_filled_actual", plain: "Orders filled" },
];
const BAR_W = 26;
const BAR_GAP = 20;
const FLOW_H = 46;
const LEAK_H = 30;
const LEAK_GAP = 8;

function rect(x, y, w, h, style) {
  return svg("rect", { x, y, width: w, height: h, style });
}

function funnelBars(counts, max) {
  return FUNNEL_STAGES.map((stage, i) => {
    const x = i * (BAR_W + BAR_GAP);
    const count = counts[stage.key] || 0;
    const h = max > 0 ? Math.max(2, Math.round((count / max) * FLOW_H)) : 2;
    const parts = [rect(x, FLOW_H - h, BAR_W, h, "fill:var(--accent)")];
    if (i > 0) {
      const prevCount = counts[FUNNEL_STAGES[i - 1].key] || 0;
      const prevH = max > 0 ? Math.max(2, Math.round((prevCount / max) * FLOW_H)) : 2;
      const px = (i - 1) * (BAR_W + BAR_GAP) + BAR_W;
      parts.unshift(svg("polygon", {
        points: `${px},${FLOW_H - prevH} ${px},${FLOW_H} ${x},${FLOW_H} ${x},${FLOW_H - h}`,
        style: "fill:var(--ink-muted);opacity:0.25" }));
    }
    // A `<g>` inside an SVG tree must itself be an SVG element (`svg("g", ...)`), never `el`'s
    // HTML one: an HTML element grafted into the SVG namespace breaks layout for everything
    // nested under it, and every child here silently measures zero.
    return svg("g", {}, ...parts, svg("title", {}, stage.plain + ": " + count));
  });
}

function leakRow(entries, y, style) {
  const total = entries.reduce((sum, e) => sum + (e.count || 0), 0) || 1;
  let x = 0;
  return entries.map((entry) => {
    const w = Math.max(4, Math.round((entry.count / total) * 200));
    const node = svg("g", {}, rect(x, y, w, LEAK_H, style),
      svg("title", {}, `${entry.plain || entry.reason} (${entry.reason}): ${entry.count}`));
    x += w + 4;
    return node;
  });
}

function funnelSection(payload) {
  if (sectionFailed(payload.funnel)) return unavailableCard("Funnel");
  const data = funnelData(payload) || {};
  const counts = { ticks: data.ticks || 0, gaps: data.gaps || 0,
                   candidate_signals: data.candidate_signals || 0,
                   intent_verdicts: data.intent_verdicts || 0,
                   placements: data.placements || 0,
                   orders_filled_actual: data.orders_filled_actual || 0 };
  const max = Math.max(1, ...Object.values(counts));
  const skipped = data.skipped || [];
  const cancelled = data.cancelled || [];
  // `rejected_total` (spec §2.2 item 2's third leak: "signals rejected") has no per-reason split
  // in the payload -- unlike skips and cancels, `runs.notes` only carries the sum -- so it draws
  // as one bar rather than a broken-out table.
  const rejectedTotal = data.rejected_total || 0;
  const rejected = rejectedTotal > 0
    ? [{ reason: "rejected", count: rejectedTotal, plain: "signals rejected" }] : [];
  const leakGroups = [{ entries: skipped, style: "fill:var(--warn)" },
                      { entries: cancelled, style: "fill:var(--bad)" },
                      { entries: rejected, style: "fill:var(--ink-muted)" }]
    .filter((g) => g.entries.length);
  const height = FLOW_H + (leakGroups.length
    ? leakGroups.length * (LEAK_H + LEAK_GAP) + LEAK_GAP : 0);
  const width = FUNNEL_STAGES.length * (BAR_W + BAR_GAP);
  const rows = leakGroups.map((g, i) =>
    leakRow(g.entries, FLOW_H + LEAK_GAP + i * (LEAK_H + LEAK_GAP), g.style));
  const fig = figure({ title: payload.sentences?.funnel?.[0] || "The funnel over the window.",
                       viewBox: `0 0 ${width} ${height}` },
    ...funnelBars(counts, max), ...rows.flat());
  // Candidates by variant (spec §2.2 item 2: "candidates (by variant)"), one row per registered
  // variant beside the funnel's own total.
  const variantRows = Object.entries(data.by_variant || {})
    .map(([variant, v]) => [`candidates -- ${variant}`, v.candidate ?? 0]);
  const units = data.units || {};
  const fillRows = data.fill_rows || {};
  const countRows = [
    ...FUNNEL_STAGES.map((stage) => [stage.plain, counts[stage.key], units[stage.key] || ""]),
    ["signals rejected", rejectedTotal, "rejected signal rows"],
    ["Orders filled, counterfactual only", data.orders_filled_counterfactual || 0,
     units.orders_filled_counterfactual || ""],
    ...Object.keys(fillRows).map((method) =>
      [`fill rows -- ${method}`, fillRows[method], units.fill_rows || ""]),
    ...variantRows.map(([name, count]) => [name, count, units.candidate_signals || ""]),
  ];
  const reasonRows = [
    ...skipped.map((s) => [s.plain || s.reason, s.reason, s.count]),
    ...cancelled.map((s) => [s.plain || s.reason, s.reason, s.count]),
  ];
  return el("div", { class: "card" },
    el("h3", { text: `Funnel, trailing ${data.window_h ?? "--"} h` }),
    sentences(payload.sentences?.funnel),
    fig,
    table(["stage", "count", "unit"], countRows, { label: "Funnel counts" }),
    reasonRows.length
      ? table(["reason", "code", "count"], reasonRows, { label: "Skips and cancels" })
      : null);
}

// --- 3. open simulated orders ---------------------------------------------------------------

//: `table()` builds its own `<tr>`/`<td>` from an array of cell values, so a row helper hands
//: back that array -- never a pre-built `<tr>` -- or the row ends up nested a level too deep
//: and off the header's columns.
function orderRow(order) {
  const bar = queueBar(order);
  const holder = el("div", { class: "spark" });
  const liveEdge = order.fair_p !== null && order.fair_p !== undefined;
  const edge = liveEdge
    ? el("span", {}, label("edge now", "fair_values"),
         el("span", { class: "val num", text: String(order.edge_live ?? "--") }),
         el("span", { class: "n", text: fmtAge(order.fair_age_s) }))
    : el("span", {}, label("edge when we placed it", "edge_at_place"),
         el("span", { class: "val num", text: String(order.edge_at_place ?? "--") }));
  const queueCell = el("div", {}, bar, el("div", { class: "n", text:
    `${order.queue_remaining ?? "--"} of ${order.queue_ahead_at_place ?? "--"}` }), holder);
  // Spec §2.2 item 3: "our price against best bid and ask" -- the newest watch sample's book,
  // beside how old that read of the book is.
  const book = order.best_bid !== null && order.best_bid !== undefined
    ? el("span", { class: "row" },
         el("span", { class: "val num", text: `${order.best_bid} / ${order.best_ask}` }),
         el("span", { class: "n", text: fmtAge(order.book_age_s) }))
    : el("span", { class: "grey", text: "--" });
  const cells = [el("span", { class: "n", text: order.ticker }), order.side,
                 order.prob ?? "--", book, queueCell, edge, fmtAge(order.age_s),
                 order.book_source || "--", order.dirty_minutes ?? "--", order.variant || "--"];
  return { cells, holder };
}

function openOrders(payload) {
  if (sectionFailed(payload.orders)) {
    return el("div", { class: "card" },
      el("h3", {}, "Open simulated orders",
         el("span", { class: "technical" }, " · "), glossaryTerm("queue_remaining", "queue")),
      el("p", { class: "grey", text: "unavailable" }));
  }
  const data = orders(payload) || {};
  const rows = data.orders || [];
  const built = rows.map(orderRow);
  const body = table(
    [label("orders ahead of ours", "queue_ahead_at_place"), "side", "prob",
     label("best bid / ask", "order book"), "queue", "edge",
     "age", "book source", "dirty min", "variant"],
    built.map((row) => row.cells), { label: "Open simulated orders" });
  // The queue history sparkline draws once the holder is attached and laid out, matching how
  // Pulse's vitals tiles size their own sparklines.
  requestAnimationFrame(() => {
    rows.forEach((order, i) => {
      if ((order.queue_history || []).length) sparkline(built[i].holder, order.queue_history);
    });
  });
  return el("div", { class: "card" },
    el("h3", {}, "Open simulated orders",
       el("span", { class: "technical" }, " · "), glossaryTerm("queue_remaining", "queue")),
    sentences(payload.sentences?.orders),
    body);
}

// --- 4. fills stream -------------------------------------------------------------------------

function fillMethodLabel(method) {
  const term = FILL_METHOD_TERM[method];
  return term ? glossaryTerm(term, method) : el("span", { text: method || "--" });
}

function fillsStream(payload) {
  if (sectionFailed(payload.fills)) return unavailableCard("Fills, today");
  const data = fills(payload) || {};
  const rows = (data.fills || []).map((fill) => [
    fill.filled_at,
    fill.home && fill.away ? `${fill.away} at ${fill.home}` : "--",
    fill.ticker, fill.side, String(fill.prob ?? "--"), String(fill.contracts ?? "--"),
    fillMethodLabel(fill.fill_method),
    fill.through ? "through" : "at",
    fill.tape_source || "--",
    fill.has_print ? el("span", { class: "badge ok", text: "printed" }) : "--",
  ]);
  return el("div", { class: "card" }, el("h3", { text: "Fills, today" }),
            table(["filled at", "game", "ticker", "side", "prob", "contracts", "method",
                   "through / at", "tape source",
                   label("a real trade printed at our price", "has_print")],
                  rows, { label: "Fills" }));
}

// --- 5. exposure lanes -----------------------------------------------------------------------

function exposureLane(lane) {
  const capUse = lane.caps_enforced && lane.cap_use !== null && lane.cap_use !== undefined
    ? `${Math.round(lane.cap_use * 100)} %` : "not enforced";
  return el("div", { class: "tile" },
    el("div", { class: "row spread" }, el("span", { class: "badge dim", text: lane.variant })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open stake" }),
      el("span", { class: "val num", text: String(lane.open_stake ?? "--") })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open contracts" }),
      el("span", { class: "val num", text: String(lane.open_contracts ?? "--") })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open orders" }),
      el("span", { class: "val num", text: String(lane.n_open_orders ?? "--") })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "fills today" }),
      el("span", { class: "val num",
                   text: `${lane.fills_today ?? "--"} · ${lane.fills_today_stake ?? "--"}` })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "daily cap use" }),
      el("span", { class: "val num", text: capUse })),
    el("div", { class: "row spread" },
      label("simulated cash", "equity_snapshots"),
      el("span", { class: "val num", text: String(lane.cash ?? "--") }),
      el("span", { class: "n", text: `+${lane.mtm_open ?? "--"} open` })));
}

function exposureLanes(payload) {
  // Spec §1.1: every monetary or contract figure sits under the word "paper" in the same visual
  // unit as the figure -- the shell's header pill is a different unit, so the card carries its
  // own badge too.
  const head = el("h3", {}, "Exposure, by variant", el("span", { class: "badge dim", text: "PAPER" }));
  if (sectionFailed(payload.exposure)) {
    return el("div", { class: "card" }, head, el("p", { class: "grey", text: "unavailable" }));
  }
  const data = exposure(payload) || {};
  const lanes = data.lanes || [];
  // Addendum 0.12: a 14-day figure labelled as one. The bound is fix 31's and stays.
  const coverage = data.coverage;
  const note = coverage
    ? el("p", { class: "n", text: coverage.note })
    : null;
  return el("div", { class: "card" }, head,
    lanes.length ? el("div", { class: "grid3" }, lanes.map(exposureLane))
                 : el("p", { class: "grey", text: "no lane reporting yet" }),
    note);
}

// --- 6. executor vitals strip ------------------------------------------------------------------

function vitalsStrip(payload) {
  if (sectionFailed(payload.vitals)) return unavailableCard("Executor vitals");
  const data = vitals(payload) || {};
  const lines = data.sparklines || {};
  const names = Object.keys(lines);
  const grid = el("div", { class: "grid" }, names.map((name) => {
    const holder = el("div", { class: "spark" });
    const points = lines[name] || [];
    if (points.length) requestAnimationFrame(() => sparkline(holder, points));
    return el("div", { class: "tile" }, el("span", { class: "lbl", text: name }), holder);
  }));
  return el("div", { class: "card" },
    el("h3", { text: `Executor vitals, ${data.window_h ?? "--"} h` }), grid);
}

// --- 7. venue tile -----------------------------------------------------------------------------

function venueTile(payload) {
  if (sectionFailed(payload.venue)) {
    return el("div", { class: "card" },
      el("h3", {}, "Venue calls", el("span", { class: "technical" }, " · "),
         glossaryTerm("venue_requests", "venue_requests")),
      el("p", { class: "grey", text: "unavailable" }));
  }
  const data = venue(payload) || {};
  // A production-order tripwire must never read "no control breach" from no measurement: `ok`
  // is true only when the section actually ran and said so (a timed-out or absent read is
  // caught by the `sectionFailed` branch above, never reaches here as a quiet `undefined`).
  const ok = data.tripwire_ok === true;
  const rows = (data.by_env_method || []).map((r) => [r.env, r.method, r.count]);
  const statusRows = (data.status || []).map((r) => [r.env, r.status, r.reason || "--"]);
  return el("div", { class: "card" },
    el("h3", {}, "Venue calls", el("span", { class: "technical" }, " · "),
       glossaryTerm("venue_requests", "venue_requests")),
    el("div", { class: "row spread" },
      el("span", { class: `val num ${ok ? "ok" : "bad"}`, text: String(data.prod_non_get_24h ?? "--") }),
      el("span", { class: `badge ${ok ? "ok" : "bad"}`,
                   text: ok ? "no control breach" : "control breach" })),
    rows.length ? table(["env", "method", "count"], rows, { label: "Venue calls by env" }) : null,
    statusRows.length ? table(["env", "status", "reason"], statusRows,
                              { label: "Venue status by env" }) : null,
    el("div", { class: "n", text: `last smoke: ${data.last_smoke || "--"}` +
      (data.last_smoke_at ? ` (${fmtAge(
        (Date.now() - Date.parse(data.last_smoke_at)) / 1000)} ago)` : "") }));
}

export function render(root, payload, _envelope) {
  root.replaceChildren(
    gameBoard(payload), funnelSection(payload), openOrders(payload), fillsStream(payload),
    exposureLanes(payload), vitalsStrip(payload), venueTile(payload));
}
