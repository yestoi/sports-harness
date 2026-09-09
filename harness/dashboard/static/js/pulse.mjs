// Pulse: is it fine? And when it is not, which named rule fired?
// Spec §2.1's six blocks in reading order: the status word and the fired rules, the 24 h tape
// continuity strip, the vitals row, the storage arc, the invariant wall, and the last ten
// operator events -- plus the build tile, which shows the recorder's sha, the executor's
// version and the serving build side by side, because build_sha_drift can only see the first
// of the three (ruling B-(c)).
//
// Nothing here is a control and nothing here is money: Pulse is about the machine, not the edge.

import { el, fmtAge, glossaryTerm, sentences, statTile, statusWord, storageArc, table,
         tapeStrip } from "./components.mjs";
import { sparkline } from "./charts.mjs";

export const LABELS = [
  { plain: "Is it fine?", technical: "dashboard snapshot" },
  { plain: "How fresh is the recorder?", technical: "STALE_AFTER_S" },
  { plain: "Is the executor's loop alive?", technical: "exec_heartbeat" },
  { plain: "Last message from the exchange", technical: "ws_last_event_at" },
  { plain: "Gaps in the recorded stream", technical: "ws.gaps" },
  { plain: "The stop button", technical: "kill switch" },
  { plain: "How full is the database?", technical: "db.size_gb" },
  { plain: "Free space on the disk", technical: "host.disk_free_gb" },
  { plain: "The twelve-check sweep", technical: "check_results" },
  { plain: "Credits left this month", technical: "runs.odds_remaining" },
  { plain: "This page's data, computed N s ago", technical: "dashboard snapshot" },
];

const LEVEL_CLASS = { broken: "bad", watch: "warn", fine: "good", not_evaluated: "grey" };

//: `table()` builds one `<tr>`/`<td>` per row from an array of cell values; a row helper must
//: hand back that array, never a pre-built `<tr>` of its own, or the cell winds up nested a
//: level too deep and the columns stop lining up with the header.
function ruleRow(rule, reading) {
  return [
    el("span", {}, el("span", { class: `swatch ${LEVEL_CLASS[rule.level] || "grey"}` }),
       el("span", { text: rule.name })),
    rule.value === null ? "--" : rule.value,
    rule.threshold === null ? "--" : rule.threshold,
    reading || "",
  ];
}

function statusCard(payload) {
  const status = payload.status || {};
  const readings = payload.readings?.rules || [];
  const byName = {};
  for (const line of readings) byName[line.split(":")[0]] = line;
  const card = el("div", { class: "card" },
    statusWord(status.status),
    sentences(payload.sentences?.status));
  const fired = [...(status.rules || []), ...(status.not_evaluated || [])];
  if (fired.length) {
    card.appendChild(table(["rule", "value", "threshold", "what it means"],
                           fired.map((rule) => ruleRow(rule, byName[rule.name])),
                           { label: "Fired rules" }));
  }
  return card;
}

function tapeCard(payload) {
  const tape = payload.tape || {};
  return el("div", { class: "card" },
    el("h3", {}, "Was the tape continuous?",
       el("span", { class: "technical" }, " · "), glossaryTerm("tape", "tape")),
    sentences(payload.sentences?.tape),
    tape.error ? el("div", { class: "grey", text: "unavailable" }) : tapeStrip(tape));
}

function vitalsCard(payload) {
  const vitals = payload.vitals || {};
  const grid = el("div", { class: "grid" });
  for (const tile of vitals.tiles || []) {
    const points = (vitals.sparklines || {})[tile.technical] || [];
    const holder = el("div", { class: "spark" });
    const node = statTile({ label: tile.label, technical: tile.technical,
                            value: tile.unit === "s" ? fmtAge(tile.value) : tile.value,
                            threshold: tile.threshold, spark: holder });
    grid.appendChild(node);
    if (points.length) requestAnimationFrame(() => sparkline(holder, points));
  }
  return el("div", { class: "card" }, el("h3", { text: "Vitals" }), grid);
}

function storageCard(payload) {
  const storage = payload.storage || {};
  const grid = el("div", { class: "grid" }, storageArc(storage),
    statTile({ label: "Free space on the disk", technical: "host.disk_free_gb",
               value: storage.disk_free_gb, threshold: storage.disk_min_fraction }),
    statTile({ label: "Days until the ceiling", technical: "db.growth_gb_per_day",
               value: storage.days_to_ceiling, threshold: null }));
  return el("div", { class: "card" }, el("h3", { text: "Storage" }), grid);
}

function invariantWall(payload) {
  const wall = payload.invariants || {};
  const tiles = (wall.tiles || []).map((tile) => {
    const cls = tile.status === "pass" ? "good" : tile.status === "fail" ? "bad" : "warn";
    // Three tile states, not two: a `skip` is a check that timed out or raised, and a wall
    // that drew it green would be reporting on a measurement nobody took (ruling B-I1).
    const node = el("button", { class: `tile ${cls}`, type: "button",
                                "aria-label": `${tile.check_name}: ${tile.status}` },
      el("span", { class: "lbl", text: tile.check_name }),
      el("span", { class: "val", text: tile.status }));
    if (tile.detail) node.appendChild(el("span", { class: "meta", text: tile.detail }));
    if (tile.threshold !== null && tile.threshold !== undefined) {
      node.appendChild(el("span", { class: "meta", text: String(tile.threshold) }));
    }
    return node;
  });
  return el("div", { class: "card" },
    el("h3", {}, "Did every invariant hold?",
       el("span", { class: "technical" }, " · check_results")),
    el("div", { class: "meta",
                text: `${wall.n_pass ?? 0} passed, ${wall.n_fail ?? 0} failed, ` +
                      `${wall.n_skip ?? 0} could not run` }),
    el("div", { class: "grid" }, tiles));
}

function buildCard(payload) {
  const build = payload.build || {};
  return el("div", { class: "card" }, el("h3", { text: "Which build is running?" }),
    el("div", { class: "grid" },
      statTile({ label: "Recorder", technical: "runs.build_sha",
                 value: build.recorder_build_sha, threshold: null }),
      statTile({ label: "Executor", technical: "exec_heartbeat.executor_version",
                 value: build.executor_version, threshold: null }),
      statTile({ label: "This page", technical: "build_sha",
                 value: build.serving_build_sha, threshold: null })));
}

function snapshotsCard(payload) {
  const rows = (payload.snapshots || []).map((row) =>
    [row.name, fmtAge(row.age_s), `${row.cadence_s} s`, `${row.elapsed_ms} ms`,
     row.error || ""]);
  return el("div", { class: "card" },
    el("h3", {}, "How fresh is each page's data?",
       el("span", { class: "technical" }, " · "),
       glossaryTerm("dashboard snapshot", "snapshot")),
    table(["surface", "age", "cadence", "build time", "error"], rows,
          { label: "Snapshot ages" }));
}

function eventsCard(payload) {
  const rows = (payload.operator_events || []).map((event) =>
    [fmtAge((Date.now() - Date.parse(event.ts)) / 1000), event.kind, event.summary]);
  return el("div", { class: "card" }, el("h3", { text: "Recent operator events" }),
            table(["when", "kind", "what"], rows, { label: "Operator events" }));
}

export function render(root, payload) {
  root.replaceChildren(
    statusCard(payload), tapeCard(payload), vitalsCard(payload), storageCard(payload),
    invariantWall(payload), buildCard(payload), snapshotsCard(payload), eventsCard(payload));
}
