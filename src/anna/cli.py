import json
import shlex
import sys
import time
from dataclasses import asdict
from pathlib import Path

import click
import httpx

from anna import __version__
from anna.client import DEFAULT_BASE_URL, DEFAULT_USER_AGENT, Client
from anna.errors import AnnaError
from anna.parsing import record_id


class ErrorGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if isinstance(exc, httpx.HTTPError):
                code = "network_error"
                message = (
                    f"Network request failed ({type(exc).__name__}); "
                    "check your network, proxy or --base-url."
                )
            elif isinstance(exc, OSError):
                code = "filesystem_error"
                message = f"File operation failed: {exc.strerror or type(exc).__name__}."
            else:
                code = exc.code
                message = str(exc)
            if (ctx.obj or {}).get("json"):
                click.echo(
                    json.dumps(
                        {"error": {"code": code, "type": type(exc).__name__, "message": message}},
                        ensure_ascii=False,
                    )
                )
                ctx.exit(1)
            raise click.ClickException(message) from exc


@click.group(cls=ErrorGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--base-url",
    envvar="ANNA_BASE_URL",
    default=DEFAULT_BASE_URL,
    show_default=True,
    help="Mirror origin URL.",
)
@click.option(
    "--cookies",
    envvar="ANNA_COOKIES",
    type=click.Path(path_type=Path, exists=True),
    help="Netscape Cookie file exported from your browser.",
)
@click.option(
    "--user-agent",
    envvar="ANNA_USER_AGENT",
    default=DEFAULT_USER_AGENT,
    help="Match your browser User-Agent when using its Cookies.",
)
@click.option(
    "--timeout",
    envvar="ANNA_TIMEOUT",
    type=click.FloatRange(min=0, min_open=True),
    default=30,
    show_default=True,
    help="Network timeout in seconds.",
)
@click.option(
    "--json", "json_output", is_flag=True, help="Output JSON. Also accepted after a subcommand."
)
@click.version_option(__version__)
@click.pass_context
def main(ctx, base_url, cookies, user_agent, timeout, json_output):
    """Search Anna's Archive, inspect records and download files."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        json=json_output,
        client_options=dict(
            base_url=base_url, cookies=cookies, user_agent=user_agent, timeout=timeout
        ),
    )


def json_option(function):
    return click.option("--json", "json_output", is_flag=True, help="Output JSON.")(function)


def prepare(ctx, json_output):
    ctx.obj["json"] = ctx.obj["json"] or json_output
    return Client(**ctx.obj["client_options"])


def emit(value):
    click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def command_hint(ctx, *arguments):
    """Render a copyable command, retaining explicitly supplied connection options."""
    root = ctx.find_root()
    words = ["anna"]
    for name in ("base_url", "cookies", "user_agent", "timeout"):
        if root.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
            words.extend(["--" + name.replace("_", "-"), str(root.params[name])])
    words.extend(str(argument) for argument in arguments)
    if sys.platform == "win32":
        # The documented Windows shell is PowerShell; POSIX escapes differ.
        return " ".join(
            word
            if word and all(c.isalnum() or c in "-_/.:=" for c in word)
            else "'" + word.replace("'", "''") + "'"
            for word in words
        )
    return shlex.join(words)


class DownloadCountdown:
    def __init__(self, interactive: bool):
        self.interactive = interactive
        self.active = False
        self.width = 0

    def __call__(self, remaining: int) -> None:
        if not self.interactive:
            if remaining > 0 and not self.active:
                click.echo(f"Free source countdown: waiting {remaining} seconds...", err=True)
            self.active = remaining > 0
            return
        if remaining > 0:
            minutes, seconds = divmod(remaining, 60)
            message = (
                f"Free source countdown: {minutes:02d}:{seconds:02d} remaining (Ctrl+C to cancel)"
            )
            self.width = max(self.width, len(message))
            click.echo("\r" + message.ljust(self.width), err=True, nl=False)
            self.active = True
        elif self.active:
            message = "Countdown complete; requesting download..."
            click.echo("\r" + message.ljust(self.width), err=True)
            self.active = False
            self.width = 0

    def close(self) -> None:
        if self.interactive and self.active:
            click.echo(err=True)
        self.active = False
        self.width = 0


class DownloadProgress:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.bytes = 0
        self.last_update = 0.0

    def __call__(self, amount: int) -> None:
        self.bytes += amount
        now = time.monotonic()
        if self.enabled and now - self.last_update > 0.2:
            click.echo(f"\rDownloaded {self.bytes / 1048576:.1f} MiB", err=True, nl=False)
            self.last_update = now

    def close(self) -> None:
        if self.enabled and self.bytes:
            click.echo(err=True)


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option(
    "--lang", multiple=True, help="Language code; repeat for multiple languages (en, zh, ja)."
)
@click.option("--ext", multiple=True, help="File format; repeat for multiple formats (epub, pdf).")
@click.option(
    "--content", multiple=True, help="Content type, such as book_nonfiction or book_fiction."
)
@click.option(
    "--sort",
    type=click.Choice(
        ["relevance", "newest", "oldest", "largest", "smallest", "newest_added", "oldest_added"]
    ),
    default="relevance",
    show_default=True,
)
@click.option("--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Maximum records from this page; does not fetch additional pages.",
)
@json_option
@click.pass_context
def search(ctx, query, lang, ext, content, sort, page, limit, json_output):
    """Search by title, author, ISBN or keywords."""
    with prepare(ctx, json_output) as client:
        books = client.search(
            " ".join(query),
            lang=lang,
            ext=ext,
            content=content,
            sort="" if sort == "relevance" else sort,
            page=page,
        )[:limit]
    if ctx.obj["json"]:
        emit([asdict(book) for book in books])
    elif not books:
        click.echo("No matching books found.")
        click.echo("Try fewer keywords or remove language, format or content filters.")
    else:
        for i, book in enumerate(books, 1):
            click.echo(f"{i}. {book.title}")
            click.echo(f"   {book.author or 'Unknown author'} | {book.metadata}")
            click.echo(f"   {book.url}")
        click.echo("\nNext steps for result 1:")
        click.echo(f"  Download: {command_hint(ctx, 'download', books[0].md5)}")
        click.echo(f"  Details:  {command_hint(ctx, 'info', books[0].md5)}")
        click.echo(f"  Sources:  {command_hint(ctx, 'links', books[0].md5)}")
        click.echo("For another result, use its URL or MD5, not its list number.")
        click.echo("Files are saved in the current directory. Add -d downloads to choose a folder.")


@main.command()
@click.argument("record")
@json_option
@click.pass_context
def info(ctx, record, json_output):
    """Inspect a record by MD5 or record URL."""
    with prepare(ctx, json_output) as client:
        book = client.info(record)
    if ctx.obj["json"]:
        emit(asdict(book))
    else:
        for label, value in [
            ("Title", book.title),
            ("Author", book.author),
            ("Publisher", book.publisher),
            ("File", book.metadata),
            ("MD5", book.md5),
            ("URL", book.url),
            ("Description", book.description),
        ]:
            if value:
                click.echo(f"{label}: {value}")
        click.echo(f"Download sources: {len(book.links)}")
        if book.links:
            click.echo()
            if any(
                link.kind != "fast" and link.url.startswith(("http://", "https://"))
                for link in book.links
            ):
                click.echo(f"Download: {command_hint(ctx, 'download', book.md5)}")
            click.echo(f"Choose a source: {command_hint(ctx, 'links', book.md5)}")


@main.command()
@click.argument("record")
@json_option
@click.pass_context
def links(ctx, record, json_output):
    """List download sources; some require verification or login."""
    with prepare(ctx, json_output) as client:
        book = client.info(record)
    if ctx.obj["json"]:
        emit([{"index": i, **asdict(link)} for i, link in enumerate(book.links, 1)])
    else:
        for i, link in enumerate(book.links, 1):
            click.echo(f"{i}. [{link.kind}] {link.label}\n   {link.url}")
        if not book.links:
            click.echo("No download sources are available for this record.")
        else:
            sources = [
                (i, link)
                for i, link in enumerate(book.links, 1)
                if link.url.startswith(("http://", "https://"))
            ]
            selected = next((item for item in sources if item[1].kind != "fast"), None)
            if selected is None and sources:
                selected = sources[0]
            if selected:
                index, _ = selected
                click.echo(f"\nDownload using source {index}:")
                click.echo(f"  {command_hint(ctx, 'download', book.md5, '--source', index)}")
                click.echo(
                    "Change --source to another number from this list to choose that source."
                )
                click.echo(
                    "Free sources may count down before downloading; "
                    "--max-wait defaults to 300 seconds."
                )
            else:
                click.echo(
                    "Open these sources with a compatible app; anna download needs an HTTP(S) URL."
                )


@main.command()
@click.argument("target")
@click.option("--source", type=click.IntRange(min=1), help="Source index from anna links.")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output file path. Never overwrites existing files.",
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(path_type=Path),
    default=".",
    help="Destination directory when -o is not specified.",
)
@click.option("--md5", "expected_md5", help="Verify the MD5 of a direct URL download.")
@click.option(
    "--max-wait",
    type=click.IntRange(min=0),
    default=300,
    show_default=True,
    help="Maximum seconds for the free source countdown. Use 0 to fail immediately.",
)
@json_option
@click.pass_context
def download(ctx, target, source, output, directory, expected_md5, max_wait, json_output):
    """Download a record by MD5/URL, or a direct HTTP(S) file URL."""
    with prepare(ctx, json_output) as client:
        try:
            md5 = record_id(target)
        except AnnaError:
            md5 = None
        if md5:
            if expected_md5 and record_id(expected_md5) != md5:
                raise AnnaError("--md5 does not match the record MD5.")
            book = client.info(md5)
            if source:
                if source > len(book.links):
                    raise AnnaError(
                        f"Record has {len(book.links)} sources; --source is out of range."
                    )
                link = book.links[source - 1]
            else:
                link = next(
                    (
                        x
                        for x in book.links
                        if x.kind != "fast" and x.url.startswith(("http://", "https://"))
                    ),
                    None,
                )
            if not link:
                raise AnnaError(
                    "No regular HTTP download source; use anna links to inspect available sources."
                )
            target, expected_md5 = link.url, md5
        elif source:
            raise AnnaError("--source requires an MD5 or record URL.")
        interactive = not ctx.obj["json"] and sys.stderr.isatty()
        progress = DownloadProgress(interactive)
        countdown = DownloadCountdown(interactive)
        try:
            result = client.download(
                target,
                output,
                directory,
                expected_md5,
                progress=progress,
                max_wait=max_wait,
                wait_progress=countdown,
            )
        finally:
            countdown.close()
            progress.close()
    if ctx.obj["json"]:
        emit(result)
    else:
        click.echo(f"Saved: {result['path']}\nSize: {result['bytes']} bytes\nMD5: {result['md5']}")


@main.command()
@json_option
@click.pass_context
def doctor(ctx, json_output):
    """Check whether the mirror returns recognizable search results."""
    with prepare(ctx, json_output) as client:
        books = client.search("Pride and Prejudice", page=1)
        result = {"base_url": client.base_url, "ok": True, "results": len(books)}
    if ctx.obj["json"]:
        emit(result)
    else:
        click.echo(
            f"Mirror parsed successfully: {result['base_url']} ({result['results']} records)"
        )
