# Live download verification

On 2026-09-12, the actual CLI completed search, record inspection, source listing,
free partner download, catalog MD5 comparison and EPUB ZIP/CRC validation. These
requests used ordinary httpx, without accounts, browser Cookies, proxies or new
runtime dependencies. Each command ran in a fresh process outside the checkout.

| Mirror | Public-domain edition | Bytes | Catalog MD5 |
| --- | --- | ---: | --- |
| `annas-archive.gl` | Pride and Prejudice, Project Gutenberg, 1998 | 277900 | `51d2b22ca12a8b470b51f543298b34c9` |
| `annas-archive.gd` | Pride and Prejudice, Project Gutenberg, 1998 | 277900 | `51d2b22ca12a8b470b51f543298b34c9` |
| `annas-archive.gl` | Frankenstein, Project Gutenberg, 1993 | 174355 | `0fc6a1a51ea11b894d78117a553bb1f5` |

The two copies of Pride and Prejudice have SHA-256
`a17d6fff2ce6072a1f95ec38c58cb2c9c0e244b88a01e6487152ce2387ce159f`.
The source was the free Slow Partner Server #1 listed on the real record page;
this was not a substituted Gutenberg download URL or a local HTTP fixture.
Frankenstein has SHA-256
`4d363734f81817fa43a223a9f6dfcb1382072a07a78ff1350220d65cdc70271f`.

## Reproduce

```sh
uvx --from annas-archive-cli==0.2.0rc2 anna search '"Pride and Prejudice" "Gutenberg"' --lang en --ext epub --sort smallest --limit 3
uvx --from annas-archive-cli==0.2.0rc2 anna info 51d2b22ca12a8b470b51f543298b34c9
uvx --from annas-archive-cli==0.2.0rc2 anna links 51d2b22ca12a8b470b51f543298b34c9
uvx --from annas-archive-cli==0.2.0rc2 anna download 51d2b22ca12a8b470b51f543298b34c9 -o pride-and-prejudice.epub
```

From a checkout, `uv run python scripts/live_smoke.py` performs the complete check,
prints a JSON report and removes the temporary download. Use `--base-url` to test
another verified mirror. Live tests remain opt-in because they depend on external
services; deterministic CI uses local fixtures.
The manual **Live diagnostics** GitHub workflow also provides a `download` mode
for the same check from a fresh runner.

## Behavior and limits

The current site sends anonymous requests through a verification redirect. After
a recognized challenge response, the CLI retries once with an equivalent
percent-encoded `check=1` value. For a challenged free-source route on the configured
mirror, it retries the underscore in `/slow_download/` as `%5F`. This works because
the current upstream routing and protection handle these equivalent spellings
differently. It can stop working when the site changes; it is not a supported API
or a general CAPTCHA solver. Persistent challenges still return
`browser_verification_required`.

Free sources may return a countdown instead of a link. The CLI waits for the
displayed interval and retries, with a total wait budget of 300 seconds and bounded
attempts. Use `--max-wait 0` to disable waiting, or increase it explicitly. Countdown
messages use stderr; JSON stdout remains machine-readable. An exhausted wait
budget returns `download_wait_required`. Login forms, unknown pages and files
that fail MD5 checks are never reported as successful downloads.

The `annas-archive.pk` mirror returned valid search and record pages during these
checks, but complete runs encountered connection errors. It is not counted as a
successful end-to-end mirror. Availability of other records, external sources,
long queues and other network exits is not established by these small-book tests.

## Fork TUI acceptance — 3 October 2026

The fork's complete headless TUI event loop completed live search, edition details
and download on this Arch host using automatic mirror selection. The listed
Libgen.li file source returned the same selected catalogue edition:

- Title: Pride and Prejudice, Project Gutenberg, 1998.
- Catalogue MD5: `51d2b22ca12a8b470b51f543298b34c9`.
- Downloaded size: 277900 bytes.
- Catalogue MD5, EPUB mimetype and ZIP CRC all passed.
- The test file was downloaded to a temporary directory and removed afterwards.

The first partner file server timed out after its countdown. Additional partner
routes also timed out, including copy-only HTTP URLs. A reduced, sanitised fixture
records the observed copy-only control layout. The fork now prefers the record's
listed Libgen file source, then tries bounded free-source alternatives; it does not
substitute a different edition or disable checksum/TLS verification.

This verifies the live network and UI event flow on this host. It is separate from
manual acceptance in the user's terminal and does not establish availability of all
records or source servers.

## Large illustrated EPUB diagnosis — 3 October 2026

The user subsequently reported three file-source timeouts for a 24.8 MB Austen
edition after reopening the installed app. Several matching Gutenberg EPUB records
exist, including `9401a1d7f8532732a4d8d1a5fa2086e0`; these do not list the direct
Libgen source used by the successful small-book check. The precise record selected
by the user was not established from the screenshot and size alone. Later partner
sources #4 and #16 for the above record returned browser-verification challenges.
No successful transfer of that Anna catalogue file is claimed.

The installed CLI downloaded the official illustrated EPUB linked by
[Project Gutenberg ebook 1342](https://www.gutenberg.org/ebooks/1342), using
`https://www.gutenberg.org/ebooks/1342.epub.images`:

- Downloaded size: 24848783 bytes.
- Measured MD5: `68ce39fde86db21b727fa9eb35d6b7cc`.
- EPUB mimetype and ZIP CRC passed; temporary files were removed afterwards.

The installed TUI transfer worker then downloaded the same official file, with
exactly 24848783 received bytes and the correct progress total, and verified the
above checksum and EPUB integrity. That transfer probe injected the official
edition's catalogue metadata; search does not currently offer this official source.
This tests large-file transfer and progress, not an end-to-end catalogue fallback
or the user's original selection.

The failure remains a source-availability limitation, not an established file-size
limit. The automatic three-source fallback does not establish that every listed
source is unavailable. A direct official-source alternative must identify its
edition explicitly rather than silently substituting a different checksum.

## Bookfinder and official edition — 3 October 2026

Version 0.3.0a3's real Textual application completed search, highlight, official
edition selection and a large EPUB transfer using live network requests. Search
`"Pride and Prejudice" "Gutenberg"`, author `Jane Austen`, English EPUB returned
24.8 MB catalogue record `fb73d4fd19b0da98923365cb85a03a2b`. Its preview discovered
Project Gutenberg record 1342 by matching the official title, author, language
and public-domain metadata. The deliberately selected official alternative saved
24,848,783 bytes with MD5 `68ce39fde86db21b727fa9eb35d6b7cc`; EPUB mimetype and
all ZIP entry CRC checks passed. This is a separate official illustrated edition,
not a successful transfer of that Anna catalogue MD5. The original partner
servers may still time out or require verification.

Deterministic tests cover explicit selection after archive source failure,
invalid-EPUB rejection, full-screen layout and resizing during a details view,
Firefox import restricted to one site's cookies, saved-session reuse across new
clients, per-source browser identity and setup retry. Browser session tests use
synthetic cookies; no live human verification or universal CAPTCHA clearance is
claimed. Checks may recur after expiry or a changed network/browser identity.

## Visible terminal acceptance — controls update

Version 0.3.0a4 was launched in the user's actual Kitty window. Search for
Pride and Prejudice with author Jane Austen returned the same 24.8 MB record.
Clicking that result kept the existing preview; selecting its outlined primary
Download official EPUB action opened the compact activity strip and saved
`~/Books/gutenberg-1342-illustrated.epub`. Independent validation found 24,848,783
bytes, MD5 `68ce39fde86db21b727fa9eb35d6b7cc`, correct EPUB mimetype and passing
ZIP CRCs. The three original archive sources still timed out before this explicit
alternative was selected.

The user's Kitty theme had 24-pixel terminal padding. A backed-up host-local
change set that padding to zero and the real window was checked after reloading.
This affects the normal shell too; it is not a repository setting. The app colours
fractional-cell terminal margins while running and restores the configured
background on exit.


## Running Firefox session import and remaining archive failure

Version 0.3.0a5 was installed on 3 October 2026. The user's default Firefox
profile held an exclusive lock on cookies.sqlite. The old reader consequently
reported a session-read failure despite successful browser verification.
The installed replacement imported eight current cookies for annas-archive.gd
with Firefox still running and saved its browser identity. No cookie values
were logged. Regression tests cover exclusive locks in rollback-journal and WAL
modes and exclude unrelated sites from the saved session.

The original 24.8 MB record remains fb73d4fd19b0da98923365cb85a03a2b.
Its sixteen listed free partner routes are now eligible for bounded fallback;
an exhausted queue budget no longer prevents trying a later ready route.
Archive downloads remain the primary action and require this exact MD5.

Live connections to the file hosts succeeded quickly, but signed file transfers
from multiple hosts timed out before response headers. A longer earlier request
returned HTTP 504; a later request returned a rate-limit response. The real
Firefox partner download page passed its browser check and warned of heavy
activity from the current IP; opening its exact file link subsequently returned 504 Gateway Time-out
in Firefox too. No successful transfer was established. Further network probes were stopped after the rate limit.
No transfer of this exact archive record is claimed. The VPN was left unchanged.

Validation: 106 deterministic tests passed, as did Ruff and application type
checks and wheel/source builds. The installed 0.3.0a5 session reader was exercised
against the live running Firefox profile. Full archive-download acceptance remains
open pending file-server availability and expiry of the rate limit.


## Stalled route retries — 0.3.0a6

The user reported the same transfer still contacting route 10/16 without receiving
file bytes. Increasing candidate count had compounded the waiting rather than
established a functioning download. DNS inspection showed wbsg8v.xyz resolving to
45.3.63.28 and asuycdg6.org to 45.3.63.27, confirming that some differently labelled
routes are aliases. This does not establish that all partner routes share a backend.

Automatic failed attempts now share a 90-second network budget; request timeouts
are capped at the remaining budget. Queue countdowns are accounted separately,
and successful transfers are not capped to 90 seconds overall. Explicit sources
retain their configured timeout. The UI calls these download routes and does not
show an indeterminate transfer bar before receiving bytes. Synthetic regressions
verify a stop after three 30-second stalls, exclusion of queue time, and clipping
the next timeout to the remaining five seconds before checksum-verified success.

109 tests passed. No further live file requests were made in this change after the
previous observed rate limit. The exact archive download remains unresolved;
these changes address accumulated waiting rather than server availability.


## HTTP-only continuation — 0.3.0a7

The user declined peer-to-peer downloads. No torrent session was started and no
peer-download backend was added to Anna. A read-only metadata inspection confirmed
an exact preservation-torrent entry, but it was not transferred.

A regression reproduced a genuine HTTP bug: the catalogue used the saved Firefox
identity, while following its signed file link reverted to the default Chrome
identity. The corrected client inherits the signing browser identity across that
link; independent source sessions and explicit identity overrides still take
precedence, and cookies remain domain-scoped. A mock server requiring the signing
identity now delivers the checksum-verified file without receiving catalogue cookies.

The guided browser setup also now retains and opens the exact challenged download
page instead of only the mirror origin. Cross-origin targets are rejected.
112 deterministic tests, Ruff and application type checks passed.

A live retry of the exact 24.8 MB record reached browser verification with the
correct Firefox identity on every observed request. Firefox itself remained at
Checking your browser before accessing annas-archive.gd; no checkbox was observed
in its accessible page state, and no exact-file transfer was established. The
user's VPN connection was left unchanged. An independent real HTTP archive source
for catalogue MD5 51d2b22ca12a8b470b51f543298b34c9 again downloaded 277,900 bytes and
passed that catalogue checksum; this is a diagnostic control, not the user's file.
