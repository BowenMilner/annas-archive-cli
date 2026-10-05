# Bookfinder 0.3.0a12

Search results appear as soon as the catalogue returns them. Background loading then
adds edition details/statistics without holding back the list. Highlighted books get
the next request; narrow details views share the same pending fetch. New searches
cancel older work, and late responses cannot change the current results.

Exact terms optionally filters titles and advertised filenames using whole query
words/numbers. Series 13 does not match volume 12 or 130. Most downloaded ranks the
loaded editions as counts arrive, keeps the selected edition and places unavailable
counts last. Popularity does not guarantee quality. CLI equivalents are `--exact`
and `--sort downloads` on `anna search`.

# Bookfinder 0.3.0a11

Page details and statistics preload with visible progress, then stay cached as you
browse or open details. Rate limits/browser checks stop further archive preloads;
individual failures leave results available. Optional official alternatives remain
a separate background lookup.

Search beyond the first page, sort editions by size/date/relevance, and change format
without retyping your search. Load more keeps the current filters and selection;
failed pages can be retried and duplicate editions are removed.

History keeps local receipts for newly completed downloads. Repeated edition downloads
recheck the existing file before reusing it. Record filenames now show author, title
and an edition identifier; explicit output filenames and direct URL names still work.
Open book and Show folder are available after success and from History, only when
chosen. No background desktop windows are launched.

Archive checksums, source fallback, browser session handling and no-overwrite guarantees
remain in place. Queueing and partial-download resumption are deferred. Older downloads
are not retrospectively imported into history. See the README for storage, JSON and
filename compatibility details.

## Historical upstream release — 0.2.0rc4

Live free-source countdowns in Anna's Archive CLI 0.2.0rc4.

Interactive terminals now show a `MM:SS` countdown that updates in place every
second, then transitions to requesting the download. Remaining time uses a
monotonic clock so scheduling delays do not extend the displayed countdown.
Ctrl+C cancels the wait and closes the progress line cleanly.

JSON mode and redirected stderr retain one waiting line per countdown; JSON stdout
stays machine-readable. No new dependencies or browser runtime are required.

```sh
uvx --from annas-archive-cli==0.2.0rc4 anna download 51d2b22ca12a8b470b51f543298b34c9 -o pride-and-prejudice.epub
```

Includes the next-step guidance from rc3 and browserless download support from rc2.
Upstream protection and source availability can still change; see
`docs/live-verification.md` for the tested routes and limits.

Wheel, source distribution and five native standalone archives are available with
SHA-256 checksums and GitHub build attestations. The binaries are not OS-signed or
notarized.
