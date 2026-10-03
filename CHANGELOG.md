# Changelog

## 0.3.0a8 — check browser-session reuse before retrying

- Validate imported sessions against the exact challenged page before reporting
  success or retrying. A session rejected by the site stays in guided setup with
  a clear explanation, rather than restarting the failed transfer.
- Preserve imported browser cookies when HTTP verification fails; only sessions
  used successfully may update the saved cache.
- Skip remaining slow routes behind an already observed mirror verification
  block, while still trying independent HTTP sources for the same edition.
- 117 deterministic tests pass. The exact 24.8 MB Austen archive transfer remains
  unresolved; these fixes do not establish a successful live download.

## 0.3.0a7 — preserve signed-link browser identity

- Keep the browser identity used to obtain a signed download link when contacting
  its file server, unless that server has its own saved session. Cookies remain
  scoped to their own domains; explicit browser-identity overrides remain respected.
- F2 opens the exact download page that requested verification, rather than only
  the mirror home page. Reject cross-origin verification targets.
- Add regressions for identity-bound file links and exact-page browser setup.
- No peer-to-peer download support has been added. The reported 24.8 MB record's
  HTTP transfer remains unverified while browser verification is stalled.

## 0.3.0a6 — bound stalled download retries

- Give automatic source retries a shared 90-second network budget and cap each
  request's timeout at the remaining budget; exclude advertised queue countdowns.
- Stop with an explicit attempted-route count when that budget is exhausted.
- Call the numbered options download routes rather than implying independent servers.
- Show the transfer bar only after receiving file bytes.
- The reported 24.8 MB archive download remains unverified: its exact file link
  returned HTTP 504 in verified Firefox. This release does not claim to repair
  remote file-server availability.

## 0.3.0a5 — Firefox session import and archive source recovery

- Read site-scoped cookies even when running Firefox exclusively locks its database,
  including committed WAL data, using a private temporary snapshot.
- Try up to sixteen listed free sources for the exact selected archive record;
  continue after an over-budget queue and report HTTP failures more clearly.
- Keep Download archive file as the primary action when an official alternative exists.
- Add regression coverage for live database locks, later working sources and queue fallback.

## 0.3.0a4 — controls and direct preview actions

- Give buttons clear outlines, centred labels and visible hover/focus states.
- Keep wide-screen result selection in the existing preview instead of opening
  duplicate full-screen details. Downloads use the compact activity strip.
- Put a matching official EPUB first and label the original route Try archive sources.
- Match terminal margin colour during the app and restore it on exit; retain
  narrow-screen details navigation.


## 0.3.0a3 — Bookfinder and browser sessions

- Make Bookfinder the default: full-terminal results and an adaptive edition preview,
  with descriptions, publisher, honest download statistics and contextual controls.
- Add an explicit official Project Gutenberg EPUB alternative when title, author,
  language and public-domain metadata match; verify the EPUB before publication.
- Add F2 browser setup and `anna connect`: reuse a site-scoped default Linux Firefox
  session or import a session file once, remembering cookies, identity and mirror.
- Preserve existing commands, bounded recovery, cancellation, atomic no-overwrite
  downloads and catalogue checksums.


## 0.3.0a2 — download recovery

- Prefer the record's listed direct Libgen file source before slow partner queues.
- Retry alternative free sources after connection failures or unusable download pages,
  with a bounded attempt count and one shared countdown budget.
- Recognise observed copy-only download controls and prefer explicitly advertised
  short-filename links; no verification scripts are executed.
- Show the current source and network stage, and distinguish file-source timeouts
  from a generic connection failure.
- Reset byte progress between sources and retain checksum, no-overwrite, rate-limit
  and cancellation protections. Explicit `--source` remains pinned.


## 0.3.0a1 — terminal book browser

- Launch a Textual TUI with bare `anna`: searchable results, edition details,
  saved settings, byte progress and cancellable background downloads.
- Add `anna get QUERY --author NAME --format epub` with numbered selection and
  explicit `--choose` for scripts.
- Make terminal searches selectable; preserve JSON arrays and add `--no-select`.
- Prefer .gd automatically and fall back between .gd and .gl for blocked, unavailable
  or unrecognised pages. Explicit mirrors stay pinned; rate limits stop requests.
- Add atomic JSON preferences and `anna config show/set`.
- Default searches to saved English/EPUB preferences and downloads to `~/Books`;
  use explicit filters, `--no-select` and `-d .` to adapt older workflows.
- Preserve existing commands, checksum verification and no-overwrite protection.
- Clarify that users must verify rights for the selected edition.


## 0.2.0rc4

- Update free-source countdowns every second in interactive terminals, showing
  remaining `MM:SS` in place and a clear transition when requesting the download.
- Calculate remaining time with a monotonic clock, including after scheduler delays.
- Close the progress line cleanly on cancellation. Keep JSON and redirected stderr
  free of redraw sequences, with one waiting message per countdown.

## 0.2.0rc3

- Show copyable download, details and source commands after search results, using
  the actual record MD5 and retaining explicit connection options.
- Explain record IDs versus list numbers, download destinations and how to recover
  from an empty search.
- Guide record details and source listings toward the next download command;
  source examples prefer non-fast HTTP sources and show the correct source index.
- Keep JSON output unchanged and add no dependencies.

## 0.2.0rc2

Real public-domain downloads through Anna's Archive now pass end-to-end acceptance
without a browser, account, exported Cookies or additional runtime dependencies.

- Parse the current separate-link search cards and updated record layout; skip
  hidden cover placeholders instead of returning empty titles.
- Retry recognized challenges once using equivalent percent-encoded requests.
  This depends on current upstream behavior and is not a general CAPTCHA solver.
- Follow the current free-source download button and honor server countdowns,
  with `--max-wait` (default: 300 seconds) and stderr-only waiting messages.
- Add reduced public HTML regression fixtures and an opt-in live CLI smoke test
  that verifies catalog MD5, downloaded byte count and EPUB integrity.

## 0.2.0rc1

First distributable preview. Live end-to-end Anna's Archive acceptance remains pending
because tested mirrors require browser verification.

- English documentation, help, errors and project contribution policies.
- Versioned Python wheel/sdist and native standalone release packaging.
- SHA-256-verified shell and PowerShell installers with user-directory installation.
- GitHub Actions for lint, types, unit tests, wheel installation, native artifact tests,
  dependency audits, release checksums and build attestations.
- Stable JSON error codes, stderr-only interactive download progress and SOCKS support.
- Windows reserved-filename handling and UTF-8-safe cross-platform fixtures.
- Package version sourced from one place.

## 0.1.0

Initial local implementation: search, record details, source links, downloads and
mirror diagnostics; filters, JSON, Netscape Cookies and integrity checks.
