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
  // The six technical names the build tile and the vitals row actually render in small type
  // (review-T18-notes.md I3): the recorder's own build sha, the day-over-day growth rate beside
  // the days-to-ceiling figure, and the four `vitals.tiles` names the payload supplies.
  { plain: "Recorder build", technical: "runs.build_sha" },
  { plain: "Days until the ceiling", technical: "db.growth_gb_per_day" },
  { plain: "Executor heartbeat", technical: "exec_heartbeat.last_loop_at" },
  { plain: "Loop time", technical: "exec.p95_loop_ms" },
  { plain: "Loops skipped", technical: "exec_heartbeat.loops_skipped" },
  { plain: "Markets with a book we distrust", technical: "book_dirty_markets" },
  { plain: "Memory free on the NAS", technical: "host.mem_available_mb" },
  // Fix round 2, I5: the research card's two counts -- the spend and the veto rate are already
  // read through the sentences below, which is why they carry no `technical` name of their own.
  { plain: "RFQ quotes in the last 24 hours", technical: "research.rfq_quotes_24h" },
  { plain: "Report annotations written this week", technical: "research.annotations_week" },
];

const LEVEL_CLASS = { broken: "bad", watch: "warn", fine: "good", not_evaluated: "grey" };

//: A guarded section (`snapshots/__init__.py`'s `section()`) writes `{"error": "<ClassName>"}` on
//: a timeout, which is a normal outcome under a 2 s statement timeout, not a rarity. `.map`/
//: `.filter` on that dict throws before `render` ever calls `replaceChildren`, which blanks the
//: whole surface rather than the one section that failed.
const listOf = (value) => (Array.isArray(value) ? value : []);
const sectionFailed = (value) => Boolean(value) && !Array.isArray(value) && value.error;

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
  // `status.all` carries every rule's value and threshold, fired or not -- on a healthy machine
  // `fired` is empty and, without this, no rule's value is visible anywhere on the page. Fired
  // rules stay in their own table above; everything else sits behind a disclosure so a fine
  // machine's Pulse is still a one-glance page.
  const firedNames = new Set(fired.map((rule) => rule.name));
  const rest = (status.all || []).filter((rule) => !firedNames.has(rule.name));
  if (rest.length) {
    card.appendChild(el("details", {},
      el("summary", { text: "Every rule, including the ones that are fine" }),
      table(["rule", "value", "threshold", "what it means"],
            rest.map((rule) => ruleRow(rule, byName[rule.name])),
            { label: "Every rule" })));
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
    // `tile.technical` is the glossary key (checked by `test_dashboard_surfaces.py`'s `_labels`)
    // and does double duty as nothing else: the sparkline series is keyed by `tile.metric`, the
    // `metric_samples` name the builder writes separately (`pulse.py`'s `_vitals`), which is
    // `None` for a tile with no collected series.
    const points = (vitals.sparklines || {})[tile.metric] || [];
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
  const section = payload.storage;
  // A number the machine did not earn is never drawn as one: a failed read and a read that ran
  // before housekeeping ever counted a table (`storage.measured === false`, `pulse.py`'s
  // `"measured": bool(counts)`) both say "not evaluated" rather than an arc clamped to 0 %.
  if (sectionFailed(section) || section?.measured === false) {
    return el("div", { class: "card" }, el("h3", { text: "Storage" }),
      el("p", { class: "grey", text: "not evaluated" }));
  }
  const storage = section || {};
  const grid = el("div", { class: "grid" }, storageArc(storage),
    statTile({ label: "Free space on the disk", technical: "host.disk_free_gb",
               value: storage.disk_free_gb, threshold: storage.disk_min_fraction }),
    statTile({ label: "Days until the ceiling", technical: "db.growth_gb_per_day",
               value: storage.days_to_ceiling, threshold: null }),
    statTile({ label: "Memory free on the NAS", technical: "host.mem_available_mb",
               value: storage.mem_available_mb, threshold: null }));
  const tables = Object.entries(storage.tables_gb || {})
    .sort((a, b) => b[1] - a[1]).slice(0, 6);
  return el("div", { class: "card" }, el("h3", { text: "Storage" }), grid,
    tables.length ? el("details", {},
      el("summary", { text: "The six largest tables" }),
      table(["table", "GB"], tables, { label: "Largest tables" })) : null);
}

function invariantWall(payload) {
  const section = payload.invariants;
  if (sectionFailed(section)) {
    return el("div", { class: "card" },
      el("h3", {}, "Did every invariant hold?",
         el("span", { class: "technical" }, " · check_results")),
      el("div", { class: "grey", text: "unavailable" }));
  }
  const wall = section || {};
  const tiles = (wall.tiles || []).map((tile) => {
    const cls = tile.status === "pass" ? "good" : tile.status === "fail" ? "bad" : "warn";
    // Three tile states, not two: a `skip` is a check that timed out or raised, and a wall
    // that drew it green would be reporting on a measurement nobody took (ruling B-I1).
    // A `<div>`, not a button: spec §2.1's "tap for value and SQL name" wants the value on
    // screen, not behind a handler-less control that only ever looked tappable.
    const node = el("div", { class: `tile ${cls}` },
      el("span", { class: "lbl", text: tile.check_name }),
      el("div", { class: "row spread" },
        el("span", { class: "val", text: tile.status }),
        tile.value !== null && tile.value !== undefined
          ? el("span", { class: "val num", text: String(tile.value) }) : null));
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
  const section = payload.snapshots;
  // `disabled` is the scheduler having stopped that builder because three builds in a row cost
  // more than `disabled_over_ms` (fix 31). It is not an error, so it does not belong in the
  // error column: the row's numbers are the last good ones and the age is growing on purpose.
  // Only a restart of app-serve starts it again, which is what the cell says.
  const rows = listOf(section).map((row) =>
    [row.name, fmtAge(row.age_s), `${row.cadence_s} s`, `${row.elapsed_ms} ms`,
     row.disabled ? `stopped over ${row.disabled_over_ms} ms · restart app-serve` : "",
     row.error || ""]);
  return el("div", { class: "card" },
    el("h3", {}, "How fresh is each page's data?",
       el("span", { class: "technical" }, " · "),
       glossaryTerm("dashboard snapshot", "snapshot")),
    sectionFailed(section) ? el("div", { class: "grey", text: "unavailable" })
      : table(["surface", "age", "cadence", "build time", "state", "error"], rows,
              { label: "Snapshot ages" }));
}

function eventsCard(payload) {
  const section = payload.operator_events;
  const rows = listOf(section).map((event) =>
    [fmtAge((Date.now() - Date.parse(event.ts)) / 1000), event.kind, event.summary]);
  return el("div", { class: "card" }, el("h3", { text: "Recent operator events" }),
            sectionFailed(section) ? el("div", { class: "grey", text: "unavailable" })
              : table(["when", "kind", "what"], rows, { label: "Operator events" }));
}

function researchCard(payload) {
  const section = payload.research;
  if (sectionFailed(section)) {
    return el("div", { class: "card" }, el("h3", { text: "Research spend and the veto" }),
      el("div", { class: "grey", text: "unavailable" }));
  }
  const research = section || {};
  // The day's spend against the daily cap, the live reservation, the week's spend against the
  // weekly cap, the dormant flag's wording, the veto rate and the decided count all come through
  // these two prose sentences (`research_reading`, `veto_reading`) rather than being recomposed
  // here; what is left to show as numbers is the two counts neither sentence carries.
  return el("div", { class: "card" }, el("h3", { text: "Research spend and the veto" }),
    sentences([research.sentence, research.veto_sentence].filter(Boolean)),
    el("div", { class: "grid" },
      statTile({ label: "RFQ quotes", technical: "research.rfq_quotes_24h",
                 value: research.rfq_quotes_24h, threshold: null }),
      statTile({ label: "Report annotations", technical: "research.annotations_week",
                 value: research.annotations_week, threshold: null })));
}

export function render(root, payload) {
  root.replaceChildren(
    statusCard(payload), tapeCard(payload), vitalsCard(payload), storageCard(payload),
    invariantWall(payload), buildCard(payload), snapshotsCard(payload), eventsCard(payload),
    researchCard(payload));
}
