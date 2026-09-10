// Every node in this application is built here or by a surface module using `el` and `svg`.
// Nothing sets markup: payload strings enter through textContent, structure through
// createElement. That makes the client safe independent of the server's sanitizer, which is the
// boundary that should hold (ruling A-I7).

import { loadGlossary } from "./api.mjs";

const SVG_NS = "http://www.w3.org/2000/svg";

export function text(value) {
  return document.createTextNode(value === null || value === undefined ? "--" : String(value));
}

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.appendChild(text(value));
    else if (key === "hidden") node.hidden = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.appendChild(text(value));
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(child instanceof Node ? child : text(child));
  }
}

// Every hand-drawn figure carries role="img" and a <title> repeating its sentence, so a screen
// reader gets the same statement a sighted reader gets (ruling A-I13). The first argument is the
// sentence, or `{title, ...attrs}` when the figure needs a viewBox of its own.
export function figure(titleText, ...children) {
  const spec = typeof titleText === "string" ? { title: titleText } : (titleText || {});
  const { title, ...attrs } = spec;
  const node = svg("svg", { role: "img", class: "figure", ...attrs });
  node.appendChild(svg("title", {}, text(title === undefined ? "" : title)));
  append(node, children);
  return node;
}

export function fmtAge(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "--";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

// Two-level labels everywhere: the plain question first, the technical name beside it in small
// muted type, so the surface can be cross-referenced with the reports (spec §1.2).
export function label(plain, technical) {
  // A non-empty technical name is routed through `glossaryTerm` here, once, rather than every
  // caller wrapping its own `label()` calls in one: this is what gives every two-level label a
  // tap target, not only the handful of calls a surface wrote `glossaryTerm` around by hand
  // (spec §1.2, review-T18-notes.md I9).
  return el("span", { class: "lbl" }, plain,
            technical ? el("span", { class: "technical tech" }, " · ",
                           glossaryTerm(technical, technical))
                      : null);
}

export function sentences(lines) {
  return el("ul", { class: "sentences" }, (lines || []).map((line) => el("li", { text: line })));
}

//: The three words the status rule can produce, and the class each maps to. Anything else -- a
//: surface with no snapshot yet, a payload whose status section failed -- is `unknown`, which is
//: grey and says so, never a green FINE the machine did not earn.
function statusLevel(word) {
  if (word === "BROKEN") return "broken";
  if (word === "WATCH") return "watch";
  if (word === "FINE") return "fine";
  return "unknown";
}

export function statusWord(word, options) {
  const level = statusLevel(word);
  const compact = options && options.compact ? " compact" : "";
  return el("div", { class: `status-word ${level}${compact}` },
            el("span", { class: `dot ${level}` }),
            el("span", { text: word || "--" }));
}

export function statTile(spec) {
  const value = el("span", { class: "val num", text: spec.value });
  if (spec.unit) value.appendChild(el("small", { text: spec.unit }));
  const tile = el("div", { class: `tile${spec.level ? ` ${spec.level}` : ""}` },
                  label(spec.label, spec.technical),
                  el("div", { class: "row spread" }, value, spec.spark || null));
  if (spec.threshold !== null && spec.threshold !== undefined) {
    tile.appendChild(el("div", { class: "n", text: `threshold ${spec.threshold}` }));
  }
  return tile;
}

// The one reusable interval mark, used everywhere an estimate appears (spec §5). Below the
// report's grey line it draws no point at all: an estimate nobody should read is not drawn.
export function intervalMark(cell) {
  const flags = (cell && cell.flags) || {};
  const wrap = el("span", { class: "interval" });
  if (!cell || flags.greyed) {
    wrap.appendChild(el("span", { class: "grey", text: "too few games" }));
  } else {
    wrap.appendChild(el("span", { class: "num", text: cell.text === undefined ? "--" : cell.text }));
  }
  const clusters = cell && cell.n_clusters !== undefined && cell.n_clusters !== null
    ? cell.n_clusters : "--";
  wrap.appendChild(el("span", { class: "n", text: `n=${clusters} games` }));
  if (flags.flagged) wrap.appendChild(el("span", { class: "flag", text: "flagged" }));
  return wrap;
}

export function queueBar(spec) {
  const ahead = Number(spec.queue_ahead_at_place || 0);
  const left = Number(spec.queue_remaining || 0);
  const done = ahead > 0 ? Math.max(0, Math.min(1, 1 - left / ahead)) : 0;
  const bar = el("div", { class: "track", role: "img",
                          "aria-label": `${left} of ${ahead} contracts still ahead of us` });
  bar.appendChild(el("div", { class: "fill", style: `width:${(done * 100).toFixed(1)}%` }));
  return bar;
}

//: One bucket every 3 units wide with a 0.6 gap, so a 48-bucket strip is 144 units across and the
//: viewBox scales to whatever width the row gives it.
const TAPE_STEP = 3;
const TAPE_HEIGHT = 10;

export function tapeStrip(spec) {
  const rows = (spec.sources || []).map((source) => {
    const buckets = source.buckets || [];
    const max = Math.max(1, ...buckets);
    const cells = buckets.map((value, index) =>
      svg("rect", { x: (index * TAPE_STEP).toFixed(1), y: 0, width: TAPE_STEP - 0.6,
                    height: TAPE_HEIGHT, rx: 1,
                    class: value > 0 ? "tape-on" : "tape-off",
                    opacity: value > 0 ? (0.35 + 0.65 * (value / max)).toFixed(2) : "1" }));
    const gaps = source.gaps === undefined || source.gaps === null ? "--" : source.gaps;
    const strip = figure({ title: `${source.source}: ${gaps} recorded gaps in the window`,
                           viewBox: `0 0 ${Math.max(1, buckets.length) * TAPE_STEP} ${TAPE_HEIGHT}`,
                           preserveAspectRatio: "none" }, ...cells);
    return el("div", { class: "tape-row" },
              el("span", { class: "lbl", text: source.source }), strip);
  });
  return el("div", { class: "tape" }, rows);
}

const ARC_WIDTH = 120;
const ARC_HEIGHT = 8;

export function storageArc(spec) {
  const share = spec.share === null || spec.share === undefined ? 0 : Number(spec.share);
  const filled = (ARC_WIDTH * Math.max(0, Math.min(1, share))).toFixed(1);
  const arc = figure({ title: `Database at ${(share * 100).toFixed(0)} % of its ceiling`,
                       viewBox: `0 0 ${ARC_WIDTH} ${ARC_HEIGHT}`, preserveAspectRatio: "none" },
                     svg("rect", { x: 0, y: 0, width: ARC_WIDTH, height: ARC_HEIGHT, rx: 4,
                                   class: "arc-track" }),
                     svg("rect", { x: 0, y: 0, width: filled, height: ARC_HEIGHT, rx: 4,
                                   class: "arc-fill" }));
  const size = spec.size_gb === undefined || spec.size_gb === null ? "--" : spec.size_gb;
  const budget = spec.budget_gb === undefined || spec.budget_gb === null ? "--" : spec.budget_gb;
  return el("div", { class: "tile" }, label("How full is the database?", "db.size_gb"), arc,
            el("div", { class: "n num", text: `${size} of ${budget} GB` }));
}

export function table(columns, rows, options) {
  const head = el("tr", {}, columns.map((c) =>
    el("th", { class: typeof c === "object" && c && c.num ? "num" : null },
       c instanceof Node ? c : (typeof c === "object" && c ? text(c.text) : text(c)))));
  const body = rows.map((row) => el("tr", {}, row.map((value) =>
    el("td", { class: typeof value === "number" ? "num" : null },
       value instanceof Node ? value : text(value)))));
  const node = el("table", {}, el("thead", {}, head), el("tbody", {}, body));
  // Wide tables scroll inside their own container; the page body never scrolls sideways.
  // A scrollable region needs a name and a tab stop, or a keyboard user cannot reach the
  // columns that are off screen.
  return el("div", { class: "scroll-x",
                     role: options && options.label ? "region" : null,
                     tabindex: options && options.label ? "0" : null,
                     "aria-label": options ? options.label : null }, node);
}

// A <button>, not a span: it opens on tap, click and keyboard focus alike (ruling A-I13). Hover
// is a bonus on top. The returned node is the wrapper the panel is positioned against.
export function glossaryTerm(term, shown) {
  const button = el("button", { class: "term", type: "button",
                                "aria-expanded": "false", text: shown || term });
  const wrap = el("span", { class: "term-wrap" }, button);
  let panel = null;
  let pinned = false;   // a click or a keyboard focus keeps it open; hover alone does not
  let wanted = false;   // the glossary load is async, so a close during it must win

  const hide = () => {
    if (panel) { panel.remove(); panel = null; }
    button.setAttribute("aria-expanded", "false");
  };
  const close = () => { if (!pinned) { wanted = false; hide(); } };
  const open = async () => {
    if (wanted) return;
    wanted = true;
    const entry = (await loadGlossary())[term] || {};
    if (!wanted || panel) return;
    panel = el("span", { class: "term-def", role: "tooltip" },
               el("b", { text: entry.plain || term }),
               el("div", { text: entry.what || "No definition yet." }),
               entry.why ? el("div", { class: "n", text: entry.why }) : null);
    wrap.appendChild(panel);
    button.setAttribute("aria-expanded", "true");
  };

  button.addEventListener("click", () => {
    if (pinned) { pinned = false; wanted = false; hide(); }
    else { pinned = true; open(); }
  });
  // Only a keyboard focus opens it; a mouse focus would fight the click above.
  button.addEventListener("focus", () => {
    if (button.matches(":focus-visible")) { pinned = true; open(); }
  });
  button.addEventListener("blur", () => { pinned = false; wanted = false; hide(); });
  button.addEventListener("mouseenter", open);
  button.addEventListener("mouseleave", close);
  return wrap;
}
