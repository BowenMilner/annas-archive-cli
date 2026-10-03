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


## Browser-session reuse diagnosis — 0.3.0a8

With the user's approval, a brief comparison switched the active encrypted Proton
connection from AU#326 to AU#320, then restored AU#326 and the original settings.
On AU#320, Firefox completed the partner-page browser check, while a new HTTP
request using the imported site cookies and Firefox identity still returned a
403 verification page. This shows cookie replay is insufficient in that observed
case; it does not establish whether the cause is a client fingerprint, additional
browser state or a different protection rule. No exact-file transfer was obtained.
The browser itself still needs a fresh exact-file check; the user deferred further
computer use until tomorrow. No peer connections were started.

The client previously cached cookies after every attempted origin, including
failed verification. Synthetic tests reproduce replacement of an imported good
cookie by a blocked response; the saved session now remains unchanged. Guided
setup verifies the exact page before reporting success and does not retry if it
remains blocked. Remaining slow routes behind that same blocked origin are
skipped, preserving independent HTTP alternatives for the exact catalogue MD5.

117 deterministic tests, Ruff and application type checks passed. These are
session-recovery and retry fixes, not acceptance of the user's 24.8 MB download.


## Terminal-only continuation — 0.3.0a9

With desktop interaction deferred, ordinary HTTPX and curl requests both received
403 verification pages using the saved site session. A fresh site-only import
from the running Firefox database and a browser-compatible HTTP transport also
received 403. An isolated headless Firefox process remained on the check page;
no human checks were attempted, desktop windows opened or VPN settings changed.
These diagnostic dependencies were temporary, not added to Anna.

The archive's own catalogue advertises `.pk` alongside `.gd` and `.gl`. Direct
requests to `.pk` successfully retrieved exact record
`fb73d4fd19b0da98923365cb85a03a2b` and followed its real countdown to fresh partner
file URLs, without browser verification. Fresh file requests on two advertised
hosts timed out before receiving bytes. An older exact-file URL also timed out
with both ordinary and explicitly VPN-bound curl connections. A small independent
archive control again downloaded 277,900 bytes with catalogue MD5
`51d2b22ca12a8b470b51f543298b34c9`; that is not the user's selected file.

The implementation previously considered only `.gd` and `.gl`, and automatic
mirror fallback applied to catalogue parsing rather than challenged download
pages. The update includes `.pk` and recovers public slow routes across those
advertised mirrors, retaining the MD5 and route index. It remembers a working
page mirror separately from file availability. Tests exercise all three mirrors,
later-route selection, exact-file checksum, cookie isolation, explicit pinning,
query isolation and rate-limit stops. 126 deterministic tests, Ruff and application
type checks pass. Exact large-file download acceptance remains unresolved.


The installed automatic-route check subsequently received verification blocks
from all three mirrors; a later direct `.pk` request again resolved a fresh file
link, then timed out. Mirror access is intermittent, not guaranteed by its earlier
successful countdown. Further code inspection reproduced another client defect:
reactivating an origin reloaded older disk cookies over cookies renewed by the
previous server response. The fix loads each origin once per client and retains
live cookie updates. A synthetic catalogue-to-file transition now succeeds with
the renewed cookie and persists it. Session validation also follows the same
bounded equivalent-route retry as downloads; rate limits remain terminal.
The final suite has 126 tests. These fixes do not claim the stalled remote file
has become available.

The final installed 0.3.0a9 check selected `.pk`, recovered its initial verification
response through the equivalent slow route, received HTTP 200 for the partner
page and advanced to Contacting file server. The exact-file request then timed
out before receiving bytes. This verifies installed page resolution with the
complete fixes, but still does not verify the selected file transfer.


## Longer exact-file diagnostic — 4 October 2026, 0.3.0a10

A fresh page-resolution attempt was blocked by browser verification. A separate
long request used the most recently resolved exact-file URL, generated roughly
12 minutes earlier on the unchanged network. Its embedded expiry was still in
the future. With a 180-second first-response allowance, asuycdg6.org returned
HTTP 504 from nginx after 61.2 seconds, with zero file bytes. Anna's short read
timeout was therefore not the only reason that this observed request failed.
No saved ebook, desktop interaction or network-setting change resulted.

The archive's public [slow-download implementation](https://software.annas-archive.gl/AnnaArchivist/annas-archive/-/blob/main/allthethings/page/views.py)
requests clearance from within seven minutes. Its public
[session and signing utilities](https://software.annas-archive.gl/AnnaArchivist/annas-archive/-/blob/main/allthethings/utils.py)
renew the paired clearance/check cookies when stale and generate signed URLs
with two-hour expiry. This published main-branch code is not proof of the exact
version deployed on each live mirror, but explains why cookie-file expiry alone
cannot guarantee permanent browser clearance. Signed links were not edited,
re-signed or redirected to unadvertised services.

HTTP 502/504 messages now describe upstream failures and report the responding
file host in source summaries, without exposing signed paths or query tokens.
129 deterministic tests, Ruff and application type checks pass. The live exact
large-file transfer still has not completed.


The archive-advertised direct HTTP counterpart (`45.3.63.27:6060`) was also
compared using the documented domain-signature mapping, keeping the signed path
and checksum unchanged. It returned nginx HTTP 504 after 60.8 seconds. This
observed failure was therefore not recovered by bypassing the HTTPS gateway.
The separate advertised alternate-backend route was then tested once; its partner
page required browser verification, so no alternate file transfer was established.
Expired previously saved URLs were inspected but not requested. No rate-limit
response was routed around, network settings changed or desktop interaction used.

## Independent-network isolation — 4 October 2026

The original source URL listed on the exact record could not be reached, and a
byte-identical MD5 lookup on the listed Library Genesis endpoint did not yield a
file. No different edition was substituted.

A previously authorised brief encrypted Australian VPN comparison matched the
persisted connection identity against the active NetworkManager connection before
switching. An alternate Australian exit still received an advertised link to the
same `s` backend for slow route 14; the duplicate file request was skipped. Proton's
local agent failed to confirm its connected state. The original AU#326 connection
was verified active afterwards, the original runtime protection settings were
reapplied, and the saved settings file remained byte-for-byte unchanged. No desktop
interaction or peer connections took place.

Live diagnostics now accept an exact catalogue MD5 and an optional listed source
number. `--record-only` avoids search-ranking dependence while retaining record,
source, CLI download, exact MD5, EPUB mimetype and ZIP CRC checks. The CI diagnostic
allows a 180-second HTTP inactivity timeout and a bounded job lifetime. Any
successful file is checked in a temporary directory and removed; failure is not
converted into a passing workflow. Listed source numbers include fast links and
therefore differ from the automatic free-route labels.

Two clean GitHub-hosted runs reproduced explicit upstream timeouts:

- [Exact 24.8 MB record, listed source 36 (slow route 14)](https://github.com/BowenMilner/annas-archive-cli/actions/runs/37134829200):
  HTTP 504 from the archive download chain with a 180-second HTTP allowance.
- [Small archive-hosted control, listed source 22 (slow route 0)](https://github.com/BowenMilner/annas-archive-cli/actions/runs/37134904076):
  HTTP 504 with the same allowance. This deliberately exercised the partner route,
  rather than its independently available Library Genesis source.

A preceding exact-file test with a 60-second allowance returned ReadTimeout. The
longer allowance exposed the service's HTTP 504 rather than fixing the transfer.
This reproduces failures outside the user's host/VPN and across two catalogue
records; it does not establish that every remote backend is down. Contemporary
[user reports](https://www.reddit.com/r/Annas_Archive/comments/1wwemvy/error_504/)
describe similar failures on 3 October; these are corroboration, not an operator
status announcement.

131 deterministic tests and Ruff lint/format checks pass. The exact selected
archive file remains unavailable in these live checks and download acceptance
remains open. Resolving that external blocker requires a working advertised HTTP
source; passing tests, catalogue pages or a different edition cannot satisfy it.

## Fresh official-domain comparison — 4 October 2026

The live [official-mirror FAQ](https://annas-archive.pk/faq#official-mirrors)
continues to list only `.gl`, `.pk` and `.gd`, matching the client's configured
mirror set. A fresh direct request to slow route 14 for the exact selected MD5
was made on each origin, with the existing site-scoped session and the bounded
normal equivalent-route retry. All three ultimately returned a 403 browser check;
none reached a file transfer in this comparison. No new official origin was found
in the current list. This result is distinct from the earlier `.pk` page success
followed by file-server HTTP 504: page access and file availability remain separate
conditions, and a catalogue-mirror switch does not guarantee a different file host.
