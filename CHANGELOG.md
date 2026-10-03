# Changelog

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
