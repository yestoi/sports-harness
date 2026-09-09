"""The static bundle's guarantees: the DOM rule, the byte budget, the pinned vendor hashes, no
external URL, the viewport meta, and the token set."""

import hashlib
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "harness" / "dashboard" / "static"

#: uPlot 1.6.32, fetched once from the pinned GitHub tag by the implementer (D6). Replace both
#: values only when the pinned version changes, and change the version in the same commit.
UPLOT_VERSION = "1.6.32"
UPLOT_SHA256 = {
    "uPlot.iife.min.js": "19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f",
    "uPlot.min.css": "df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04",
}

#: Spec §5's asset budget, uncompressed, vendor included.
ASSET_BUDGET_BYTES = 300 * 1024

FORBIDDEN_DOM = ("innerHTML", "outerHTML", "insertAdjacentHTML")

#: The one localStorage key the theme is remembered under. The shell sets it before first paint
#: in an inline script and the router reads it, so a drift between the two would show as a
#: theme flash on every load.
THEME_KEY = "harness.theme"


def _js_files():
    return sorted((STATIC / "js").glob("*.mjs"))


def _all_files():
    return [p for p in STATIC.rglob("*") if p.is_file()]


def test_no_module_ever_writes_markup():
    """The DOM rule (ruling A-I7 / B-(e)): every payload string enters through textContent and
    every node through createElement. With this, the front end is safe independent of the
    sanitizer, which is the boundary that should hold."""
    for path in _js_files():
        body = path.read_text()
        for word in FORBIDDEN_DOM:
            assert word not in body, f"{path.name} uses {word}"


def test_the_shell_writes_no_markup_either():
    body = (STATIC / "index.html").read_text()
    for word in FORBIDDEN_DOM:
        assert word not in body


def test_no_module_writes_to_the_document_stream():
    """`document.write` is the other way markup can enter, and it is never needed here."""
    for path in _js_files() + [STATIC / "index.html"]:
        assert "document.write" not in path.read_text(), path.name


#: Files this grep skips, and why. The uPlot LICENSE carries the project's own URL; the two
#: vendored uPlot files are pinned byte-for-byte by sha256, which is a stronger guarantee than a
#: grep; `how.html` is allowed exactly one URL, the design canvas, checked by its own test below.
URL_EXEMPT = {"uPlot.LICENSE", "uPlot.iife.min.js", "uPlot.min.css", "how.html"}
#: The SVG namespace is an identifier `createElementNS` requires, not a resource anything fetches.
SVG_NS = "http://www.w3.org/2000/svg"


def test_no_static_file_names_an_external_url():
    """No CDN, no font download: the NAS is viewed over a tunnel and the page must work with no
    internet. The artboards' Google Fonts link must not have survived the port."""
    for path in _all_files():
        if path.name in URL_EXEMPT:
            continue
        found = [u for u in re.findall(r"https?://[^\s\"')]+", path.read_text(errors="ignore"))
                 if u != SVG_NS]
        assert found == [], f"{path.name} names {found}"


def test_the_how_page_links_only_the_canvas():
    body = (STATIC / "how.html").read_text() if (STATIC / "how.html").exists() else ""
    for url in re.findall(r"https?://[^\s\"')]+", body):
        assert url.startswith("https://claude.ai/code/artifact/"), url


def test_the_asset_budget_holds():
    total = sum(p.stat().st_size for p in _all_files())
    assert total < ASSET_BUDGET_BYTES, f"static/ is {total} bytes, over {ASSET_BUDGET_BYTES}"


def test_the_vendored_uplot_matches_its_pinned_hashes():
    for name, expected in UPLOT_SHA256.items():
        path = STATIC / "vendor" / name
        assert path.is_file(), f"{name} was not vendored"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, f"{name} sha256 is {actual}"


def test_the_uplot_licence_travels_with_it():
    licence = (STATIC / "vendor" / "uPlot.LICENSE").read_text()
    assert "MIT" in licence and "Leon Sorokin" in licence


def test_the_vendored_uplot_is_the_pinned_version():
    """The version constant and the files move in one commit, so the banner in the dist file is
    read back rather than trusted."""
    body = (STATIC / "vendor" / "uPlot.iife.min.js").read_text()
    assert UPLOT_VERSION in body.splitlines()[0]


def test_the_shell_declares_the_viewport():
    """Without this the 390 px walker row measures a scaled desktop layout and passes for the
    wrong reason (ruling A-I13)."""
    body = (STATIC / "index.html").read_text()
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in body


def test_the_tabs_are_a_tablist_with_the_five_surfaces_and_the_legacy_link():
    body = (STATIC / "index.html").read_text()
    assert 'role="tablist"' in body
    for surface in ("pulse", "floor", "study", "gate", "ticket"):
        assert f'data-surface="{surface}"' in body
    assert 'href="/"' in body and "Legacy panel" in body


def test_the_shell_links_how_it_works_and_carries_the_paper_badge():
    body = (STATIC / "index.html").read_text()
    assert 'href="#how"' in body and "How it works" in body
    assert "PAPER" in body


def test_the_shell_shows_both_build_shas_and_the_oldest_snapshot_age():
    body = (STATIC / "index.html").read_text()
    for node_id in ("shell-build", "shell-serving", "shell-age", "theme-toggle"):
        assert f'id="{node_id}"' in body, node_id


def test_the_status_word_is_a_polite_live_region():
    assert 'aria-live="polite"' in (STATIC / "index.html").read_text()


def test_the_theme_key_is_one_string_in_both_places():
    assert THEME_KEY in (STATIC / "index.html").read_text()
    assert THEME_KEY in (STATIC / "js" / "app.mjs").read_text()


def test_the_token_set_is_one_set_with_two_values_each():
    """Spec §5: light and dark are one token set with two values, never two stylesheets."""
    css = (STATIC / "app.css").read_text()
    assert ":root" in css and '[data-theme="light"]' in css
    assert "prefers-color-scheme" in css
    assert "prefers-reduced-motion" in css
    for token in ("--ground", "--tile", "--card", "--raised", "--border", "--rule", "--track",
                  "--ink", "--ink-2", "--ink-muted", "--accent", "--good", "--warn", "--bad",
                  "--fun", "--slip-paper", "--slip-ink", "--slip-muted", "--hit", "--miss"):
        assert token in css, f"app.css has no {token}"
    for slot in range(1, 8):
        assert f"--variant-{slot}" in css


def test_the_dark_ground_and_the_accent_are_the_canvas_values():
    css = (STATIC / "app.css").read_text()
    assert "#0b0e13" in css and "#131820" in css and "#35c9d9" in css
    assert "#f4f3ef" in css and "#0f8e9c" in css


def test_the_display_stack_prefers_the_canvas_faces_with_system_fallbacks():
    """D5: no font file is vendored, so every face falls back to the system stack."""
    css = (STATIC / "app.css").read_text()
    assert '"Space Grotesk"' in css and '"IBM Plex Sans"' in css and '"IBM Plex Mono"' in css
    assert "system-ui" in css and "ui-monospace" in css


def test_no_font_file_is_vendored():
    """D5 again, from the other side: a face is preferred by name, never downloaded."""
    suffixes = {p.suffix.lower() for p in _all_files()}
    assert suffixes.isdisjoint({".woff", ".woff2", ".ttf", ".otf", ".eot"}), suffixes
    assert "@font-face" not in (STATIC / "app.css").read_text()


def test_a_visible_focus_ring_exists_in_both_themes():
    css = (STATIC / "app.css").read_text()
    assert ":focus-visible" in css


def test_the_phone_tab_bar_meets_the_tap_target_floor():
    css = (STATIC / "app.css").read_text()
    assert "44px" in css


def test_the_glossary_ships_and_parses():
    json.loads((STATIC / "glossary.json").read_text())


def test_every_module_is_an_es_module_with_no_build_step():
    for path in _js_files():
        body = path.read_text()
        assert "export " in body
        assert "require(" not in body and "module.exports" not in body


def test_the_shell_loads_the_router_as_a_module():
    body = (STATIC / "index.html").read_text()
    assert '<script type="module" src="./js/app.mjs"></script>' in body
    assert '<script src="./vendor/uPlot.iife.min.js"></script>' in body


def test_the_shell_loads_both_stylesheets_and_nothing_else():
    body = (STATIC / "index.html").read_text()
    assert '<link rel="stylesheet" href="./vendor/uPlot.min.css">' in body
    assert '<link rel="stylesheet" href="./app.css">' in body
    assert len(re.findall(r"<link\s", body)) == 2


def test_the_api_module_is_the_only_one_that_fetches():
    """One place talks to the server, so the ETag discipline cannot be bypassed by a surface."""
    for path in _js_files():
        if path.name == "api.mjs":
            continue
        assert "fetch(" not in path.read_text(), f"{path.name} fetches directly"


def test_every_poll_carries_if_none_match():
    body = (STATIC / "js" / "api.mjs").read_text()
    assert "If-None-Match" in body and "304" in body


def test_the_charts_resize_rather_than_reflow_from_scratch():
    body = (STATIC / "js" / "charts.mjs").read_text()
    assert "ResizeObserver" in body and "setSize" in body


def _light_blocks(css):
    """The two light-token blocks: the manual toggle's and the OS default's."""
    manual = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\}', css, re.S)
    osdef = re.search(r':root:not\(\[data-theme="dark"\]\)\s*\{(.*?)\}', css, re.S)
    return manual, osdef


def test_the_two_light_blocks_carry_the_same_values():
    """A media query cannot join a selector list, so the light half of the token set is written
    twice: once for the toggle and once for the OS default. They can only move together."""
    css = (STATIC / "app.css").read_text()
    manual, osdef = _light_blocks(css)
    assert manual and osdef
    normalise = lambda body: sorted(d.strip() for d in body.split(";") if d.strip())
    assert normalise(manual.group(1)) == normalise(osdef.group(1))


def test_the_dark_and_light_halves_define_the_same_tokens():
    """One token set with two values each: a token defined only in the dark half would silently
    keep its dark value in light mode."""
    css = (STATIC / "app.css").read_text()
    dark = re.search(r"^:root \{(.*?)\n\}", css, re.S | re.M)
    manual, _ = _light_blocks(css)
    names = lambda body: {d.split(":")[0].strip() for d in body.split(";")
                          if d.strip().startswith("--")}
    colours = {n for n in names(dark.group(1)) if not n.startswith("--font")}
    assert colours == names(manual.group(1))
