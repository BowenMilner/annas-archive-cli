# Anna's Archive CLI

[![CI](https://github.com/meurz/annas-archive-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/meurz/annas-archive-cli/actions/workflows/ci.yml)

Search Anna's Archive, inspect book records and download files from your terminal.
The command is `anna`. This is an independent, unofficial project.

> **Preview:** real Anna's Archive search → record → free-source download has been
> verified with public-domain EPUBs, including catalog MD5 and EPUB integrity checks.
> No browser, account or additional dependencies are needed for the verified routes.
> Site protection can change; this is not a universal CAPTCHA solver.
> See [live verification](docs/live-verification.md) for evidence and limits.

## Run in one command

With [uv](https://docs.astral.sh/uv/), run the pinned preview:

```sh
uvx --from annas-archive-cli==0.2.0rc4 anna --help
```

No repository clone or manual virtual environment is needed. uv needs a compatible
Python runtime and can download one when permitted. To install permanently, use
`uv tool install annas-archive-cli==0.2.0rc4`.

The identical wheel is also available directly from GitHub Releases:

```sh
uvx --from https://github.com/meurz/annas-archive-cli/releases/download/v0.2.0rc4/annas_archive_cli-0.2.0rc4-py3-none-any.whl anna --help
```

### Without Python

Standalone archives are available from [GitHub Releases](https://github.com/meurz/annas-archive-cli/releases).
They bundle the runtime. Install the preview on Linux or macOS:

```sh
curl -fsSL https://github.com/meurz/annas-archive-cli/releases/download/v0.2.0rc4/install.sh | ANNA_VERSION=v0.2.0rc4 sh
```

Windows PowerShell:

```powershell
$env:ANNA_VERSION='v0.2.0rc4'; & ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing https://github.com/meurz/annas-archive-cli/releases/download/v0.2.0rc4/install.ps1).Content))
```

Alternatively, download and inspect the installer before running it, or extract the
archive yourself and run `anna --help` / `anna.exe --help`. Installers verify the
archive's SHA-256 before replacing an existing executable. Release build attestations
can be verified with `gh attestation verify <archive> --repo meurz/annas-archive-cli`.
The executables are not platform-signed or notarized; OS security prompts may apply.

| Target | Build/test baseline |
| --- | --- |
| Linux x86_64 | Ubuntu 22.04, glibc 2.35+ |
| Linux arm64 | Ubuntu 24.04, glibc 2.39+ |
| macOS arm64 / x86_64 | macOS 15 |
| Windows x86_64 | Windows Server 2022 runner; desktop Windows 11 not separately verified |

These targets are published only after native artifact tests pass. Alpine/musl and
Windows arm64 are not supported by the standalone builds. The Python package requires
Python 3.11+; CI tests 3.11 and 3.14.

### Upgrade and uninstall

Run the installer again with the desired `ANNA_VERSION`. Without that variable it
selects the latest **stable** release, which may not exist during the preview phase.
Use `ANNA_INSTALL_DIR` to override the destination. The default is `~/.local/bin` on
Linux/macOS and `%LOCALAPPDATA%\Programs\anna` on Windows. Installers explain how to
add this directory to PATH; they do not edit your shell/profile or request admin rights.

Remove the installed executable to uninstall. For a uv installation, use
`uv tool uninstall annas-archive-cli`; after PyPI publication, upgrade with
`uv tool upgrade annas-archive-cli`. For GitHub wheel installs, install the new version's
wheel URL with `uv tool install --reinstall <URL>`. Downloaded books are never removed.

## Usage

```sh
anna search "Jane Austen" --lang en --ext epub --limit 5
anna search "三体" --lang zh --ext epub --json
anna search "Pride and Prejudice" --sort smallest --page 2
anna info <MD5-or-record-URL>
anna links <MD5-or-record-URL> --json
anna download <MD5-or-record-URL> --source 2 -o book.epub
anna download <file-URL> -d downloads --md5 <expected-MD5>
anna doctor --json
```

Replace angle-bracket placeholders with values. Search prints record URLs and
copyable next-step commands using the first result's MD5. Use another result's URL
or MD5 to select it; list numbers are not record IDs. Details and source listings
also show the next download command, and explicit connection options are retained.
These hints appear only in human-readable output; `--json` remains pure data.
`--source` selects the one-based index printed by `anna links`; without it, download
chooses the first non-fast HTTP source. It does not switch sources automatically.
Free-source countdowns are honored for up to 300 seconds; use `--max-wait 0` to fail
immediately or `--max-wait 600` to allow longer waits. Interactive terminals show
a live `MM:SS` countdown on stderr, updated in place every second. JSON mode and
redirected stderr emit one waiting line per countdown. Ctrl+C cancels the wait.
`--limit` caps records on the requested page, not the number of pages fetched.
`--lang`, `--ext` and `--content` may be repeated.

Downloads follow HTTP redirects and explicit file-download controls. Data is streamed
into a temporary file in the destination directory and published without overwriting
existing files. Record downloads always verify the record MD5; direct URL downloads
can use `--md5`. MD5 identifies catalog files, while release archives use SHA-256.
HTML/JSON/XML responses, empty downloads, length mismatches and MD5 failures are
rejected. Temporary files are removed on errors and interruption. Filesystems must
support hard links for atomic no-overwrite publication (for example, ext4/APFS/NTFS).
There is no resume support.

## Mirrors, Cookies and proxies

```sh
anna --base-url https://annas-archive.gl doctor
anna --cookies /path/to/cookies.txt search "Pride and Prejudice"
anna --timeout 60 search "Pride and Prejudice"
```

Global options go **before** the subcommand. Precedence is command-line option,
environment variable, then built-in default.

| Option | Environment | Default |
| --- | --- | --- |
| `--base-url` | `ANNA_BASE_URL` | `https://annas-archive.gl` |
| `--cookies` | `ANNA_COOKIES` | No Cookie file |
| `--user-agent` | `ANNA_USER_AGENT` | Built-in browser-style User-Agent |
| `--timeout` | `ANNA_TIMEOUT` | 30 seconds per network operation |

Standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE` and
`SSL_CERT_DIR` are supported through httpx. SOCKS support is included.
Netscape Cookie files are read with domain/path/expiry restrictions. Cookies may be
bound to browser fingerprints, IP or User-Agent; exporting them does not guarantee
access. Match the browser User-Agent when needed. Cookies are not transferred between
mirrors or saved in the repository. TLS certificate validation remains enabled.

Mirror domains change. Verify a new origin before passing it to `--base-url`.
An HTTP 200 parking or advertising page is not a functioning mirror. Unknown page
layouts produce errors rather than silently returning no results. `doctor` inspects
live mirror availability separately from deterministic CI tests.

## Scripting contract

All commands accept `--json`; the global position is also supported. JSON data goes
to stdout. Human-mode progress/errors go to stderr. JSON-mode operational errors go
to stdout with exit status 1; Click usage errors remain on stderr with status 2.
Successful empty searches return `[]` with status 0. Ctrl-C exits 1.

| Command | JSON result |
| --- | --- |
| `search` | Array of book records |
| `info` | Book record, including `links` |
| `links` | Array of `{index, label, url, kind}` |
| `download` | `{path, bytes, md5}` |
| `doctor` | `{base_url, ok, results}` |

```json
{"error":{"code":"browser_verification_required","type":"ChallengeError","message":"Browser verification required..."}}
```

Use `error.code` in scripts. Codes include `browser_verification_required`,
`unrecognized_page`, `network_error`, `filesystem_error`, `invalid_input`,
`file_exists`, `integrity_error`, `rate_limited`, `download_wait_required`,
`http_error` and `operation_failed`.
`type` is retained for compatibility/diagnostics; English messages are not stable APIs.
Breaking CLI/JSON changes are called out in the changelog, including during 0.x releases.

## Development

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check src/anna
uv run pytest -q
uv build
uv run twine check dist/*
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[release operations](docs/releasing.md) and [CHANGELOG.md](CHANGELOG.md).
Documentation, comments, docstrings, interface text and new collaboration/release
text are English. Book metadata and multilingual fixtures retain their original text.

Parsing was independently implemented against the public HTML layout documented in
[Anna's Archive source](https://github.com/LilyLoops/annas-archive/tree/main/allthethings).
Fixtures include synthetic examples and reduced public HTML snapshots of Gutenberg
book records. No ebooks, account credentials or browser runtime are included.

Opt-in live acceptance (downloads a public-domain EPUB to a temporary directory):

```sh
uv run python scripts/live_smoke.py
```

This runs the actual CLI outside the checkout, verifies search, details, sources,
downloaded bytes, catalog MD5 and EPUB CRC, and then removes the test download.
It is separate from deterministic CI because mirrors and protection rules change.
