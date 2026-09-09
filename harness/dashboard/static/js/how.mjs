// How it works: a static page, linked from the header on every surface. `how.html` is fixed
// text, not markup -- it is fetched once (through api.mjs, the one module that talks to the
// server) and split into headings and paragraphs by plain string rules, never by an HTML
// parser. Every piece still enters the DOM through `el`'s `text` attribute, exactly like every
// other surface (the DOM rule applies here too, even though this file is ours). The glossary at
// the foot is built straight from `glossary.json`, so there is one source for every definition
// on every surface.

import { loadGlossary, loadHow } from "./api.mjs";
import { el } from "./components.mjs";

// How it works carries no plain/technical pairs of its own: it is the page that already defines
// every technical name any other surface shows, in full, at the foot.
export const LABELS = [];

function parseHow(raw) {
  const blocks = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ tag: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  for (const rawLine of (raw || "").split("\n")) {
    const line = rawLine.trim();
    if (line === "") { flush(); continue; }
    if (line.startsWith("### ")) { flush(); blocks.push({ tag: "h3", text: line.slice(4) }); }
    else if (line.startsWith("## ")) { flush(); blocks.push({ tag: "h2", text: line.slice(3) }); }
    else if (line.startsWith("# ")) { flush(); blocks.push({ tag: "h1", text: line.slice(2) }); }
    else paragraph.push(line);
  }
  flush();
  return blocks;
}

function glossaryEntry(term, entry) {
  return el("div", { class: "card" },
    el("h3", {}, entry.plain || term, el("span", { class: "technical" }, ` · ${term}`)),
    el("p", { class: "lead", text: entry.what || "" }),
    entry.why ? el("p", { class: "n", text: entry.why }) : null);
}

export function render(root) {
  Promise.all([loadHow(), loadGlossary()]).then(([raw, glossary]) => {
    const blocks = parseHow(raw).map((block) => el(block.tag, { text: block.text }));
    const terms = Object.keys(glossary).sort((a, b) => a.localeCompare(b));
    const entries = terms.map((term) => glossaryEntry(term, glossary[term]));
    root.replaceChildren(...blocks, ...entries);
  });
}
