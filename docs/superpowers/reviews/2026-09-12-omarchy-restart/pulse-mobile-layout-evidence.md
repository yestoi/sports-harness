# Pulse responsive layout repair — browser preview

The pre-release Pulse page overflowed a390px browser window to467px. Sparklines
and long technical/invariant labels extended beyond their tiles. A10-lineCSS patch
allocates chart space, wraps narrow tile rows and permits labels to wrap. It changes
no metrics, thresholds, data, or glossary interaction code.

The controller's final preview uses production read-only payloads with the candidate
CSS injected in a private Chromium profile. It is preview evidence, not deployment.
At both390px and1440px all five surfaces have document scrollWidth equal to clientWidth.
Pulse's actualTab events open a glossary definition, and ArrowRight on a focused tab
selectsFloor. All rendered sparkline widths match their holders. No runtime exception
was observed. The baseline under the same capture settings still overflowed to467px.

Artifacts in the controller SDD directory: restart-gutter-baseline-browser.json and
restart-css-final-browser.json; screenshots under the fixed screenshots directory.
The final browser uses explicit390/1440 window sizes and hides scrollbar gutters;
prior device-emulation/visible-gutter probes are retained separately and are not
claimed as the final comparison. Navigation-width and clipping experiments were not
adopted. Original scientific/data failures remain visible in the page.

Independent source review and exact final-main full acceptance remain required before
release. Production visual acceptance is repeated on the actual deployed build.
