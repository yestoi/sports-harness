// The only place the client talks to the server. Every poll carries If-None-Match, so a surface
// that has not changed costs one 304 rather than a payload (spec §4). A test asserts no other
// module calls `fetch`, so the ETag discipline cannot be bypassed by a surface.

//: Resolved against this module's own URL rather than the document's, so it is `/ui/glossary.json`
//: whether the page was opened as `/ui`, `/ui/` or `/ui/index.html`.
const GLOSSARY_URL = new URL("../glossary.json", import.meta.url).pathname;

export async function getSnapshot(name, etag) {
  const headers = etag ? { "If-None-Match": etag } : {};
  let response;
  try {
    response = await fetch(`/api/snap/${encodeURIComponent(name)}`, { headers });
  } catch (error) {
    // A dropped tunnel is not a broken surface: the caller keeps the payload it has and the
    // header's age keeps counting up, which is the honest reading.
    return { status: 0, etag, body: null };
  }
  if (response.status === 304) return { status: 304, etag, body: null };
  if (!response.ok) return { status: response.status, etag: null, body: null };
  return { status: 200, etag: response.headers.get("ETag"), body: await response.json() };
}

export async function listSnapshots() {
  try {
    const response = await fetch("/api/snap");
    return response.ok ? await response.json() : { snapshots: [] };
  } catch (error) {
    return { snapshots: [] };
  }
}

export async function servingBuild() {
  // `/ui/` is static, so the shell cannot know its own build from a file: it asks the process
  // that served it and compares that against the sha inside each payload (ruling A-I11).
  try {
    const response = await fetch("/healthz");
    if (!response.ok) return null;
    const body = await response.json();
    return body.build || null;
  } catch (error) {
    return null;
  }
}

let glossary = null;

export async function loadGlossary() {
  if (glossary === null) {
    try {
      const response = await fetch(GLOSSARY_URL);
      glossary = response.ok ? await response.json() : {};
    } catch (error) {
      glossary = {};
    }
  }
  return glossary;
}
