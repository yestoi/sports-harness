// Gate: the go-live gate as evidence, in the shape it will be read in October.
// Spec §2.4. Everything here is stored: the criteria the evaluation was made against, the
// values it measured, the hash of the definitions, the date. Nothing is recomputed and nothing
// is projected.

import { el, glossaryTerm, sentences, table } from "./components.mjs";
import { sparkline } from "./charts.mjs";

export const LABELS = [
  { plain: "The twelve tests for going live", technical: "gate criteria" },
  { plain: "The one being judged", technical: "gate variant" },
  { plain: "The pre-registered original", technical: "primary" },
];

const STATUS_CLASS = { passed: "good", failed: "bad", insufficient: "warn" };

function verdictCard(payload) {
  const verdict = payload.verdict || {};
  const passing = verdict.passing === true;
  const known = "passing" in verdict;
  const word = known ? (passing ? "PASSING" : "NOT PASSING") : "--";
  // `app.css` defines `.status-word.watch`/`.broken`/`.unknown`; there is no `.fine` because the
  // base `.status-word` rule already is the fine/passing colour, so a passing, known verdict
  // carries no modifier class at all rather than a name that would match nothing.
  return el("div", { class: "card" },
    el("h3", {}, "Is the gate variant passing?",
       el("span", { class: "technical" }, " · "),
       glossaryTerm("gate criteria", "gate criteria")),
    sentences(payload.sentences?.verdict),
    el("div", { class: `status-word${known && !passing ? " broken" : ""}` +
                        `${known ? "" : " unknown"}` },
       el("span", { text: word })),
    el("div", { class: "row" },
      el("span", { class: "n", text: `${verdict.n_pass ?? "--"} pass` }),
      el("span", { class: "n", text: `${verdict.n_fail ?? "--"} fail` }),
      el("span", { class: "n", text: `${verdict.n_insufficient ?? "--"} insufficient` }),
      el("span", { class: "n", text: `of ${verdict.n_total ?? "--"}` })),
    el("div", { class: "row" },
      el("span", { class: "n", text: `evaluated ${verdict.evaluated_at || "--"}` }),
      el("span", { class: "n", text: `criteria hash ${verdict.criteria_hash || "--"}` }),
      el("span", { class: "n", text: `variant: ${verdict.variant || "--"}` })));
}

//: `table()` builds its own `<tr>`/`<td>` from an array of cell values, so a row helper hands
//: back that array -- never a pre-built `<tr>` -- or the row ends up nested a level too deep and
//: off the header's columns.
function criterionRow(row, reading, history) {
  const cls = STATUS_CLASS[row.status] || "grey";
  const holder = el("div", { class: "spark" });
  if (history && history.length) requestAnimationFrame(() => sparkline(holder, history.map(
    (h) => [h.evaluated_at, h.value])));
  return [
    el("span", {}, el("span", { class: `swatch ${cls}` }), el("span", { text: row.name })),
    row.definition || "--",
    row.threshold === null || row.threshold === undefined ? "--" : row.threshold,
    row.value === null || row.value === undefined ? "--" : row.value,
    row.n === null || row.n === undefined ? "--" : row.n,
    el("span", { class: `badge ${cls}`, text: row.status || "--" }),
    reading || "",
    holder,
  ];
}

function criteriaTable(payload, criteria, readings) {
  const history = payload.history || {};
  const rows = criteria.map((row, i) => criterionRow(row, readings[i], history[row.name]));
  return table(["criterion", "definition", "threshold", "value", "n", "status", "reading",
               "history"], rows, { label: "Gate criteria" });
}

function criteriaSection(payload) {
  const criteria = payload.criteria || [];
  const readings = payload.readings?.criteria || [];
  return el("div", { class: "card" }, el("h3", { text: "The twelve criteria" }),
    sentences(payload.sentences?.criteria),
    criteria.length ? criteriaTable(payload, criteria, readings)
                    : el("p", { class: "grey", text: "no criteria stored yet" }));
}

function variantBlock(title, note, criteria) {
  return el("div", { class: "card" },
    el("h3", {}, title, note ? el("span", { class: "badge dim", text: note }) : null),
    criteria.length
      ? table(["criterion", "definition", "threshold", "value", "n", "status"],
              criteria.map((row) => [row.name, row.definition || "--",
                row.threshold === null || row.threshold === undefined ? "--" : String(row.threshold),
                row.value === null || row.value === undefined ? "--" : String(row.value),
                row.n_clusters === null || row.n_clusters === undefined
                  ? "--" : String(row.n_clusters),
                row.status || "--"]),
              { label: title })
      : el("p", { class: "grey", text: "no rows stored" }));
}

function variantsSection(payload) {
  const variants = payload.variants || [];
  const gate = variants.find((v) => v.gate_variant);
  // Every stored variant beside the gate variant, not only the first: `gate_reports` can carry
  // more than one reported-not-gated row, and dropping the rest would be a silent omission.
  const others = variants.filter((v) => !v.gate_variant);
  const blocks = [];
  if (gate) blocks.push(variantBlock(`Gate variant: ${gate.name}`, null, gate.criteria || []));
  for (const other of others) {
    blocks.push(variantBlock(other.name, other.note, other.criteria || []));
  }
  return el("div", {}, blocks);
}

function standingFoot(payload) {
  return el("div", { class: "card" }, el("p", { class: "lead", text: payload.standing_text || "" }));
}

export function render(root, payload, _envelope) {
  root.replaceChildren(
    verdictCard(payload), criteriaSection(payload), variantsSection(payload),
    standingFoot(payload));
}
