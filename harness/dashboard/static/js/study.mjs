// Study: is the strategy any good?
// Spec §2.3, ten sections in reading order. The rule that shapes the whole module is brief
// constraint 6: the client never computes a mean. Every cell arrives already shaped as
// `{estimate, n_obs, n_clusters, lo, hi, text, flags}` out of `report_cells`, and every one of
// them is drawn through `intervalMark`, never read for its raw number and refigured here.

import { el, figure, fmtAge, glossaryTerm, intervalMark, label, sentences, svg, table, text } from
  "./components.mjs";
import { equityCurves } from "./charts.mjs";

// The vocabulary table (spec §1.2) also names a plain/technical pair for the interval itself
// ("Honest range" / a lowercase technical term). It is omitted here on purpose: that exact
// lowercase string is the one substring `tests/test_dashboard_surfaces.py`'s
// `test_no_surface_composes_its_own_sentence` forbids in every surface's source, and the same
// string is what the glossary-coverage test requires a LABELS entry to spell verbatim. The two
// rules cannot both hold for this one term, so the label is not shown; `intervalMark` (T17)
// still carries the concept in its own accessible text, unlabelled by name.
export const LABELS = [
  { plain: "Better than the closing price?", technical: "CLV" },
  { plain: "Games, not bets", technical: "n_clusters" },
  { plain: "A week still being counted", technical: "provisional" },
  { plain: "Pinnacle's price 5 min before kickoff", technical: "pinnacle_t5" },
  { plain: "How old these cells are", technical: "report_wtd" },
];

//: The paired-contrasts benchmark every gate and report reads by (`CONTRAST_BENCHMARK` in
//: `harness/report/tables.py`). A fixed vocabulary term, not a threshold: naming it here is no
//: different from the surface naming "pulse" or "floor" to pick a snapshot.
const CONTRAST_BENCHMARK = "pinnacle_t5";
//: t2 columns that are not a benchmark family -- excluded from the CLV small multiples.
const T2_NON_BENCHMARK = new Set(["tier", "basis", "gate"]);
//: Benchmark codes the vocabulary table names; anything else still renders, under its own code,
//: per spec §1.2's rule for a reason not in the table.
const BENCHMARK_TERM = { pinnacle_t5: "pinnacle_t5",
                         kalshi_last_trade_pre_kick: "kalshi_last_trade_pre_kick",
                         result: "result" };

function cellsOf(payload, key) {
  const cells = payload.cells;
  return cells && typeof cells === "object" && !cells.error ? (cells[key] || {}) : {};
}

function columnsOf(rows) {
  const seen = [];
  for (const row of Object.values(rows)) {
    for (const col of Object.keys(row)) if (!seen.includes(col)) seen.push(col);
  }
  return seen;
}

// --- 1. week selector (read-only: the shell polls a single fixed week per surface, so a
//        picker that wrote a week nothing reads would be a dead control) ----------------------

function weekBar(payload) {
  const weeks = payload.weeks || [];
  const current = `${payload.year}-${payload.week}`;
  const chips = weeks.map((w) => el("span",
    { class: `badge ${w === current ? "ok" : "dim"}`, text: w }));
  const provisionalNote = payload.provisional
    ? el("span", { class: "n" },
        el("span", { class: "flag", text: "provisional" }), " ",
        label("how old these cells are", "report_wtd"), " ", fmtAge(payload.cell_age_s))
    : null;
  return el("div", { class: "card" },
    el("h3", { text: "Week" }),
    el("div", { class: "row" }, chips),
    provisionalNote,
    el("div", { class: "row" }, label("games, not bets", "n_clusters")));
}

// --- 2. variant ledger, t1 ----------------------------------------------------------------

function cellTable(rows, sectionLabel) {
  const rowKeys = Object.keys(rows);
  if (!rowKeys.length) return el("p", { class: "grey", text: "no rows stored for this week" });
  const columns = columnsOf(rows);
  const body = rowKeys.map((rk) => [rk, ...columns.map((c) => intervalMark(rows[rk][c]))]);
  return table(["row", ...columns], body, { label: sectionLabel });
}

function ledger(payload) {
  return el("div", { class: "card" },
    el("h3", { text: "Variant ledger" }),
    sentences(payload.sentences?.ledger),
    cellTable(cellsOf(payload, "t1"), "Variant ledger"));
}

// --- 3. CLV small multiples, t2 -------------------------------------------------------------

function clvHeader(benchmark) {
  const term = BENCHMARK_TERM[benchmark];
  return term ? glossaryTerm(term, benchmark) : el("span", { text: benchmark });
}

function clvPanels(payload) {
  const rows = cellsOf(payload, "t2");
  const rowKeys = Object.keys(rows);
  if (!rowKeys.length) return el("p", { class: "grey", text: "no rows stored for this week" });
  const benchmarks = columnsOf(rows).filter((c) => !T2_NON_BENCHMARK.has(c)
    && !c.startsWith("holm("));
  const panels = benchmarks.map((benchmark) => {
    const body = rowKeys.map((rk) => [rk, intervalMark(rows[rk][benchmark])]);
    return el("div", { class: "tile" }, el("h4", {}, clvHeader(benchmark)),
      table(["variant", "CLV"], body, { label: `CLV vs ${benchmark}` }));
  });
  return el("div", { class: "grid3" }, panels);
}

function clvSmallMultiples(payload) {
  return el("div", { class: "card" },
    el("h3", {}, "Better than the closing price?",
       el("span", { class: "technical" }, " · "), glossaryTerm("CLV", "CLV")),
    clvPanels(payload));
}

// --- 4. paired contrasts forest plot, t2 -----------------------------------------------------

function forestPlot(payload) {
  const rows = cellsOf(payload, "t2");
  const points = Object.entries(rows)
    .map(([variant, cols]) => ({ variant, cell: cols[CONTRAST_BENCHMARK] }))
    .filter((r) => r.cell && r.cell.estimate !== null && r.cell.estimate !== undefined);
  if (!points.length) {
    return el("div", { class: "card" }, el("h3", { text: "Paired contrasts" }),
      sentences(payload.sentences?.contrasts),
      el("p", { class: "grey", text: "no contrast could be paired this week" }));
  }
  const values = points.flatMap((p) => [p.cell.lo ?? p.cell.estimate, p.cell.hi ?? p.cell.estimate, 0]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = (hi - lo) * 0.15 || 1;
  const xmin = lo - pad;
  const xmax = hi + pad;
  // Sized close to the card's own pixel width, not just the plot's logical units: a `.figure`
  // stretches to 100% of the card and a small viewBox blown up to fill it would scale the axis
  // text and strokes up with it, well past the 11px `.axis-text` the CSS actually asks for.
  const plotW = 760;
  const rowH = 40;
  const labelW = 220;
  const scaleX = (v) => ((v - xmin) / (xmax - xmin)) * plotW;
  const zeroX = scaleX(0);
  const height = points.length * rowH + 16;
  const marks = points.map((p, i) => {
    const y = 12 + i * rowH;
    const loX = scaleX(p.cell.lo ?? p.cell.estimate);
    const hiX = scaleX(p.cell.hi ?? p.cell.estimate);
    const x = scaleX(p.cell.estimate);
    // A `<g>` grouping SVG shapes must itself be an SVG element (`svg("g", ...)`); `el`'s HTML
    // one silently zeroes the geometry of everything nested inside it.
    return svg("g", {},
      svg("line", { x1: loX, y1: y, x2: hiX, y2: y, style: "stroke:var(--ink-2);stroke-width:1.5" }),
      svg("circle", { cx: x, cy: y, r: 4, style: "fill:var(--accent)" }),
      svg("text", { x: plotW + 8, y: y + 4, class: "axis-text" }, `${p.variant}`),
      svg("title", {}, `${p.variant}: ${p.cell.text ?? p.cell.estimate}`));
  });
  const fig = figure({ title: payload.sentences?.contrasts?.[0]
                        || `Paired contrasts against ${CONTRAST_BENCHMARK}.`,
                       viewBox: `0 0 ${plotW + labelW} ${height}` },
    svg("line", { x1: zeroX, y1: 4, x2: zeroX, y2: height - 4, class: "axis-line" }),
    ...marks);
  return el("div", { class: "card" },
    el("h3", {}, "Paired contrasts", el("span", { class: "technical" },
       ` · vs ${CONTRAST_BENCHMARK}`)),
    sentences(payload.sentences?.contrasts), fig);
}

// --- 5. fill and markout panel, t3 -----------------------------------------------------------

function fillAndMarkout(payload) {
  return el("div", { class: "card" }, el("h3", { text: "Fill and markout" }),
    cellTable(cellsOf(payload, "t3"), "Fill and markout"));
}

// --- 6. strata heatmap, t4 --------------------------------------------------------------------

function heatCell(cell) {
  if (!cell) return el("span", { class: "grey", text: "--" });
  const flags = cell.flags || {};
  const est = cell.estimate;
  const tone = flags.greyed || est === null || est === undefined
    ? "background:var(--track)"
    : `background:${est >= 0 ? "var(--good)" : "var(--bad)"};` +
      `opacity:${Math.min(1, Math.abs(est) * 4 + 0.15).toFixed(2)}`;
  const outline = flags.flagged ? ";outline:1px dashed var(--warn);outline-offset:-1px" : "";
  return el("span", { class: "badge", style: tone + outline, text: cell.text ?? "--" });
}

function strataHeatmap(payload) {
  const rows = cellsOf(payload, "t4");
  const rowKeys = Object.keys(rows);
  const body = rowKeys.length
    ? table(["strata", ...columnsOf(rows)],
            rowKeys.map((rk) => [rk, ...columnsOf(rows).map((c) => heatCell(rows[rk][c]))]),
            { label: "Strata heatmap" })
    : el("p", { class: "grey", text: "no rows stored for this week" });
  return el("div", { class: "card" }, el("h3", { text: "Strata heatmap" }), body);
}

// --- 7. survival, validity, data quality: t5, t6, t8 ------------------------------------------

function compactTable(payload, key, title) {
  return el("div", { class: "card" }, el("h3", { text: title }),
    cellTable(cellsOf(payload, key), title));
}

// --- 8. equity curves --------------------------------------------------------------------------

function equitySection(payload) {
  const equity = payload.equity && typeof payload.equity === "object" && !payload.equity.error
    ? payload.equity : {};
  const lanes = equity.variants || [];
  const holder = el("div", {});
  if (lanes.length) requestAnimationFrame(() => equityCurves(holder, lanes));
  const annotations = Array.isArray(payload.annotations) ? payload.annotations : [];
  const annotationRows = annotations.map((a) => [a.ts, a.kind, a.summary]);
  return el("div", { class: "card" }, el("h3", { text: "Equity" }),
    sentences(payload.sentences?.equity),
    lanes.length ? holder : el("p", { class: "grey", text: "no equity recorded this week" }),
    annotationRows.length
      ? table(["when", "kind", "what"], annotationRows, { label: "Operator events this week" })
      : null);
}

// --- 9. declined candidates, t12 ---------------------------------------------------------------

function declined(payload) {
  const readings = payload.readings?.declined || [];
  return el("div", { class: "card" }, el("h3", { text: "Declined candidates" }),
    sentences(payload.sentences?.declined),
    readings.length
      ? el("ul", { class: "sentences" }, readings.map((line) =>
          el("li", { class: "reading", text: line })))
      : el("p", { class: "grey", text: "nothing was turned down this week" }));
}

// --- 10. the week's markdown --------------------------------------------------------------------

function markdown(payload) {
  if (!payload.markdown) {
    return el("div", { class: "card" }, el("h3", { text: "The week's report" }),
      el("p", { class: "grey", text: "no report has been stored for this week yet" }));
  }
  return el("div", { class: "card" },
    el("details", {},
      el("summary", { text: "The week's report" }),
      el("div", { class: "n", text: `sha256 ${payload.markdown_sha256 || "--"}` }),
      el("pre", {}, text(payload.markdown))));
}

export function render(root, payload, _envelope) {
  root.replaceChildren(
    weekBar(payload), ledger(payload), clvSmallMultiples(payload), forestPlot(payload),
    fillAndMarkout(payload), strataHeatmap(payload),
    compactTable(payload, "t5", "Survival"), compactTable(payload, "t6", "Validity"),
    compactTable(payload, "t8", "Data quality"), equitySection(payload), declined(payload),
    markdown(payload));
}
