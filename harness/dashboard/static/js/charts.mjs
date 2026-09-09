// uPlot for time series and nothing else. Every other figure -- funnel, forest plot, heatmap,
// queue bars, tape strip, storage arc -- is hand-drawn SVG in components.mjs and the surface
// modules (spec §5). uPlot draws to a canvas, so a Study lane of a couple of thousand points
// costs one path, not a node per point.

import { el } from "./components.mjs";

const CSS = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

//: uPlot arrives as a classic script before this module, so it is a global rather than an import.
//: Read through a function, never captured at import time, and a missing global degrades to a
//: line of text instead of a blank surface and a console error.
function lib() {
  return typeof globalThis.uPlot === "function" ? globalThis.uPlot : null;
}

function unavailable(node, what) {
  node.replaceChildren(el("div", { class: "n", text: `${what} could not be drawn here.` }));
  return null;
}

//: uPlot wants seconds since the epoch; every payload timestamp is an ISO-8601 string.
function stamps(points) {
  return points.map((p) => Date.parse(p[0]) / 1000);
}

// Width follows the container; height is whatever the caller asked for and never changes, so
// observing the container cannot feed its own resize back through setSize.
function observe(chart, node) {
  if (typeof ResizeObserver !== "function") return chart;
  let last = node.clientWidth;
  const observer = new ResizeObserver(() => {
    // A surface re-renders by replacing its root's children, which orphans this node. The
    // detach itself fires a resize, so this is where a long session stops accumulating dead
    // charts and their observers.
    if (!node.isConnected) { observer.disconnect(); chart.destroy(); return; }
    const width = node.clientWidth;
    if (!width || width === last) return;
    last = width;
    chart.setSize({ width, height: chart.height });
  });
  observer.observe(node);
  return chart;
}

function seriesColour(index) {
  return CSS(`--variant-${(index % 7) + 1}`) || CSS("--accent");
}

//: Shared by every chart here: the grid and axis colours come from the token set, so a theme
//: toggle and a redraw agree, and the cursor never shows a value the surface has not named.
function axisStyle() {
  return { stroke: CSS("--ink-muted"), grid: { stroke: CSS("--rule"), width: 1 },
           ticks: { stroke: CSS("--rule"), width: 1 },
           font: `11px ${CSS("--font-text") || "sans-serif"}` };
}

export function sparkline(node, points, options) {
  const uplot = lib();
  if (!uplot) return unavailable(node, "This sparkline");
  const series = points || [];
  if (series.length === 0) return unavailable(node, "This sparkline");
  const chart = new uplot({
    width: node.clientWidth || 160, height: (options && options.height) || 28,
    cursor: { show: false }, legend: { show: false },
    padding: [2, 1, 2, 1],
    axes: [{ show: false }, { show: false }],
    scales: { x: { time: true } },
    series: [{}, { stroke: (options && options.stroke) || CSS("--accent"), width: 1.5,
                   points: { show: false } }],
  }, [stamps(series), series.map((p) => p[1])], node);
  return observe(chart, node);
}

export function equityCurves(node, lanes, options) {
  // Two lines per variant: realized cash, and cash plus mark-to-market. The marked line is drawn
  // grey when coverage fell below the honest threshold, and is named beside its mark -- colour is
  // never the only signal (ruling B-(d), spec §5).
  const uplot = lib();
  if (!uplot) return unavailable(node, "The equity curves");
  const rows = (lanes || []).filter((lane) => lane && (lane.points || []).length > 0);
  if (rows.length === 0) return unavailable(node, "The equity curves");
  const data = [stamps(rows[0].points)];
  const series = [{}];
  rows.forEach((lane, index) => {
    data.push(lane.points.map((p) => p[1]));
    series.push({ label: `${lane.variant} cash`, stroke: seriesColour(index), width: 1.5,
                  points: { show: false } });
    data.push(lane.points.map((p) => p[2]));
    series.push({ label: `${lane.variant} cash + open`,
                  stroke: lane.mtm_grey ? CSS("--ink-muted") : seriesColour(index),
                  dash: [4, 3], width: 1, points: { show: false } });
  });
  const chart = new uplot({
    width: node.clientWidth || 640, height: (options && options.height) || 220,
    legend: { show: true }, scales: { x: { time: true } },
    axes: [axisStyle(), axisStyle()], series,
  }, data, node);
  return observe(chart, node);
}

export function legProbHistory(node, points) {
  const uplot = lib();
  if (!uplot) return unavailable(node, "This leg's history");
  const series = points || [];
  if (series.length === 0) return unavailable(node, "This leg's history");
  const chart = new uplot({
    width: node.clientWidth || 240, height: 40, cursor: { show: false },
    legend: { show: false }, padding: [2, 1, 2, 1],
    axes: [{ show: false }, { show: false }],
    scales: { x: { time: true }, y: { range: [0, 1] } },
    series: [{}, { label: "sharps say", stroke: CSS("--fun"), width: 1.5,
                   points: { show: false } }],
  }, [stamps(series), series.map((p) => p[1])], node);
  return observe(chart, node);
}
