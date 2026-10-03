"""Keyboard-driven book browser; network and file work stay off the UI thread."""

import threading
import time
from pathlib import Path

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, OptionList, ProgressBar, Select
from textual.worker import get_current_worker

from anna.client import Client, download_record
from anna.config import load_config, save_config
from anna.errors import AnnaError, DownloadCancelledError
from anna.parsing import Book, author_matches


def book_summary(book):
    parts = [
        {"en": "English", "fr": "French", "de": "German"}.get(book.language, book.language),
        book.format.upper(),
        book.size,
    ]
    return " · ".join(part for part in parts if part) or book.metadata


def error_message(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "The file source timed out. Try again later or choose another edition."
    if isinstance(exc, httpx.HTTPError):
        return "Connection failed. Check your network and try again."
    if isinstance(exc, OSError):
        return f"Cannot save the file: {exc.strerror or type(exc).__name__}."
    return str(exc)


class SettingsScreen(ModalScreen):
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


class BookScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, book, options, directory, client_factory):
        super().__init__()
        self.book = book
        self.options = dict(options)
        self.directory = directory
        self.client_factory = client_factory
        self.ready = False
        self.loaded = False

    def compose(self) -> ComposeResult:
        with Vertical(id="book-panel", classes="dialog"):
            with VerticalScroll(id="book-copy"):
                yield Label(Text(self.book.title), classes="heading")
                yield Label(Text(self.book.author or "Unknown author"))
                yield Label(Text(book_summary(self.book)), id="book-metadata")
                yield Label(Text(self.book.publisher), id="book-publisher")
                yield Label("Loading edition details…", id="book-description")
                yield Label(Text(f"Save to: {self.directory}"))
                yield Label("", id="book-error")
            with Horizontal(classes="actions"):
                yield Button("Download", id="download-book", variant="primary", disabled=True)
                yield Button("Back", id="close-book")

    def on_mount(self):
        self.load_details()

    @work(thread=True, exclusive=True)
    def load_details(self):
        worker = get_current_worker()
        try:
            with self.client_factory(**self.options) as client:
                book = client.info(self.book.md5)
            if not worker.is_cancelled:
                self.app.call_from_thread(self.show_details, book)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.show_error, error_message(exc))

    def show_details(self, book):
        self.loaded = True
        self.book = book
        self.ready = any(
            link.kind != "fast" and link.url.startswith(("http://", "https://"))
            for link in book.links
        )
        self.query_one("#book-description", Label).update(
            Text(book.description or "No description available.")
        )
        self.query_one("#book-metadata", Label).update(Text(book_summary(book)))
        self.query_one("#book-publisher", Label).update(Text(book.publisher))
        if not self.ready:
            self.show_error("No free HTTP download source is available for this edition.")
        self.update_download_button()

    def show_error(self, message):
        if not self.loaded:
            self.query_one("#book-description", Label).update("Could not load edition details.")
        self.query_one("#book-error", Label).update(Text(message))

    def update_download_button(self):
        self.query_one("#download-book", Button).disabled = not self.ready

    @on(Button.Pressed, "#close-book")
    def close_book(self):
        self.dismiss(None)

    @on(Button.Pressed, "#download-book")
    def download_book(self):
        if self.ready:
            self.dismiss(self.book)


class DownloadScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel / back")]

    def __init__(self, book, options, directory, client_factory):
        super().__init__()
        self.book = book
        self.options = dict(options)
        self.directory = directory
        self.client_factory = client_factory
        self.cancel_event = threading.Event()
        self.running = True
        self.bytes_received = 0
        self.total = None
        self.source_index = 1
        self.source_count = 1
        self.last_update = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="download-panel", classes="dialog"):
            yield Label(Text(self.book.title), classes="heading")
            yield Label("Preparing download…", id="download-status")
            yield ProgressBar(total=None, show_eta=False, id="progress")
            yield Label(Text(f"Save to: {self.directory}"))
            yield Button("Cancel", id="cancel-download")

    def on_mount(self):
        self.transfer()

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
        self.show_request("Requesting download link…")

    def show_request(self, phase):
        if not self.cancel_event.is_set():
            self.query_one("#download-status", Label).update(
                f"Free source {self.source_index}/{self.source_count} · {phase}"
            )

    def set_total(self, total):
        self.total = total
        self.query_one("#progress", ProgressBar).update(total=total, progress=0)

    def show_progress(self, size):
        if self.cancel_event.is_set():
            return
        self.query_one("#progress", ProgressBar).update(progress=size)
        amount = f"{size / 1048576:.2f} MiB"
        if self.total:
            amount += f" / {self.total / 1048576:.2f} MiB"
        self.query_one("#download-status", Label).update("Downloading · " + amount)

    def show_wait(self, seconds):
        if not self.cancel_event.is_set():
            self.query_one("#download-status", Label).update(
                f"Free source {self.source_index}/{self.source_count} · {seconds}s remaining"
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

    @work(thread=True)
    def transfer(self):
        def progress(amount):
            self.bytes_received += amount
            now = time.monotonic()
            if now - self.last_update >= 0.1:
                self.app.call_from_thread(self.show_progress, self.bytes_received)
                self.last_update = now

        try:
            with self.client_factory(**self.options) as client:
                # Resolve sources on the active mirror, not an earlier search session.
                book = client.info(self.book.md5)
                result = download_record(
                    client,
                    book,
                    source_progress=lambda index, total: self.app.call_from_thread(
                        self.start_source, index, total
                    ),
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
            self.app.call_from_thread(
                self.finish, f"Saved and checksum verified: {result['path']}", True
            )
        except DownloadCancelledError:
            self.app.call_from_thread(
                self.finish, "Cancelled. No unfinished book was saved.", False
            )
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            self.app.call_from_thread(self.finish, error_message(exc), False)


class AnnaApp(App):
    TITLE = "Anna · Books"
    BINDINGS = [
        Binding("/", "search", "Search"),
        Binding("ctrl+s", "settings", "Settings"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "search", "Back to search"),
    ]
    CSS = """
    Screen { background: #101820; color: #e7ecef; align-horizontal: center; }
    #browser { max-width: 100; width: 100%; height: 1fr; padding: 1 2; margin: 0; }
    .dialog Label { width: 100%; }
    .heading { text-style: bold; color: #8ed9c4; margin-bottom: 1; }
    #filters { height: auto; }
    #author { width: 1fr; }
    #language { width: 14; margin: 0 1; }
    #format { width: 18; }
    #query { margin-bottom: 1; }
    #results { height: 1fr; margin: 1 0; border: round #34525c; }
    #status { height: auto; min-height: 1; color: #b0c4cb; }
    #toolbar { height: auto; }
    #settings { margin-left: 1; }
    ModalScreen { align: center middle; background: #00000088; }
    .dialog { width: 76; max-width: 96%; height: auto; max-height: 94%;
              padding: 1 2; border: round #8ed9c4; background: #17252d; }
    .actions { height: auto; margin-top: 1; }
    .actions Button { margin-right: 1; }
    #book-copy { height: auto; max-height: 24; }
    #book-error, #settings-error { color: #ffbdad; height: auto; }
    #progress { margin: 1 0; }
    #download-status { height: auto; }
    Input, Select { border: tall #34525c; }
    Input:focus { border: tall #8ed9c4; }
    OptionList:focus { border: round #8ed9c4; }
    """

    def __init__(self, client_options=None, preferences=None, client_factory=Client):
        super().__init__()
        self.client_options = dict(client_options or {})
        self.preferences = dict(preferences or load_config())
        self.client_factory = client_factory
        self.books = []

    def compose(self) -> ComposeResult:
        formats = list(dict.fromkeys(["epub", "pdf", "mobi", "txt", self.preferences["format"]]))
        with Vertical(id="browser"):
            yield Label("Anna  /  Find your next book", classes="heading")
            yield Input(placeholder="Search a title or keyword…", id="query")
            with Horizontal(id="filters"):
                yield Input(placeholder="Author (optional)", id="author")
                yield Input(self.preferences["language"], placeholder="Language", id="language")
                yield Select(
                    [(item.upper(), item) for item in formats] + [("Any format", "*")],
                    value=self.preferences["format"],
                    allow_blank=False,
                    id="format",
                )
            with Horizontal(id="toolbar"):
                yield Button("Search", id="search", variant="primary")
                yield Button("Settings", id="settings")
            yield Label("Search → choose an edition → download to your Books folder.", id="status")
            yield OptionList(id="results")
        yield Footer()

    def on_mount(self):
        self.query_one("#query", Input).focus()

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
        self.query_one("#status", Label).update("Searching…")
        self.search_books(query, author, language, str(book_format))

    @work(thread=True, exclusive=True)
    def search_books(self, query, author, language, book_format):
        worker = get_current_worker()
        try:
            with self.client_factory(**self.client_options) as client:
                books = client.search(
                    query + (" " + author if author else ""),
                    lang=() if language in ("", "*") else (language,),
                    ext=() if book_format == "*" else (book_format,),
                    page=1,
                )
            if author:
                books = [book for book in books if author_matches(book.author, author)]
            if not worker.is_cancelled:
                self.call_from_thread(self.show_results, books)
        except (AnnaError, httpx.HTTPError, OSError) as exc:
            if not worker.is_cancelled:
                self.call_from_thread(self.show_search_error, error_message(exc))

    def show_search_error(self, message):
        self.query_one("#status", Label).update(Text(message))

    def show_results(self, books):
        self.books = books
        results = self.query_one("#results", OptionList)
        results.clear_options()
        for book in books:
            label = Text()
            label.append(book.title, style="bold")
            label.append(f"\n{book.author or 'Unknown author'}", style="#b0c4cb")
            label.append(f"\n{book_summary(book)}\n", style="#8ed9c4")
            results.add_option(label)
        self.query_one("#status", Label).update(
            f"{len(books)} editions · ↑↓ browse · Enter opens details"
            if books
            else "No matching books. Try fewer keywords or Any format."
        )
        if books:
            results.highlighted = 0
            results.focus()

    @on(OptionList.OptionSelected, "#results")
    def open_book(self, event):
        if event.option_index < len(self.books):
            self.push_screen(
                BookScreen(
                    self.books[event.option_index],
                    self.client_options,
                    Path(self.preferences["directory"]).expanduser(),
                    self.client_factory,
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
