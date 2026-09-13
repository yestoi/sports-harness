# Pulse mobile CSS repair

Base: `8631fcd36e2b101ff77acb91ff07582c96f63a9f` in assigned recovery-fix48-review worktree. Status: uncommitted CSS patch ready for controller browser validation.

Read the supplied browser probe and viewed `/tmp/restart-before-390-pulse-top.png`. The probe records a 468px document at a 390px viewport, with 160px sparkline holders/canvases reaching x=467 and the executor-version technical key reaching x=417.6. `statTile` puts the value and initially empty spark holder in an unwrapped flex row; charts use a 160px fallback when that holder has no width. The fixed-size child then contributes to its own parent's width.

Changed only `harness/dashboard/static/app.css` (10 added lines). Tile rows can wrap; their spark holders receive a flexible 100px preferred basis and zero automatic minimum, giving uPlot an independently measurable container. Under the existing 720px phone breakpoint, spark holders occupy a full-width line below the value. Existing ResizeObserver sizing follows that container. Technical keys allow breaks anywhere, with the existing glossary wrapper/button bounded to the tile and wrapped button text aligned left. Chart visibility, focusable buttons, handlers and focus outlines remain; no body clipping, hidden-overflow workaround or JavaScript change added. Desktop keeps value/chart alongside each other when they fit and wraps only when needed.

Verification here: `git diff --check` passed and reviewed the component/observer contract against the selectors. No local browser/runtime, network, DB or test command ran; no commit. Controller must confirm true 390px document/body width, spark canvas/holder fit, readable wrapped executor key, keyboard glossary operation and desktop appearance, then include the final change in its exact-SHA acceptance run. Table content remains in its existing horizontal-scroll containers; element rectangles extending inside those containers do not alone establish page overflow.

The supplied screenshot/probe was treated as evidence. No instruction-like data was encountered or acted upon.
