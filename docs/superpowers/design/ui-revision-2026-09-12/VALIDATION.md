# Package validation and limits

## What was checked during this session

- Original interactive concept: 18 combinations of three screens, three moments and two widths (390/1440). Checks covered horizontal overflow, expected headings and invalid rendered values.
- Navigation, temporary sample-slip recording, payout arithmetic, invalid-odds rejection and a no-position scenario passed.
- SGP extension: six additional pregame/live/final layout cases across both widths passed. A completion moved related stats; a correction reduced both; a live target crossing stayed provisional. Changing a leg cleared its quote, and a sample actual quote/stake determined the recorded return.
- Broadcast comparison: the same 24 cases and interactions were rerun, plus six dense-desk layout cases. Screenshots were inspected and mobile score wrapping, status color and a preview-only focus outline were corrected during iteration.
- The concepts run from local HTML without external assets, provider calls or account access. All displayed fixtures are fictional.

These are prototype smoke checks, not production tests, a complete accessibility audit, measured provider performance or acceptance of a final design. The selected design direction and behavior in PRODUCT-BRIEF supersede rejected/exploratory details in the reference HTML.

## Packaging checks

The package builder checks local Markdown links, confirms that the exported comparison HTML contains its inline CSS/JavaScript, checks the JavaScript syntax with Node, generates file sizes and SHA-256 hashes in MANIFEST.json, creates a zip from this directory only, and reads the archive back to verify every entry against the source bytes. See the creation command's result for the final file count and archive hash.

## Boundaries

Only design documents, reference concepts/screenshots and an export archive were created or updated for this task. No application source, runtime configuration, API subscription, trading rule, active roadmap, controller state or journal was changed by this session. Other setup work was modifying roadmap/controller files in the shared workspace; those changes are not included in this export.

Read current source and operational evidence after the design session. The historical source SHA in README is only the reading anchor for this package.
