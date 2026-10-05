"""Keyboard-driven book browser; network and file work stay off the UI thread."""

import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Input,
    Label,
    OptionList,
    ProgressBar,
    Select,
)
from textual.worker import get_current_worker

from anna.client import DEFAULT_BASE_URL, Client, download_record
from anna.config import load_config, save_config
from anna.errors import (
    AnnaError,
    ChallengeError,
    DownloadCancelledError,
    DownloadSourcesError,
    RateLimitError,
)
from anna.gutenberg import ORIGIN as GUTENBERG_ORIGIN
from anna.gutenberg import download_edition, find_edition
from anna.library import history, open_saved
from anna.parsing import Book, author_matches, downloads_key, exact_matches
from anna.session import import_file, import_firefox, remember_origin


def book_summary(book):
    parts = [
        {"en": "English", "fr": "French", "de": "German"}.get(book.language, book.language),
        book.format.upper(),
        book.size,
    ]
    return " · ".join(part for part in parts if part) or book.metadata


def error_message(exc):
    if isinstance(exc, ChallengeError):
        return "Browser check needed. Press F2 for guided setup and a saved browser session."
    if isinstance(exc, httpx.TimeoutException):
        return "The file source timed out. Try again later or choose another edition."
    if isinstance(exc, httpx.HTTPError):
        return "Connection failed. Check your network and try again."
    if isinstance(exc, OSError):
        return f"Cannot save the file: {exc.strerror or type(exc).__name__}."
    return str(exc)


class ResponsiveScreen(ModalScreen):
    def on_resize(self, event):
        if isinstance(self.app, AnnaApp):
            self.app.update_layout(event.size)


class BrowserCheckScreen(ResponsiveScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, origin, target_url=None):
        super().__init__()
        self.origin = origin
        self.target_url = origin
        if target_url:
            base, target = urlsplit(origin), urlsplit(target_url)
            if (target.scheme, target.netloc) == (base.scheme, base.netloc):
                self.target_url = target_url

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("BROWSER CHECK", classes="section")
            yield Label(Text(self.origin), classes="heading")
            yield Label(
                "1. Open Firefox and finish the site's check there.\n"
                "2. Come back and choose Use Firefox session.\n"
                "3. Anna checks the session, then retries your last action if it works."
            )
            yield Label(
                "Only this site's cookies are imported into a private local file. "
                "They are reused until they expire. Changing network or browser settings "
                "can make the site ask again.",
                classes="muted",
            )
            with Horizontal(classes="actions"):
                yield Button("Open Firefox", id="open-browser", variant="primary")
                yield Button("Use Firefox session", id="reuse-browser")
            with Collapsible(title="Advanced / another browser", collapsed=True):
                yield Input(placeholder="Netscape cookies.txt file", id="session-file")
                yield Input(
                    placeholder="Browser User-Agent (optional for Firefox)", id="session-agent"
                )
                yield Button("Import session file once", id="import-session")
            yield Label("", id="connection-status")
            yield Button("Back", id="close-connection")

    @on(Button.Pressed, "#close-connection")
    def close_connection(self):
        self.dismiss(None)

    @on(Button.Pressed, "#open-browser")
    def open_browser(self):
        executable = shutil.which("firefox")
        if executable:
            subprocess.Popen(
                [executable, self.target_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            webbrowser.open(self.target_url)
        self.query_one("#connection-status", Label).update(
            "Finish the browser check, then return here. Anna will not solve it for you."
        )

    @on(Button.Pressed, "#reuse-browser")
    def reuse_browser(self):
        self.begin_import(True)

    @on(Button.Pressed, "#import-session")
    def import_session_file(self):
        self.begin_import(False)

    def begin_import(self, firefox):
        self.import_session(
            firefox,
            self.query_one("#session-agent", Input).value.strip(),
            self.query_one("#session-file", Input).value.strip(),
        )

    @work(thread=True, exclusive=True)
    def import_session(self, firefox, agent, path):
        worker = get_current_worker()
        try:
            if firefox:
                agent = import_firefox(self.origin, agent or None)
            else:
                if not path or not agent:
                    raise AnnaError("Choose a session file and enter that browser's User-Agent.")
                import_file(self.origin, Path(path).expanduser(), agent)
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.connection_error, "Checking whether Anna can reuse this session…"
                )
                with Client(self.origin, timeout=15, user_agent=agent) as client:
                    try:
                        client.verify_session(self.target_url)
                    except ChallengeError as exc:
                        raise AnnaError(
                            "The site still blocks Anna with this browser session. "
                            "If this exact page works in Firefox, repeating the check or "
                            "import will not necessarily help. You can download in Firefox; "
                            "Anna has not retried the blocked download."
                        ) from exc
            if not worker.is_cancelled:
                remember_origin(self.origin)
                self.app.call_from_thread(self.dismiss, {"cookies": None, "user_agent": agent})
        except (AnnaError, OSError, subprocess.SubprocessError, httpx.HTTPError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.connection_error, error_message(exc))

    def connection_error(self, message):
        self.query_one("#connection-status", Label).update(Text(message))


def source_description(book):
    description = book.description
    if description.startswith("description "):
        description = description.removeprefix("description ").split("Alternative filename")[0]
    elif description.startswith("Alternative "):
        description = ""
    description = description.strip()
    if len(description) > 500:
        description = description[:500].rsplit(" ", 1)[0] + "…"
    return description or "No synopsis supplied for this edition."


class EditionPane(VerticalScroll):
    """Shared edition presentation for the preview and narrow-screen drill-down."""

    def compose(self) -> ComposeResult:
        yield Label("SELECTED EDITION", classes="section")
        yield Label("Choose a book to see its details.", id="book-title", classes="heading")
        yield Label("", id="book-author")
        yield Label("", id="book-statistics", classes="statistics")
        yield Label("", id="book-publisher")
        yield Label("", id="book-metadata")
        yield Label("", id="book-filename", classes="muted")
        yield Label("", id="book-destination", classes="muted")
        yield Label("", id="book-error", classes="error")
        yield Label("", id="official-status", classes="muted")
        yield Button(
            "Download official EPUB", id="official-download", variant="default", disabled=True
        )
        yield Button("Download archive file", id="download-book", variant="primary", disabled=True)
        yield Label("", id="book-description")

    def on_mount(self):
        self.query_one("#official-download", Button).display = False
        self.query_one("#download-book", Button).display = False
        for label in self.query(Label):
            if label.id and label.id != "book-title":
                label.display = False

    def show_book(self, book, directory, statistics=None):
        for label in self.query(Label):
            label.display = True
        self.query_one("#download-book", Button).display = bool(book.md5 or book.source_id)
        self.query_one("#book-title", Label).update(Text(book.title))
        self.query_one("#book-author", Label).update(Text(book.author or "Unknown author"))
        self.query_one("#book-publisher", Label).update(Text(book.publisher))
        self.query_one("#book-metadata", Label).update(Text(book_summary(book)))
        self.query_one("#book-filename", Label).update(Text(book.filename))
        self.query_one("#book-filename", Label).display = bool(book.filename)
        self.query_one("#book-description", Label).update(Text(source_description(book)))
        self.query_one("#book-destination", Label).update(Text(f"Save to {directory}"))
        statistics = statistics or {}
        count = statistics.get("downloads_total")
        summary = (
            f"{count:,} downloads · Anna edition"
            if count is not None
            else "Download count unavailable"
        )
        for key, label in (
            ("lists_count", "saved lists"),
            ("reports_count", "reported file issues"),
        ):
            if key in statistics:
                summary += f" · {statistics[key]:,} {label}"
        self.query_one("#book-statistics", Label).update(Text(summary))
        self.query_one("#book-error", Label).update("")
        self.query_one("#book-error", Label).display = False
        self.query_one("#download-book", Button).disabled = not any(
            link.kind != "fast" and link.url.startswith(("http://", "https://"))
            for link in book.links
        )
        self.show_official(None)

    def show_official(self, book):
        button = self.query_one("#official-download", Button)
        button.display = book is not None
        button.disabled = book is None
        archive = self.query_one("#download-book", Button)
        archive.label = "Download archive file"
        archive.variant = "primary"
        label = self.query_one("#official-status", Label)
        if book is None:
            label.update("")
            return
        count = f" · {book.downloads:,} downloads in 30 days" if book.downloads is not None else ""
        label.update(
            Text(
                f"Official alternative: Gutenberg #{book.source_id}{count}\n"
                "A separate illustrated EPUB · EPUB integrity checked after download."
            )
        )


def optional_statistics(client, book):
    if not hasattr(client, "statistics"):
        return {}
    try:
        return client.statistics(book.md5)
    except (RateLimitError, ChallengeError):
        raise
    except (AnnaError, httpx.HTTPError, ValueError):
        return {}


def optional_official(client, book):
    if not hasattr(client, "http"):
        return None
    try:
        return find_edition(client, book)
    except (AnnaError, httpx.HTTPError, ValueError):
        return None


def lookup_official(factory, options, book):
    connection: dict[str, Any] = dict(options)
    connection["timeout"] = 10
    try:
        with factory(**connection) as client:
            return optional_official(client, book)
    except (AnnaError, httpx.HTTPError, OSError):
        return None


class SettingsScreen(ResponsiveScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, preferences):
        super().__init__()
        self.preferences = preferences

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-panel", classes="dialog"):
            yield Label("Your preferences", classes="heading")
            yield Label("Language code (for example en, fr or de)")
            yield Input(self.preferences["language"], id="setting-language")
            yield Label("Default format (for example epub or pdf)")
            yield Input(self.preferences["format"], id="setting-format")
            yield Label("Download folder")
            yield Input(self.preferences["directory"], id="setting-directory")
            yield Label("", id="settings-error")
            with Horizontal(classes="actions"):
                yield Button("Save", id="save-settings", variant="primary")
                yield Button("Back", id="close-settings")

    @on(Button.Pressed, "#close-settings")
    def close_settings(self):
        self.dismiss(None)

    @on(Button.Pressed, "#save-settings")
    def save_settings(self):
        values = {
            key: self.query_one(f"#setting-{key}", Input).value.strip()
            for key in ("language", "format", "directory")
        }
        if not all(values.values()):
            self.query_one("#settings-error", Label).update("Fill in all three preferences.")
            return
        try:
            save_config(values)
        except (AnnaError, OSError) as exc:
            self.query_one("#settings-error", Label).update(Text(error_message(exc)))
            return
        self.dismiss(values)


class BookScreen(ResponsiveScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(
        self,
        book,
        options,
        directory,
        client_factory,
        cached=None,
        preload_error=None,
        waiting=False,
    ):
        super().__init__()
        self.cached = cached
        self.preload_error = preload_error
        self.waiting = waiting
        self.book = book
        self.options = dict(options)
        self.directory = directory
        self.client_factory = client_factory
        self.ready = False
        self.loaded = False
        self.official = None
        self.verification_origin = None

    def compose(self) -> ComposeResult:
        with Vertical(id="book-panel", classes="dialog"):
            yield EditionPane(id="book-copy")
            with Horizontal(classes="actions"):
                yield Button("Back", id="close-book")

    def on_mount(self):
        self.query_one(EditionPane).show_book(self.book, self.directory)
        self.query_one("#download-book", Button).disabled = True
        if self.cached is not None:
            self.show_details(*self.cached)
        elif self.preload_error:
            self.show_error(*self.preload_error)
        elif self.waiting:
            self.query_one("#book-description", Label).update("Loading edition details…")
        else:
            self.load_details()

    @work(thread=True, exclusive=True)
    def load_details(self):
        worker = get_current_worker()
        try:
            with self.client_factory(**self.options) as client:
                book = client.info(self.book.md5)
                statistics = optional_statistics(client, book)
            if not worker.is_cancelled:
                self.app.call_from_thread(self.show_details, book, statistics)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.show_error,
                    error_message(exc),
                    exc.origin if isinstance(exc, AnnaError) else None,
                )

    def show_details(self, book, statistics=None):
        self.loaded = True
        self.book = book
        self.ready = any(
            link.kind != "fast" and link.url.startswith(("http://", "https://"))
            for link in book.links
        )
        self.query_one(EditionPane).show_book(book, self.directory, statistics)
        if not self.ready:
            self.show_error("No free HTTP download source is available for this edition.")
        self.update_download_button()
        self.load_official()

    @work(thread=True, exclusive=True, group="official")
    def load_official(self):
        worker = get_current_worker()
        official = lookup_official(self.client_factory, self.options, self.book)
        if not worker.is_cancelled:
            self.app.call_from_thread(self.show_official, official)

    def show_official(self, official):
        self.official = official
        self.query_one(EditionPane).show_official(official)

    @on(Button.Pressed, "#official-download")
    def download_official(self, event):
        event.stop()
        if self.official is not None:
            self.dismiss(self.official)

    def show_error(self, message, origin=None):
        self.verification_origin = origin
        if not self.loaded:
            self.query_one("#book-description", Label).update("Could not load edition details.")
        self.query_one("#book-error", Label).update(Text(message))
        self.query_one("#book-error", Label).display = True

    def update_download_button(self):
        self.query_one("#download-book", Button).disabled = not self.ready

    @on(Button.Pressed, "#close-book")
    def close_book(self):
        self.dismiss(None)

    @on(Button.Pressed, "#download-book")
    def download_book(self, event):
        event.stop()
        if self.ready:
            self.dismiss(self.book)


class HistoryScreen(ResponsiveScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label("DOWNLOAD HISTORY", classes="heading")
            yield OptionList(id="history-results")
            yield Label("Loading local receipts…", id="history-status")
            with Horizontal(classes="actions"):
                yield Button("Open book", id="history-open", disabled=True)
                yield Button("Show folder", id="history-folder", disabled=True)
                yield Button("Back", id="history-back")

    def on_mount(self):
        self.receipts = []
        self.load_history()

    @work(thread=True)
    def load_history(self):
        worker = get_current_worker()
        try:
            receipts = history()
            if not worker.is_cancelled:
                self.app.call_from_thread(self.show_history, receipts)
        except (AnnaError, OSError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.query_one("#history-status", Label).update, str(exc))

    def show_history(self, receipts):
        self.receipts = receipts
        for item in receipts:
            self.query_one("#history-results", OptionList).add_option(
                Text(
                    f"{item['title']} — {item['author']}\n"
                    f"{item['source']} · {item['saved_at']} UTC · {item['bytes']:,} bytes\n"
                    f"{item['path']}\n"
                )
            )
        self.query_one("#history-status", Label).update(
            "Local receipts; files are checked again before reusing a download."
            if receipts
            else "No downloads yet. Successfully saved editions appear here."
        )
        if receipts:
            self.query_one("#history-results", OptionList).highlighted = 0

    @on(OptionList.OptionHighlighted, "#history-results")
    def highlight_receipt(self):
        self.query_one("#history-open", Button).disabled = False
        self.query_one("#history-folder", Button).disabled = False

    @on(Button.Pressed, "#history-back")
    def go_back(self):
        self.dismiss()

    @on(Button.Pressed, "#history-open")
    @on(Button.Pressed, "#history-folder")
    def launch_receipt(self, event):
        event.stop()
        index = self.query_one("#history-results", OptionList).highlighted
        if index is not None and index < len(self.receipts):
            self.launch(self.receipts[index]["path"], event.button.id == "history-folder")

    @work(thread=True)
    def launch(self, path, folder):
        try:
            open_saved(path, folder)
        except AnnaError as exc:
            self.app.call_from_thread(self.query_one("#history-status", Label).update, str(exc))


class DownloadScreen(ResponsiveScreen):
    BINDINGS = [("escape", "cancel", "Cancel / back")]

    def __init__(self, book, options, directory, client_factory):
        super().__init__()
        self.book = book
        self.options = dict(options)
        self.directory = directory
        self.client_factory = client_factory
        self.saved_path = None
        self.cancel_event = threading.Event()
        self.running = True
        self.bytes_received = 0
        self.total = None
        self.source_index = 1
        self.source_count = 1
        self.last_update = 0.0
        self.official = None
        self.verification_origin = None

    def compose(self) -> ComposeResult:
        with Vertical(id="download-panel", classes="dialog"):
            yield Label(Text(self.book.title), classes="heading")
            yield Label(Text(book_summary(self.book)), id="download-edition")
            yield Label("Preparing download…", id="download-status")
            yield ProgressBar(total=None, show_eta=False, id="progress")
            yield Label(Text(f"Save to: {self.directory}"))
            yield Button("Cancel", id="cancel-download")
            with Horizontal(classes="actions", id="saved-actions"):
                yield Button("Open book", id="open-saved")
                yield Button("Show folder", id="show-saved")
            yield Label("", id="official-status")
            yield Button(
                "Download official Gutenberg edition", id="official-download", disabled=True
            )

    def on_mount(self):
        self.query_one("#official-download", Button).display = False
        self.query_one("#saved-actions").display = False
        self.transfer()

    @on(Button.Pressed, "#official-download")
    def use_official(self, event):
        event.stop()
        if self.official is None or self.running:
            return
        self.book = self.official
        self.running = True
        self.cancel_event.clear()
        self.query_one("#official-download", Button).display = False
        self.query_one("#official-status", Label).update("Official Gutenberg edition selected.")
        self.query_one("#download-edition", Label).update("Project Gutenberg · illustrated EPUB")
        self.query_one("#cancel-download", Button).label = "Cancel"
        self.query_one("#progress", ProgressBar).display = True
        self.start_source(1, 1)
        self.transfer()

    @work(thread=True, exclusive=True, group="official")
    def discover_official(self):
        worker = get_current_worker()
        official = lookup_official(self.client_factory, self.options, self.book)
        if not worker.is_cancelled:
            self.app.call_from_thread(self.offer_official, official)

    def offer_official(self, official):
        self.official = official
        self.query_one("#official-status", Label).update(
            Text(
                f"Alternative: Project Gutenberg #{official.source_id}.\n"
                "A separate edition; choosing it checks EPUB integrity "
                "instead of the Anna checksum."
                if official is not None
                else "No official Gutenberg alternative is available right now."
            )
        )
        button = self.query_one("#official-download", Button)
        button.disabled = official is None
        button.display = official is not None

    def saved(self, result):
        self.saved_path = result["path"]
        self.bytes_received = result["bytes"]
        self.finish(
            (
                "Already downloaded; local checksum verified: "
                if result.get("already_downloaded")
                else (
                    "Official EPUB saved and integrity checked: "
                    if self.book.source == "gutenberg"
                    else "Saved and checksum verified: "
                )
            )
            + result["path"]
            + ("\nHistory could not be saved." if result.get("history_warning") else ""),
            True,
        )
        self.query_one("#saved-actions").display = True

    @on(Button.Pressed, "#open-saved")
    @on(Button.Pressed, "#show-saved")
    def open_download(self, event):
        event.stop()
        if self.saved_path:
            self.launch_saved(self.saved_path, event.button.id == "show-saved")

    @work(thread=True)
    def launch_saved(self, path, folder):
        try:
            open_saved(path, folder)
        except AnnaError as exc:
            self.app.call_from_thread(self.query_one("#download-status", Label).update, str(exc))

    def on_unmount(self):
        self.cancel_event.set()

    def action_cancel(self):
        if self.running:
            self.cancel_event.set()
            self.query_one("#download-status", Label).update(
                "Cancelling… waiting for the current network operation."
            )
            self.query_one("#cancel-download", Button).disabled = True
        else:
            self.dismiss(None)

    @on(Button.Pressed, "#cancel-download")
    def cancel_download(self):
        self.action_cancel()

    def start_source(self, index, total):
        self.source_index = index
        self.source_count = total
        self.bytes_received = 0
        self.last_update = 0.0
        self.total = None
        self.query_one("#progress", ProgressBar).update(total=None, progress=0)
        self.query_one("#progress", ProgressBar).display = False
        self.show_request("Requesting download link…")

    def show_request(self, phase):
        if not self.cancel_event.is_set():
            self.query_one("#download-status", Label).update(
                (
                    "Project Gutenberg · "
                    if self.book.source == "gutenberg"
                    else f"Download route {self.source_index}/{self.source_count} · "
                )
                + phase
            )

    def set_total(self, total):
        self.total = total
        self.query_one("#progress", ProgressBar).update(total=total, progress=0)

    def show_progress(self, size):
        if self.cancel_event.is_set():
            return
        self.query_one("#progress", ProgressBar).display = True
        self.query_one("#progress", ProgressBar).update(progress=size)
        amount = f"{size / 1048576:.2f} MiB"
        if self.total:
            amount += f" / {self.total / 1048576:.2f} MiB"
        self.query_one("#download-status", Label).update("Downloading · " + amount)

    def show_wait(self, seconds):
        if not self.cancel_event.is_set():
            self.query_one("#download-status", Label).update(
                f"Download route {self.source_index}/{self.source_count} · {seconds}s remaining"
                if seconds
                else "Countdown complete · requesting download link…"
            )

    def finish(self, message, success):
        self.running = False
        self.query_one("#download-status", Label).update(Text(message))
        if success:
            bar = self.query_one("#progress", ProgressBar)
            bar.update(total=self.bytes_received, progress=self.bytes_received)
        else:
            self.query_one("#progress", ProgressBar).display = False
        button = self.query_one("#cancel-download", Button)
        button.label = "Back to results"
        button.disabled = False
        button.focus()

    def transfer_error(self, message, origin, target_url=None):
        self.verification_origin = origin
        self.verification_url = target_url
        self.finish(message, False)

    def retry_transfer(self, options):
        self.options = dict(options)
        self.running = True
        self.cancel_event.clear()
        self.query_one("#cancel-download", Button).label = "Cancel"
        self.query_one("#progress", ProgressBar).display = True
        self.start_source(1, 1)
        self.transfer()

    @work(thread=True)
    def transfer(self):
        def progress(amount):
            self.bytes_received += amount
            now = time.monotonic()
            if now - self.last_update >= 0.1:
                self.app.call_from_thread(self.show_progress, self.bytes_received)
                self.last_update = now

        try:
            connection: dict[str, Any] = dict(self.options)
            if self.book.source == "gutenberg":
                connection["base_url"] = GUTENBERG_ORIGIN
            with self.client_factory(**connection) as client:
                # Resolve sources on the active mirror, not an earlier search session.
                book = self.book if self.book.source == "gutenberg" else client.info(self.book.md5)
                options: dict[str, Any] = dict(
                    request_progress=lambda phase: self.app.call_from_thread(
                        self.show_request, phase
                    ),
                    directory=self.directory,
                    progress=progress,
                    wait_progress=lambda seconds: self.app.call_from_thread(
                        self.show_wait, seconds
                    ),
                    cancelled=self.cancel_event.is_set,
                    total_progress=lambda total: self.app.call_from_thread(self.set_total, total),
                )
                if book.source == "gutenberg":
                    result = download_edition(client, book, **options)
                else:
                    result = download_record(
                        client,
                        book,
                        source_progress=lambda i, total: self.app.call_from_thread(
                            self.start_source, i, total
                        ),
                        **options,
                    )
            self.app.call_from_thread(self.saved, result)
        except DownloadCancelledError:
            self.app.call_from_thread(
                self.finish, "Cancelled. No unfinished book was saved.", False
            )
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            self.app.call_from_thread(
                self.transfer_error,
                error_message(exc),
                exc.origin if isinstance(exc, AnnaError) else None,
                exc.verification_url if isinstance(exc, AnnaError) else None,
            )
            if isinstance(exc, DownloadSourcesError):
                self.app.call_from_thread(self.discover_official)


class AnnaApp(App):
    TITLE = "Anna · Bookfinder"
    BINDINGS = [
        Binding("/", "search", "Search"),
        Binding("ctrl+s", "settings", "Settings"),
        Binding("f2", "browser_check", "Browser check"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "search", "Back to search"),
    ]
    CSS = """
    Screen { background: $background; color: $text; }
    #browser { width: 100%; height: 1fr; padding: 0; margin: 0; }
    #brand { width: 100%; height: 2; padding: 0 2; background: $panel;
             color: $accent; text-style: bold; }
    #search-row { height: 3; padding: 0 2; }
    #query { width: 1fr; }
    #search-tools { height: 3; padding: 0 2; }
    #sort { width: 1fr; }
    #exact { width: auto; height: 3; }
    .compact #search-tools { height: 6; layout: grid; grid-size: 2;
        grid-columns: 1fr 1fr; grid-rows: 3 3; }
    #history-results { height: 1fr; }
    #filters { height: 3; padding: 0 2; }
    #author { width: 1fr; }
    #language { width: 14; margin: 0 1; }
    #format { width: 18; }
    #search, #settings { min-width: 10; margin-left: 1; }
    #body { height: 1fr; width: 100%; }
    #list-pane { width: 45%; padding: 1 2; }
    #preview { width: 1fr; padding: 1 3; background: $surface; }
    #results { height: 1fr; margin: 0; padding: 0; border: none; background: transparent; }
    #results:focus { border: none; }
    OptionList > .option-list--option-highlighted {
        background: $primary; color: $text; text-style: bold; }
    #status { width: 100%; height: auto; min-height: 1; padding: 0 2; color: $text-muted; }
    .compact #list-pane { width: 100%; }
    .compact #preview { display: none; }
    .tiny #browser { display: none; }
    #too-small { display: none; height: auto; padding: 1; }
    .tiny #too-small { display: block; }
    ModalScreen { align: left top; background: $background; }
    .dialog { width: 100%; height: 1fr; padding: 1 2; border: none; background: $surface; }
    .dialog Label, EditionPane Label { width: 100%; height: auto; }
    .heading { text-style: bold; color: $text; margin-bottom: 1; }
    .section { color: $accent; text-style: bold; margin-bottom: 1; }
    .statistics { color: $accent; margin: 1 0; }
    .muted { color: $text-muted; margin-top: 1; }
    #book-description { margin: 1 0; }
    #download-book { margin-top: 1; }
    EditionPane Button { height: 3; }
    #official-download { margin-top: 1; }
    .actions { height: auto; margin-top: 1; }
    .actions Button { margin-right: 1; }
    #book-copy { height: 1fr; }
    #book-error, #settings-error { color: $error; height: auto; }
    DownloadScreen { align: left bottom; background: transparent; }
    #download-panel { height: auto; max-height: 100%; overflow-y: auto; background: $panel; }
    #download-panel .heading { margin: 0; color: $accent; }
    #progress { margin: 0; }
    #download-status { height: auto; }
    Input { height: 3; border: solid $primary; background: $panel; padding: 0 1; }
    Select { height: 3; border: none; background: $panel; }
    SelectCurrent { border: solid $primary; background: $panel; }
    Input:focus { border: solid $accent; background: $panel; }
    Button { height: 3; border: round $accent; background: $panel;
             content-align: center middle; text-style: bold; }
    Button.-primary { background: $primary; }
    Button:hover, Button:focus { background: $primary; border: round $secondary; }
    Button:disabled { border: round $primary; color: $text-muted; text-style: none; }
    #download-panel Button { height: 3; margin: 0; }
    #cancel-download { min-width: 24; }
    Footer { background: $panel; }
    """

    def __init__(
        self, client_options=None, preferences=None, client_factory=Client, connect_only=False
    ):
        super().__init__()
        self.client_options: dict[str, Any] = dict(client_options or {})
        self.preferences = dict(preferences or load_config())
        self.client_factory = client_factory
        self.books = []
        self.search_spec = None
        self.search_page = 0
        self.search_busy = False
        self.detail_cache = {}
        self.detail_errors = {}
        self.detail_queue = []
        self.detail_loading = set()
        self.detail_epoch = 0
        self.official_cache = {}
        self.selected_book = None
        self.preview_timer = None
        self.verification_origin = None
        self.connect_only = connect_only
        self.register_theme(
            Theme(
                name="bookfinder",
                primary="#304b70",
                secondary="#a9c9ff",
                accent="#a9c9ff",
                foreground="#e4eaf1",
                background="#11171f",
                surface="#18212d",
                panel="#202a38",
                success="#8dddbb",
                error="#ffbdad",
                warning="#eab98b",
                dark=True,
            )
        )
        self.theme = "bookfinder"

    def compose(self) -> ComposeResult:
        formats = list(dict.fromkeys(["epub", "pdf", "mobi", "txt", self.preferences["format"]]))
        with Vertical(id="browser"):
            yield Label("ANNA   /   Bookfinder", id="brand")
            with Horizontal(id="search-row"):
                yield Input(placeholder="Search a title or keyword…", id="query")
                yield Button("Search", id="search", variant="primary")
                yield Button("Settings", id="settings")
            with Horizontal(id="filters"):
                yield Input(placeholder="Author (optional)", id="author")
                yield Input(self.preferences["language"], placeholder="Language", id="language")
                yield Select(
                    [(item.upper(), item) for item in formats] + [("Any format", "*")],
                    value=self.preferences["format"],
                    allow_blank=False,
                    id="format",
                )
            with Horizontal(id="search-tools"):
                yield Select(
                    [
                        ("Relevance", "relevance"),
                        ("Smallest file", "smallest"),
                        ("Largest file", "largest"),
                        ("Newest publication", "newest"),
                        ("Oldest publication", "oldest"),
                        ("Recently added", "newest_added"),
                        ("Most downloaded", "downloads"),
                    ],
                    value="relevance",
                    allow_blank=False,
                    id="sort",
                )
                yield Checkbox(
                    "Exact terms",
                    id="exact",
                    tooltip="Require every word and number in the title or original filename",
                )
                yield Button("Load more", id="load-more", disabled=True)
                yield Button("History", id="history")
            yield Label("Search → choose an edition → download to your Books folder.", id="status")
            with Horizontal(id="body"):
                with Vertical(id="list-pane"):
                    yield Label("EDITIONS", classes="section")
                    yield OptionList(id="results")
                yield EditionPane(id="preview")
        yield Label(
            "Terminal too small — resize to at least 48 × 18. Ctrl+Q quits.", id="too-small"
        )
        yield Footer()

    def on_mount(self):
        self.update_layout()
        self.query_one("#query", Input).focus()
        if self.connect_only:
            self.action_browser_check()

    def on_resize(self, event):
        self.update_layout(event.size)

    def update_layout(self, size=None):
        size = size or self.size
        root = self.screen_stack[0]
        root.set_class(size.width < 100 or size.height < 28, "compact")
        root.set_class(size.width < 48 or size.height < 18, "tiny")

    def action_search(self):
        if len(self.screen_stack) == 1:
            self.query_one("#query", Input).focus()

    def action_quit(self):
        if isinstance(self.screen, DownloadScreen) and self.screen.running:
            self.screen.action_cancel()
            return
        self.exit()

    def action_settings(self):
        if len(self.screen_stack) == 1:
            self.push_screen(SettingsScreen(self.preferences), self.settings_saved)

    def action_browser_check(self):
        parent = self.screen
        if isinstance(parent, BrowserCheckScreen):
            return
        if isinstance(parent, DownloadScreen) and parent.running:
            self.notify("Cancel the current transfer before opening a browser check.")
            return
        origin = (
            getattr(parent, "verification_origin", None)
            or self.verification_origin
            or self.client_options.get("base_url")
            or DEFAULT_BASE_URL
        )
        self.push_screen(
            BrowserCheckScreen(origin, getattr(parent, "verification_url", None)),
            lambda result: self.session_ready(result, parent),
        )

    def session_ready(self, options, parent):
        if options is None:
            if self.connect_only:
                self.exit()
            return
        self.client_options.update(options)
        self.detail_cache.clear()
        self.detail_errors.clear()
        self.official_cache.clear()
        self.notify("Browser session saved. It will be reused automatically.")
        if self.connect_only:
            self.exit()
        elif isinstance(parent, BookScreen):
            parent.options.update(options)
            parent.load_details()
        elif isinstance(parent, DownloadScreen):
            parent.retry_transfer(self.client_options)
        elif parent is self.screen and self.query_one("#query", Input).value.strip():
            self.submit_search()

    def settings_saved(self, preferences):
        if preferences is None:
            return
        self.preferences = preferences
        self.query_one("#language", Input).value = preferences["language"]
        select = self.query_one("#format", Select)
        formats = list(dict.fromkeys(["epub", "pdf", "mobi", "txt", preferences["format"]]))
        select.set_options([(item.upper(), item) for item in formats] + [("Any format", "*")])
        select.value = preferences["format"]
        self.notify("Preferences saved.")

    @on(Button.Pressed, "#settings")
    def open_settings(self):
        self.action_settings()

    @on(Input.Submitted)
    @on(Button.Pressed, "#search")
    def submit_search(self):
        query = self.query_one("#query", Input).value.strip()
        if not query:
            self.query_one("#status", Label).update("Enter a title or keyword to search.")
            return
        author = self.query_one("#author", Input).value.strip()
        language = self.query_one("#language", Input).value.strip()
        book_format = self.query_one("#format", Select).value
        self.books = []
        self.query_one("#results", OptionList).clear_options()
        self.selected_book = None
        if self.preview_timer is not None:
            self.preview_timer.stop()
        self.query_one("#preview", EditionPane).show_book(
            Book("", "Searching…", ""), self.preferences["directory"]
        )
        self.query_one("#status", Label).update("Searching…")
        self.search_page = 0
        self.search_spec = (
            query,
            author,
            language,
            str(book_format),
            str(self.query_one("#sort", Select).value),
            self.query_one("#exact", Checkbox).value,
        )
        self.fetch_search(1)

    @on(Button.Pressed, "#load-more")
    def more_results(self):
        if self.search_spec and not self.search_busy:
            self.fetch_search(self.search_page + 1)

    @on(Select.Changed, "#format")
    @on(Select.Changed, "#sort")
    def sort_changed(self, event):
        if self.search_spec:
            if event.select.id == "sort" and event.value == "downloads":
                self.search_spec = (*self.search_spec[:4], "downloads", self.search_spec[5])
                self.refresh_downloads_order()
            else:
                self.submit_search()

    @on(Checkbox.Changed, "#exact")
    def exact_changed(self):
        if self.search_spec:
            self.submit_search()

    def fetch_search(self, page):
        if self.search_spec is None:
            return
        self.detail_epoch += 1
        self.workers.cancel_group(self, "details")
        self.detail_loading.clear()
        self.detail_queue.clear()
        self.search_busy = True
        self.query_one("#load-more", Button).disabled = True
        self.query_one("#status", Label).update(f"Searching page {page}…")
        self.search_books(*self.search_spec, page)

    @work(thread=True, exclusive=True, group="search")
    def search_books(self, query, author, language, book_format, sort, exact, page):
        worker = get_current_worker()
        try:
            with self.client_factory(**self.client_options) as client:
                books = client.search(
                    query + (" " + author if author else ""),
                    lang=() if language in ("", "*") else (language,),
                    ext=() if book_format == "*" else (book_format,),
                    page=page,
                    sort="" if sort in {"relevance", "downloads"} else sort,
                )
                has_results = bool(books)
                if author:
                    books = [book for book in books if author_matches(book.author, author)]
                if exact:
                    books = [book for book in books if exact_matches(book, query)]
                books = list({book.md5: book for book in books}.values())
                if not worker.is_cancelled:
                    self.call_from_thread(self.accept_page, books, page, has_results)

        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.call_from_thread(
                    self.show_search_error,
                    error_message(exc),
                    exc.origin if isinstance(exc, AnnaError) else None,
                )

    def show_search_error(self, message, origin=None):
        self.search_busy = False
        self.query_one("#load-more", Button).disabled = self.search_spec is None
        self.verification_origin = origin
        self.query_one("#status", Label).update(Text(message))
        self.start_background_details()

    def accept_page(self, books, page, has_results):
        for book in books:
            self.detail_errors.pop(book.md5, None)
        self.search_finished(books, page, has_results)
        self.refresh_downloads_order()
        self.start_background_details()

    def start_background_details(self):
        self.detail_queue = [
            book
            for book in self.books
            if book.md5 not in self.detail_cache and book.md5 not in self.detail_errors
        ]
        self.detail_loading = {book.md5 for book in self.detail_queue}
        if self.detail_queue:
            self.fetch_details(self.detail_epoch)

    def next_detail(self, epoch):
        if epoch != self.detail_epoch or not self.detail_queue:
            return None
        selected = self.selected_book
        index = next(
            (
                i
                for i, book in enumerate(self.detail_queue)
                if selected is not None and book.md5 == selected.md5
            ),
            0,
        )
        return self.detail_queue.pop(index)

    @work(thread=True, exclusive=True, group="details")
    def fetch_details(self, epoch):
        worker = get_current_worker()
        try:
            self.load_background_details(epoch)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.call_from_thread(
                    self.background_failed, epoch, error_message(exc), getattr(exc, "origin", None)
                )

    def background_failed(self, epoch, message, origin):
        if epoch != self.detail_epoch:
            return
        for md5 in list(self.detail_loading):
            self.detail_ready(epoch, md5, None, (message, origin))
        self.detail_queue.clear()

    def load_background_details(self, epoch):
        worker = get_current_worker()
        stopped = None
        with self.client_factory(**self.client_options) as client:
            configured = client.http.timeout if isinstance(client, Client) else None
            while not worker.is_cancelled:
                selected = self.call_from_thread(self.next_detail, epoch)
                if selected is None:
                    return
                cached = None
                error = None
                if stopped is not None:
                    error = stopped
                else:
                    if isinstance(client, Client) and configured is not None:
                        client.http.timeout = httpx.Timeout(
                            **{
                                name: min(value, 10) if value is not None else 10
                                for name, value in configured.as_dict().items()
                            }
                        )
                    try:
                        book = client.info(selected.md5)
                        cached = (book, {})
                        if not worker.is_cancelled:
                            cached = (book, optional_statistics(client, book))
                    except (AnnaError, httpx.HTTPError, OSError) as exc:
                        error = (error_message(exc), getattr(exc, "origin", None))
                        if isinstance(exc, (RateLimitError, ChallengeError)):
                            stopped = error
                if not worker.is_cancelled:
                    self.call_from_thread(self.detail_ready, epoch, selected.md5, cached, error)

    def detail_ready(self, epoch, md5, cached, error):
        if epoch != self.detail_epoch:
            return
        self.detail_loading.discard(md5)
        if error:
            self.detail_errors[md5] = error
        if cached is not None:
            self.show_preview(md5, *cached)
            index = next((i for i, book in enumerate(self.books) if book.md5 == md5), None)
            if index is not None:
                self.query_one("#results", OptionList).replace_option_prompt_at_index(
                    index, self.result_label(self.books[index])
                )
        elif error:
            self.preview_error(md5, *error)
        if isinstance(self.screen, BookScreen) and self.screen.book.md5 == md5:
            if cached is not None:
                self.screen.show_details(*cached)
            elif error:
                self.screen.show_error(*error)
        self.refresh_downloads_order()
        if not self.search_busy:
            remaining = len(self.detail_loading)
            unavailable = sum(book.md5 in self.detail_errors for book in self.books)
            self.query_one("#status", Label).update(
                f"{len(self.books)} editions · "
                + (
                    f"Loading details in background · {remaining} remaining"
                    if remaining
                    else "Details loaded · ↑↓ browse"
                )
                + (f" · {unavailable} unavailable" if unavailable else "")
            )

    def refresh_downloads_order(self):
        if self.query_one("#sort", Select).value != "downloads" or not self.books:
            return
        selected = self.selected_book.md5 if self.selected_book else self.books[0].md5
        statistics = {md5: cached[1] for md5, cached in self.detail_cache.items()}
        ordered = sorted(self.books, key=lambda book: downloads_key(book, statistics))
        if ordered == self.books:
            return
        self.books = ordered
        results = self.query_one("#results", OptionList)
        results.clear_options()
        results.add_options([self.result_label(book) for book in ordered])
        results.highlighted = next((i for i, book in enumerate(ordered) if book.md5 == selected), 0)

    def search_finished(self, books, page, has_results):
        self.search_busy = False
        self.search_page = page
        highlighted = self.query_one("#results", OptionList).highlighted
        previous = list(self.books) if page > 1 else []
        seen = {book.md5 for book in previous}
        combined = previous
        for book in books:
            if book.md5 not in seen:
                combined.append(book)
                seen.add(book.md5)
        self.show_results(combined)
        if page > 1 and highlighted is not None and combined:
            self.query_one("#results", OptionList).highlighted = highlighted
        self.query_one("#load-more", Button).disabled = not has_results
        self.query_one("#status", Label).update(
            ("No matching books · " if not combined else f"{len(combined)} editions · ")
            + f"page {page} · "
            + ("Load more to keep searching" if has_results else "No further results")
        )

    @on(Button.Pressed, "#history")
    def open_history(self):
        self.push_screen(HistoryScreen())

    def result_label(self, book):
        label = Text()
        label.append(book.title, style="bold")
        label.append(f"\n{book.author or 'Unknown author'}")
        summary = book_summary(book)
        cached = self.detail_cache.get(book.md5)
        if cached and "downloads_total" in cached[1]:
            summary += f" · {cached[1]['downloads_total']:,} downloads"
        label.append(f"\n{summary}\n")
        return label

    def show_results(self, books):
        self.books = books
        results = self.query_one("#results", OptionList)
        results.clear_options()
        for book in books:
            results.add_option(self.result_label(book))
        self.query_one("#status", Label).update(
            f"{len(books)} editions · ↑↓ browse · Enter opens details"
            if books
            else "No matching books. Try fewer keywords or Any format."
        )
        if books:
            results.highlighted = 0
            results.focus()
        else:
            self.query_one("#preview", EditionPane).show_book(
                Book("", "No matching editions", ""), self.preferences["directory"]
            )

    @on(OptionList.OptionHighlighted, "#results")
    def highlight_book(self, event):
        if event.option_index >= len(self.books):
            return
        self.selected_book = self.books[event.option_index]
        pane = self.query_one("#preview", EditionPane)
        pane.show_book(self.selected_book, self.preferences["directory"])
        pane.query_one("#download-book", Button).disabled = True
        if self.preview_timer is not None:
            self.preview_timer.stop()
        cached = self.detail_cache.get(self.selected_book.md5)
        if cached is not None:
            self.show_preview(self.selected_book.md5, *cached)
        elif self.selected_book.md5 in self.detail_errors:
            self.preview_error(self.selected_book.md5, *self.detail_errors[self.selected_book.md5])
        elif self.selected_book.md5 in self.detail_loading or self.search_busy:
            pane.query_one("#book-description", Label).update("Loading edition details…")
        else:
            self.preview_timer = self.set_timer(0.2, self.load_preview)

    @work(thread=True, exclusive=True, group="preview")
    def load_preview(self):
        selected = self.selected_book
        if selected is None:
            return
        worker = get_current_worker()
        try:
            cached = self.detail_cache.get(selected.md5)
            if cached is None:
                with self.client_factory(**self.client_options) as client:
                    book = client.info(selected.md5)
                    statistics = optional_statistics(client, book)
                cached = (book, statistics)
            if not worker.is_cancelled:
                self.call_from_thread(self.show_preview, selected.md5, *cached)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.call_from_thread(
                    self.preview_error,
                    selected.md5,
                    error_message(exc),
                    exc.origin if isinstance(exc, AnnaError) else None,
                )

    def preview_error(self, md5, message, origin=None):
        if self.selected_book is not None and self.selected_book.md5 == md5:
            self.verification_origin = origin
            error = self.query_one("#preview", EditionPane).query_one("#book-error", Label)
            error.update(Text(message))
            error.display = True

    def show_preview(self, md5, book, statistics):
        self.detail_cache[md5] = (book, statistics)
        if self.selected_book is None or self.selected_book.md5 != md5:
            return
        self.query_one("#preview", EditionPane).show_book(
            book, self.preferences["directory"], statistics
        )
        self.load_official_preview(book)

    @work(thread=True, exclusive=True, group="official-preview")
    def load_official_preview(self, book):
        worker = get_current_worker()
        if book.md5 in self.official_cache:
            official = self.official_cache[book.md5]
        else:
            official = lookup_official(self.client_factory, self.client_options, book)
        if not worker.is_cancelled:
            self.call_from_thread(self.show_official_preview, book.md5, official)

    def show_official_preview(self, md5, official):
        self.official_cache[md5] = official
        if self.selected_book is not None and self.selected_book.md5 == md5:
            self.query_one("#preview", EditionPane).show_official(official)

    @on(Button.Pressed, "#download-book")
    def download_preview(self):
        if len(self.screen_stack) == 1 and self.selected_book is not None:
            cached = self.detail_cache.get(self.selected_book.md5)
            if cached is not None:
                self.book_selected(cached[0])

    @on(Button.Pressed, "#official-download")
    def download_official_preview(self):
        if len(self.screen_stack) == 1 and self.selected_book is not None:
            official = self.official_cache.get(self.selected_book.md5)
            if official is not None:
                self.book_selected(official)

    @on(OptionList.OptionSelected, "#results")
    def open_book(self, event):
        if event.option_index < len(self.books):
            if not self.screen.has_class("compact"):
                # The preview already contains the selected edition's details.
                pane = self.query_one("#preview", EditionPane)
                pane.query_one("#download-book", Button).focus()
                return
            self.push_screen(
                BookScreen(
                    self.books[event.option_index],
                    self.client_options,
                    Path(self.preferences["directory"]).expanduser(),
                    self.client_factory,
                    cached=self.detail_cache.get(self.books[event.option_index].md5),
                    preload_error=self.detail_errors.get(self.books[event.option_index].md5),
                    waiting=self.books[event.option_index].md5 in self.detail_loading,
                ),
                self.book_selected,
            )

    def book_selected(self, book: Book | None):
        if book is not None:
            self.push_screen(
                DownloadScreen(
                    book,
                    self.client_options,
                    Path(self.preferences["directory"]).expanduser(),
                    self.client_factory,
                )
            )
