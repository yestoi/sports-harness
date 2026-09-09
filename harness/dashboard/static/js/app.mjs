// The shell: a hash router over six views, one polling loop per surface at the cadence the
// payload names, the Pulse status word on every surface, the oldest-snapshot age, both build
// shas, and the theme toggle. Nothing here acts: this phase is read-only and the one control in
// the whole UI is the Legacy panel link in the header (global constraints).

import { getSnapshot, listSnapshots, servingBuild } from "./api.mjs";
import { el, fmtAge, statusWord, svg } from "./components.mjs";

const SURFACES = ["pulse", "floor", "study", "gate", "ticket", "how"];
//: The snapshot names that are rebuilt on a cadence, so an age over 3x means something is wrong.
//: A closed Study week's snapshot is days old by design and is never counted here.
const LIVE_NAMES = ["pulse", "floor", "gate", "ticket"];
const PULSE_POLL_MS = 30000;
const INDEX_POLL_MS = 60000;
const AGE_TICK_MS = 5000;
const DEFAULT_CADENCE_S = 30;
const FUN_BADGE = "FUN MONEY · $50/WEEK · PLACED BY HAND";

const modules = {};
const state = { surface: "pulse", timer: null, etags: {}, payloads: {}, rendered: {},
                studyName: null, serving: null,
                oldest: null, oldestAt: 0 };

export function registerSurface(name, module) { modules[name] = module; }

// --- theme: OS by default, a manual choice remembered in localStorage (spec §5) ---------------
// The same key the inline script in index.html reads before first paint; a test asserts both
// files name it.
const THEME_KEY = "harness.theme";

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

function storedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch (error) { return null; }
}

function initTheme() {
  applyTheme(storedTheme());
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const light = document.documentElement.getAttribute("data-theme") === "light"
      || (!document.documentElement.hasAttribute("data-theme")
          && window.matchMedia("(prefers-color-scheme: light)").matches);
    const next = light ? "dark" : "light";
    try { localStorage.setItem(THEME_KEY, next); } catch (error) { /* site data blocked */ }
    applyTheme(next);
  });
}

// --- the tabs: a tablist with arrow-key movement (ruling A-I13) -------------------------------
//: The phone bar's icons, decorative beside their labels (colour and shape are never the only
//: signal). Straight from the canvas artboards' tab bar.
const TAB_ICONS = {
  pulse: ["M3 12h4l3-8 4 16 3-8h4"],
  floor: ["M3 4h18v16H3z", "M3 10h18M9 10v10"],
  study: ["M4 20V10M10 20V4M16 20v-7M22 20H2"],
  gate: ["M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z", "M9 12l2 2 4-4"],
  ticket: ["M3 8h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z", "M9 8v11"],
};

function tabIcon(surface) {
  const paths = TAB_ICONS[surface] || [];
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" },
             paths.map((d) => svg("path", { d })));
}

function initTabs() {
  const bar = document.querySelector(".tabs");
  const phone = document.querySelector(".phone-tabs");
  const tabs = [...bar.querySelectorAll(".tab")];
  for (const tab of tabs) {
    const surface = tab.dataset.surface;
    tab.addEventListener("click", () => { location.hash = `#${surface}`; });
    // The phone bar is built from the same five buttons, so the two bars can never disagree
    // about which surface is selected.
    const twin = tab.cloneNode(true);
    twin.addEventListener("click", () => { location.hash = `#${surface}`; });
    twin.insertBefore(tabIcon(surface), twin.firstChild);
    phone.appendChild(twin);
  }
  for (const holder of [bar, phone]) {
    holder.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const own = [...holder.querySelectorAll(".tab")];
      const index = own.findIndex((t) => t.getAttribute("aria-selected") === "true");
      const next = own[(index + step + own.length) % own.length];
      location.hash = `#${next.dataset.surface}`;
      next.focus();
    });
  }
}

function markTabs(surface) {
  for (const tab of document.querySelectorAll(".tab")) {
    const on = tab.dataset.surface === surface;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.setAttribute("tabindex", on ? "0" : "-1");
  }
  const badge = document.getElementById("shell-badge");
  badge.textContent = surface === "ticket" ? FUN_BADGE : "PAPER";
  badge.className = surface === "ticket" ? "pill fun" : "pill";
}

// --- staleness, the oldest snapshot, and the two build shas -----------------------------------

//: The staleness flag is 2x the cadence for a warning and 3x for a break (spec §4). It is the
//: snapshot's own cadence, never a number restated here: a surface that rebuilds every ten
//: minutes and one that rebuilds every fifteen seconds are both fine at forty seconds old.
function level(ageS, cadenceS) {
  if (ageS === null || ageS === undefined || Number.isNaN(Number(ageS))) return "";
  const cadence = cadenceS || DEFAULT_CADENCE_S;
  if (ageS > 3 * cadence) return "broken";
  if (ageS > 2 * cadence) return "watch";
  return "";
}

function markAge(name, ageS, cadenceS) {
  const node = document.getElementById("shell-age");
  const flag = level(ageS, cadenceS);
  const known = ageS !== null && ageS !== undefined;
  node.textContent = known ? `snapshots ${fmtAge(ageS)}` : "snapshots --";
  node.className = `meta num${flag ? ` ${flag}` : ""}`;
  node.title = known
    ? `the oldest snapshot this page reads is ${name}, rebuilt every ${fmtAge(cadenceS)}`
    : "no snapshot has reported an age yet";
  const banner = document.getElementById("shell-stale");
  if (flag) {
    banner.textContent = `The ${name} snapshot is ${fmtAge(ageS)} old and is rebuilt every `
      + `${fmtAge(cadenceS)}. The numbers below are the last good ones, not the current ones.`;
    banner.className = `banner${flag === "broken" ? " broken" : ""}`;
    banner.hidden = false;
  } else {
    banner.textContent = "";
    banner.hidden = true;
  }
}

// The shell's own build and the payload's, side by side: a phone holding a cached module against
// a new payload shape is visible rather than quietly wrong (ruling A-I11). The serving sha is
// shown only when the two differ, so the header stays quiet in the ordinary case.
function markBuild(payload) {
  const shown = (payload && payload.build_sha) || "--";
  const build = document.getElementById("shell-build");
  const serving = document.getElementById("shell-serving");
  const drift = Boolean(state.serving) && shown !== "--" && state.serving !== shown;
  build.textContent = `build ${shown}`;
  build.className = drift ? "meta num broken" : "meta num";
  serving.textContent = drift ? `serving ${state.serving}` : "";
  serving.className = drift ? "meta num broken" : "meta num";
  serving.hidden = !drift;
}

function markStatus(pulsePayload) {
  const word = pulsePayload && pulsePayload.status ? pulsePayload.status.status : null;
  document.getElementById("shell-status")
          .replaceChildren(statusWord(word, { compact: true }));
}

// --- the snapshot index: the oldest live snapshot, and which Study week exists ----------------

//: The ISO week the browser is in, used only when the index has no `study:` row to name -- the
//: server is the authority on which weeks have a snapshot.
function isoWeekName(when) {
  const target = new Date(Date.UTC(when.getUTCFullYear(), when.getUTCMonth(), when.getUTCDate()));
  const day = (target.getUTCDay() + 6) % 7;
  target.setUTCDate(target.getUTCDate() - day + 3);
  const first = new Date(Date.UTC(target.getUTCFullYear(), 0, 4));
  const week = 1 + Math.round(
    ((target - first) / 86400000 - 3 + ((first.getUTCDay() + 6) % 7)) / 7);
  return `study:${target.getUTCFullYear()}-${week}`;
}

function newestStudy(rows) {
  const weeks = rows.filter((row) => String(row.name).startsWith("study:"))
                    .map((row) => row.name);
  if (weeks.length === 0) return null;
  // `study:<year>-<week>` with an unpadded week, so compare the two numbers rather than the text.
  const key = (name) => {
    const parts = name.slice("study:".length).split("-");
    return Number(parts[0]) * 100 + Number(parts[1]);
  };
  return weeks.reduce((best, name) => (key(name) > key(best) ? name : best));
}

// "Oldest" is measured in cadence multiples, not seconds: a Gate snapshot six minutes old on a
// ten-minute cadence is current, and a Pulse snapshot two minutes old on a thirty-second cadence
// is not. Ranking by raw age would have hidden the second behind the first, which is exactly the
// reading the age is in the header to prevent.
function oldestLive(rows, studyName) {
  const wanted = new Set(LIVE_NAMES.concat(studyName ? [studyName] : []));
  let worst = null;
  let worstRatio = -1;
  for (const row of rows) {
    if (!wanted.has(row.name)) continue;
    const ratio = Number(row.age_s) / (Number(row.cadence_s) || DEFAULT_CADENCE_S);
    if (!(ratio > worstRatio)) continue;
    worstRatio = ratio;
    worst = row;
  }
  return worst;
}

async function pollIndex() {
  const index = await listSnapshots();
  const rows = index.snapshots || [];
  const found = newestStudy(rows);
  const next = found || isoWeekName(new Date());
  if (next !== state.studyName) {
    state.studyName = next;
    if (state.surface === "study") await loop();
  }
  state.oldest = oldestLive(rows, state.studyName);
  state.oldestAt = Date.now();
  tickAge();
}

// The index is read once a minute, but the age it reported keeps growing between reads, and the
// 2x flag on a 30 s cadence would otherwise arrive up to a minute late. The local clock carries
// it forward; only the number the server gave is ever trusted as a starting point.
function tickAge() {
  const row = state.oldest;
  if (!row) { markAge("", null, null); return; }
  const drift = (Date.now() - state.oldestAt) / 1000;
  markAge(row.name, Number(row.age_s) + drift, Number(row.cadence_s));
}

// --- the polling loop -------------------------------------------------------------------------

function snapshotName(surface) {
  if (surface !== "study") return surface;
  return state.studyName || isoWeekName(new Date());
}

function missing(root, name) {
  root.replaceChildren(el("p", { class: "lead" },
    `No snapshot has been built for ${name} yet. `
    + "The scheduler builds one per surface on its own cadence; this page shows nothing rather "
    + "than a number it cannot stand behind."));
}

// Distinct from `missing`: the snapshot is there and the surface module is not, which is a
// deploy problem rather than a scheduler one and should not read as "no data".
function noModule(root, surface) {
  root.replaceChildren(el("p", { class: "lead" },
    `The ${surface} surface did not load in this browser. `
    + "Its snapshot is being built; reload the page, and if it persists the build serving this "
    + "page is incomplete."));
}

async function poll(surface, force) {
  const name = snapshotName(surface);
  const result = await getSnapshot(name, state.etags[name]);
  if (result.status === 200) {
    state.etags[name] = result.etag;
    state.payloads[name] = result.body;
  } else if (result.status === 404) {
    delete state.payloads[name];
    delete state.etags[name];
  }
  const root = document.getElementById("surface");
  const body = state.payloads[name];
  if (!body) {
    missing(root, name);
    state.rendered[name] = null;
    return PULSE_POLL_MS;
  }
  const cadenceS = body.cadence_s || (body.payload && body.payload.cadence_s) || DEFAULT_CADENCE_S;
  markBuild(body.payload);
  // Re-render only when the payload actually changed: a 304 that redrew the surface would throw
  // away every chart, scroll position and open glossary panel for nothing.
  const stamp = state.etags[name] || body.generated_at;
  if (force || state.rendered[name] !== stamp) {
    state.rendered[name] = stamp;
    const module = modules[surface];
    if (module && typeof module.render === "function") module.render(root, body.payload, body);
    else noModule(root, surface);
  }
  return cadenceS * 1000;
}

async function loop() {
  clearTimeout(state.timer);
  const surface = state.surface;
  let wait = PULSE_POLL_MS;
  try {
    wait = await poll(surface, state.rendered[snapshotName(surface)] === undefined);
  } catch (error) {
    // One bad render must not stop the clock: the next tick tries again and the header's age
    // keeps counting, which is what tells the reader the surface is not current.
    console.error("surface render failed", error);
  }
  if (state.surface === surface) state.timer = setTimeout(loop, wait);
}

async function pollPulse() {
  // The status word is in the header on every surface (spec §1), so the shell polls pulse
  // whatever is open -- through the same ETag path, so it is a 304 when nothing changed.
  const result = await getSnapshot("pulse", state.etags.pulse);
  if (result.status === 200) {
    state.etags.pulse = result.etag;
    state.payloads.pulse = result.body;
  }
  const body = state.payloads.pulse;
  markStatus(body ? body.payload : null);
}

async function show(surface) {
  state.surface = SURFACES.includes(surface) ? surface : "pulse";
  markTabs(state.surface);
  const root = document.getElementById("surface");
  root.replaceChildren();
  if (state.surface === "how") {
    clearTimeout(state.timer);
    const module = modules.how;
    if (module && typeof module.render === "function") module.render(root);
    else noModule(root, "How it works");
    return;
  }
  state.rendered = {};
  await loop();
}

async function boot() {
  initTheme();
  initTabs();
  state.serving = await servingBuild();
  for (const name of SURFACES) {
    try {
      registerSurface(name, await import(`./${name}.mjs`));
    } catch (error) {
      // A surface module that is missing or broken costs its own tab, never the shell: the other
      // four surfaces and the status word keep working.
      console.error(`surface module ${name} did not load`, error);
    }
  }
  window.addEventListener("hashchange", () => { show(location.hash.slice(1)); });
  await pollPulse();
  setInterval(pollPulse, PULSE_POLL_MS);
  await pollIndex();
  setInterval(pollIndex, INDEX_POLL_MS);
  setInterval(tickAge, AGE_TICK_MS);
  await show(location.hash.slice(1) || "pulse");
}

boot();
