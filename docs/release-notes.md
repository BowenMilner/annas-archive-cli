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
