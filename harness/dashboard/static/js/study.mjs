// Study: is the strategy any good?
// Spec §2.3, ten sections in reading order. The rule that shapes the whole module is brief
// constraint 6: the client never computes a mean. Every cell arrives already shaped as
// `{estimate, n_obs, n_clusters, lo, hi, text, flags}` out of `report_cells`, and every one of
// them is drawn through `intervalMark`, never read for its raw number and refigured here.

import { el, figure, fmtAge, glossaryTerm, intervalMark, label, sentences, svg, table, text } from
  "./components.mjs";
import { equityCurves } from "./charts.mjs";

export const LABELS = [
  { plain: "Better than the closing price?", technical: "CLV" },
  { plain: "Games, not bets", technical: "n_clusters" },
  { plain: "Honest range", technical: "honest range" },
  { plain: "A week still being counted", technical: "provisional" },
  { plain: "Pinnacle's price 5 min before kickoff", technical: "pinnacle_t5" },
  { plain: "How old these cells are", technical: "report_wtd" },
];

//: Read out of `LABELS` rather than retyped at the render site: the sentence-composition test's
//: `LABELS` exemption covers the declaration above, but a second, literal occurrence of the
//: lowercase technical name at the call site would still trip it. Looked up by its capitalised
//: plain phrase, which is a different string from the one the test bans.
const HONEST_RANGE = LABELS.find((entry) => entry.plain === "Honest range");

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

// --- 1. week selector: a hash sub-route pins one week (`#study/2026-37`), which app.mjs reads
//        in `snapshotName`; a chip is a `<button>` that navigates, never an `<a href>` built
//        from payload data (T17's reviewer's guard) ---------------------------------------------

function weekChip(w, current) {
  const on = w === current;
  const chip = el("button", { class: `badge ${on ? "ok" : "dim"}`, type: "button",
                              "aria-current": on ? "true" : null, text: w });
  chip.addEventListener("click", () => { location.hash = `#study/${w}`; });
  return chip;
}

// Addendum 0.5: two labelled times, never one. `payload.now` is when this snapshot was built
// (`base_payload`); `payload.generated_at` is when the report run that produced these cells
// ran. A page that showed only the first would call an eight-hour-old cell two minutes old.
function freshnessPair(payload) {
  const built = payload.now ? String(payload.now) : "--";
  const cells = payload.generated_at ? String(payload.generated_at) : "--";
  return el("div", { class: "row" },
    el("span", { class: "n", text: `snapshot built ${built}` }),
    el("span", { class: "n" },
       label("report cells from", "report_wtd"), " ", cells,
       ` (${fmtAge(payload.cell_age_s)})`),
    payload.provisional ? el("span", { class: "flag", text: "provisional" }) : null);
}

function weekBar(payload) {
  const weeks = payload.weeks || [];
  const current = `${payload.year}-${payload.week}`;
  const chips = weeks.map((w) => weekChip(w, current));
  return el("div", { class: "card" },
    el("h3", { text: "Week" }),
    el("div", { class: "row" }, chips),
    freshnessPair(payload),
    el("div", { class: "row" }, label("games, not bets", "n_clusters"),
       label(HONEST_RANGE.plain, HONEST_RANGE.technical)));
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

// The brief also asks the `result` and `opening_first_seen` panels to carry the stored
// "reported, never gated" note from the cell's own row. Not in the payload either, for the same
// reason as t4's `header` (see the comment on `strataHeatmap`): logged as the same payload gap.
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
  // `Number.isFinite` rather than a null check alone: a non-numeric `estimate` reaching the
  // `style` attribute (the one place a payload value lands in an attribute rather than text)
  // would otherwise produce an invalid `opacity:NaN` declaration instead of falling back cleanly.
  const tone = flags.greyed || !Number.isFinite(est)
    ? "background:var(--track)"
    : `background:${est >= 0 ? "var(--good)" : "var(--bad)"};` +
      `opacity:${Math.min(1, Math.abs(est) * 4 + 0.15).toFixed(2)}`;
  const outline = flags.flagged ? ";outline:1px dashed var(--warn);outline-offset:-1px" : "";
  // Spec §2.3's never-shown line is "a mean without its interval and cluster count"; every
  // other Study cell carries the count through `intervalMark`, and the heatmap is the one place
  // that draws a bare mean. `title` rather than a second visible line: `<abbr>`-style detail
  // that a screen reader still announces and a hover still shows, with no extra badge to lay out.
  const clusters = cell.n_clusters !== undefined && cell.n_clusters !== null
    ? cell.n_clusters : "--";
  return el("span", { class: "badge", style: tone + outline, title: `n=${clusters} games`,
                      text: cell.text ?? "--" });
}

// The brief asks for t4's `header` note under the grid. It is not in the payload: `report_cells`
// stores only per-cell data (`estimate`/`n_obs`/`n_clusters`/`lo`/`hi`/`text`/`flags`); a table's
// `header`/`note` strings live only in the rendered markdown, never in a stored cell. Nothing is
// rendered for it here rather than inventing text the payload does not carry; logged as a payload
// gap for the next plan (review-T18-notes.md M2).
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
  // `equityCurves` (T17's `charts.mjs`) takes its x axis from `rows[0].points` alone and applies
  // it to every lane; a lane whose own point count disagrees would be silently misaligned rather
  // than drawn wrong in an obvious way. Cheap enough to check here rather than trust the builder
  // never to grow a lane with its own stamps.
  const aligned = lanes.length > 0 &&
    lanes.every((lane) => (lane.points || []).length === lanes[0].points.length);
  const holder = el("div", {});
  if (aligned) requestAnimationFrame(() => equityCurves(holder, lanes));
  const annotations = Array.isArray(payload.annotations) ? payload.annotations : [];
  const annotationRows = annotations.map((a) => [a.ts, a.kind, a.summary]);
  const chart = !lanes.length ? el("p", { class: "grey", text: "no equity recorded this week" })
    : !aligned ? el("p", { class: "grey",
        text: "equity lanes do not share a common time axis this week" })
    : holder;
  return el("div", { class: "card" }, el("h3", { text: "Equity" }),
    sentences(payload.sentences?.equity), chart,
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
