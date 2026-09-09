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
  const data = board(payload);
  const games = (data && data.games) || [];
  return el("div", { class: "card" },
    el("h3", { text: "Game board" }),
    sentences(payload.sentences?.board),
    games.length ? el("div", { class: "grid3" }, games.map(gameCard))
                 : el("p", { class: "grey", text: "no game to show" }));
}

// --- 2. funnel -----------------------------------------------------------------------------

const FUNNEL_STAGES = [
  { key: "ticks", plain: "Raw ticks" },
  { key: "gaps", plain: "Gap snapshots" },
  { key: "candidates", plain: "Candidates" },
  { key: "intents", plain: "Wanted to bet", technical: "intent" },
  { key: "orders", plain: "Simulated orders" },
  { key: "fills", plain: "Filled" },
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
  const data = funnelData(payload) || {};
  const counts = { ticks: data.ticks || 0, gaps: data.gaps || 0,
                   candidates: data.candidates || 0, intents: data.intents || 0,
                   orders: data.orders || 0, fills: data.fills || 0 };
  const max = Math.max(1, ...Object.values(counts));
  const skipped = data.skipped || [];
  const cancelled = data.cancelled || [];
  const leakRows = (skipped.length ? 1 : 0) + (cancelled.length ? 1 : 0);
  const height = FLOW_H + (leakRows ? leakRows * (LEAK_H + LEAK_GAP) + LEAK_GAP : 0);
  const width = FUNNEL_STAGES.length * (BAR_W + BAR_GAP);
  const rows = [];
  if (skipped.length) rows.push(leakRow(skipped, FLOW_H + LEAK_GAP, "fill:var(--warn)"));
  if (cancelled.length) {
    const y = FLOW_H + LEAK_GAP + (skipped.length ? LEAK_H + LEAK_GAP : 0);
    rows.push(leakRow(cancelled, y, "fill:var(--bad)"));
  }
  const fig = figure({ title: payload.sentences?.funnel?.[0] || "The funnel over the window.",
                       viewBox: `0 0 ${width} ${height}` },
    ...funnelBars(counts, max), ...rows.flat());
  const countRows = FUNNEL_STAGES.map((stage) => [stage.plain, counts[stage.key]]);
  const reasonRows = [
    ...skipped.map((s) => [s.plain || s.reason, s.reason, s.count]),
    ...cancelled.map((s) => [s.plain || s.reason, s.reason, s.count]),
  ];
  return el("div", { class: "card" },
    el("h3", { text: `Funnel, trailing ${data.window_h ?? "--"} h` }),
    sentences(payload.sentences?.funnel),
    fig,
    table(["stage", "count"], countRows, { label: "Funnel counts" }),
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
  const cells = [el("span", { class: "n", text: order.ticker }), order.side,
                 order.prob ?? "--", queueCell, edge, fmtAge(order.age_s),
                 order.book_source || "--", order.dirty_minutes ?? "--", order.variant || "--"];
  return { cells, holder };
}

function openOrders(payload) {
  const data = orders(payload) || {};
  const rows = data.orders || [];
  const built = rows.map(orderRow);
  const body = table(
    [label("orders ahead of ours", "queue_ahead_at_place"), "side", "prob", "queue", "edge",
     "age", "book", "dirty min", "variant"],
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
  const data = fills(payload) || {};
  const rows = (data.fills || []).map((fill) => [
    fill.filled_at, fill.ticker, fill.side, String(fill.prob ?? "--"),
    String(fill.contracts ?? "--"), fillMethodLabel(fill.fill_method),
    fill.has_print ? el("span", { class: "badge ok", text: "printed" }) : "--",
  ]);
  return el("div", { class: "card" }, el("h3", { text: "Fills, today" }),
            table(["filled at", "ticker", "side", "prob", "contracts", "method",
                   label("a real trade printed at our price", "has_print")],
                  rows, { label: "Fills" }));
}

// --- 5. exposure lanes -----------------------------------------------------------------------

function exposureLane(lane) {
  return el("div", { class: "tile" },
    el("div", { class: "row spread" }, el("span", { class: "badge dim", text: lane.variant })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open stake" }),
      el("span", { class: "val num", text: String(lane.open_stake ?? "--") })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open contracts" }),
      el("span", { class: "val num", text: String(lane.open_contracts ?? "--") })),
    el("div", { class: "row spread" }, el("span", { class: "n", text: "open orders" }),
      el("span", { class: "val num", text: String(lane.n_open_orders ?? "--") })),
    el("div", { class: "row spread" },
      label("simulated cash", "equity_snapshots"),
      el("span", { class: "val num", text: String(lane.cash ?? "--") }),
      el("span", { class: "n", text: `+${lane.mtm_open ?? "--"} open` })));
}

function exposureLanes(payload) {
  const data = exposure(payload) || {};
  const lanes = data.lanes || [];
  return el("div", { class: "card" }, el("h3", { text: "Exposure, by variant" }),
    lanes.length ? el("div", { class: "grid3" }, lanes.map(exposureLane))
                 : el("p", { class: "grey", text: "no lane reporting yet" }));
}

// --- 6. executor vitals strip ------------------------------------------------------------------

function vitalsStrip(payload) {
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
  const data = venue(payload) || {};
  const ok = data.tripwire_ok !== false;
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
