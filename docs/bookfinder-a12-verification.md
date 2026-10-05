# Bookfinder 0.3.0a12 validation

154 deterministic tests passed, including a blocked detail request proving that all
50 results are visible and browsable before any details finish. The same test checks
selection prioritisation, pending-request sharing with the narrow details view and
one info fetch per record. A separate test changes search while an old request is
blocked and verifies that its late response cannot overwrite the new results.

Other regressions cover sorting loaded results by downloads while preserving the
highlighted edition, placing missing counts last, matching whole query words and
numbers against titles/advertised filenames, excluding volume 12/130 from a volume
13 exact search, and parsing filenames from captured real catalogue layouts.
CLI JSON exact matching/download sorting and the existing download/history tests pass.

Ruff lint/format and type checks pass. The wheel/sdist are built and metadata checked.
The installed wheel is checked against the source and the installed CLI is exercised
outside the checkout using the isolated local HTTP fixture smoke test. No desktop
windows are opened and no fresh remote archive acceptance is claimed. The screenshot
uses synthetic test metadata and counts.

Results-first loading replaces a11's blocking page preload. Background requests run
one at a time, prioritise the selected book for the next request, respect ten-second
request timeouts and stop on rate limits/human verification. Counts rank the loaded
editions only; popularity does not establish file quality. Exact terms is whole-word
and whole-number matching, not literal character-for-character filename equality.
