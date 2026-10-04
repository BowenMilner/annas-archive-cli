# Bookfinder 0.3.0a11 validation

- 141 deterministic tests passed, including real HTTP mock transfers, headless TUI
  navigation, per-page detail/statistics preload, no refetch on browsing in both
  layouts, pagination/retry/deduplication, history, safe names and explicit actions.
- Ruff lint/format and type checks passed. Wheel/source distribution built and
  passed Twine metadata checks.
- Replaced the existing installed Anna at `~/.local/bin/anna` with the built a11
  wheel using the locked dependency constraints; its source files match the checkout.
- The installed CLI passed the local HTTP fixture smoke test outside the checkout:
  search, info, links, checksum-verified download, history, verified local reuse,
  existing-file rejection and operational errors. All fixture state was isolated.
- Desktop open actions were tested through an injected launcher; no actual reader
  or file-manager window was opened. The screenshot is headless synthetic metadata.
- No new live archive download was required or performed for this release. The
  previous a10 exact-file live acceptance remains historical evidence; these checks
  do not guarantee present availability of every remote file server.

Preloading handles one page at a time, retains successful metadata for the session,
uses requests capped at ten seconds and a 45-second scheduling budget, and stops
additional archive fetches on human verification or rate limits. Unavailable details
can be retried with a new search. Gutenberg alternatives remain a separate background
lookup. Older downloads are not imported into history. Existing files are never
replaced automatically; explicit output/source selection requests a fresh transfer.
