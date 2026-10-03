import asyncio
import hashlib
import json

import httpx
import pytest
from click.testing import CliRunner
from textual.widgets import Button, Input, Label, OptionList, Select

from anna import cli
from anna.client import Client
from anna.errors import DownloadCancelledError
from anna.parsing import Book, Link
from anna.tui import AnnaApp, BookScreen, DownloadScreen, SettingsScreen

DATA = b"synthetic lawful download"
MD5 = hashlib.md5(DATA).hexdigest()


def make_app(tmp_path, fail=False, empty=False):
    class Backend:
        def __init__(self, **options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def search(self, query, **filters):
            if fail:
                raise httpx.ConnectError("offline")
            if empty:
                return []
            return [
                Book(
                    MD5,
                    "[bold]Pride and Prejudice",
                    "",
                    author="Austen, Jane",
                    metadata="English [en] · EPUB · 0.3 MB",
                )
            ]

        def info(self, md5):
            return Book(
                md5,
                "Pride and Prejudice",
                "",
                author="Jane Austen",
                description="An edition to inspect.",
                links=[Link("Free", "https://example.test/book.epub", "external")],
            )

        def download(
            self, url, directory, expected_md5, progress, total_progress, cancelled, **kwargs
        ):
            if cancelled():
                raise DownloadCancelledError("cancelled")
            total_progress(len(DATA))
            progress(len(DATA))
            path = directory / "book.epub"
            path.write_bytes(DATA)
            return {"path": str(path), "bytes": len(DATA), "md5": expected_md5}

    return AnnaApp(
        preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        client_factory=Backend,
    )


@pytest.mark.parametrize("size", [(80, 24), (90, 36)])
def test_keyboard_search_details_verified_download_and_return(tmp_path, size):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=size) as pilot:
            await pilot.press(*"Pride", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.books) == 1
            assert isinstance(app.focused, OptionList)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, BookScreen)
            assert not app.screen.query_one("#download-book", Button).disabled
            await pilot.click("#download-book")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, DownloadScreen)
            assert not app.screen.running
            assert (tmp_path / "book.epub").read_bytes() == DATA
            assert "checksum verified" in str(
                app.screen.query_one("#download-status", Label).render()
            )
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert len(app.books) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(80, 24), (90, 36)])
def test_settings_keyboard_save_and_reload(tmp_path, monkeypatch, size):
    monkeypatch.setenv("ANNA_CONFIG", str(tmp_path / "preferences.json"))

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=size) as pilot:
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            app.screen.query_one("#setting-format", Input).value = "pdf"
            app.screen.query_one("#setting-language", Input).value = "fr"
            await pilot.click("#save-settings")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.preferences["format"] == "pdf"
            assert app.query_one("#format", Select).value == "pdf"
            assert app.query_one("#language", Input).value == "fr"
            assert json.loads((tmp_path / "preferences.json").read_text())["format"] == "pdf"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fail, empty, message",
    [
        (True, False, "Connection failed"),
        (False, True, "No matching books"),
    ],
)
def test_search_error_or_empty_results_recover(tmp_path, fail, empty, message):
    async def scenario():
        app = make_app(tmp_path, fail=fail, empty=empty)
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#query", Input).value = "Austen"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert message in str(app.query_one("#status", Label).render())
            assert app.books == []
            await pilot.press("/")
            assert app.focused.id == "query"

    asyncio.run(scenario())


def test_no_argument_cli_checks_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("ANNA_CONFIG", str(tmp_path / "config.json"))
    result = CliRunner().invoke(cli.main, [])
    assert result.exit_code == 2
    assert "needs a terminal" in result.output
    result = CliRunner().invoke(cli.main, ["--json"])
    assert result.exit_code == 2
    assert "Use a subcommand" in result.output


def test_cancel_transfer_removes_partial_and_reports_total(tmp_path):
    cancelled = [False]
    totals = []

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * 65536
            yield b"b" * 65536

    def progress(amount):
        cancelled[0] = True

    with Client(
        "https://example.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=Chunks(), headers={"content-length": "131072"}
            )
        ),
    ) as client:
        with pytest.raises(DownloadCancelledError):
            client.download(
                "https://example.test/file.epub",
                directory=tmp_path,
                progress=progress,
                cancelled=lambda: cancelled[0],
                total_progress=totals.append,
            )
    assert totals == [131072]
    assert list(tmp_path.iterdir()) == []


def test_cancel_free_source_countdown_does_not_sleep(tmp_path, monkeypatch):
    cancelled = [False]

    def wait(seconds):
        cancelled[0] = True

    monkeypatch.setattr("anna.client.time.sleep", lambda seconds: None)
    with Client(
        "https://example.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text='<span class="js-partner-countdown">2</span>',
                headers={"content-type": "text/html"},
            )
        ),
    ) as client:
        with pytest.raises(DownloadCancelledError):
            client.download(
                "https://example.test/slow_download/" + MD5 + "/0/0",
                directory=tmp_path,
                wait_progress=wait,
                cancelled=lambda: cancelled[0],
            )
    assert list(tmp_path.iterdir()) == []


def test_download_cancel_stays_open_until_worker_cleans_up(tmp_path):
    class SlowBackend:
        def __init__(self, **options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def info(self, md5):
            return Book(
                md5,
                "Test edition",
                "",
                links=[Link("Free", "https://example.test/file", "external")],
            )

        def download(self, url, cancelled, **options):
            import time

            for _ in range(1000):
                if cancelled():
                    raise DownloadCancelledError("cancelled")
                time.sleep(0.001)
            raise AssertionError("Cancellation was not delivered")

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = DownloadScreen(Book(MD5, "Test edition", ""), {}, tmp_path, SlowBackend)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not screen.running
            assert "Cancelled" in str(screen.query_one("#download-status", Label).render())
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_tui_falls_back_and_resets_partial_byte_progress(tmp_path):
    app = make_app(tmp_path)
    original = app.client_factory

    class AlternativeBackend(original):
        def info(self, md5):
            book = super().info(md5)
            book.links = [
                Link("First", "https://dead.example/file", "slow"),
                Link("Second", "https://files.example/file", "slow"),
            ]
            return book

        def download(self, url, **options):
            if "dead.example" in url:
                options["total_progress"](131072)
                options["progress"](65536)
                raise httpx.ReadTimeout("failed", request=httpx.Request("GET", url))
            return super().download(url, **options)

    app.client_factory = AlternativeBackend

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press(*"Pride", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.click("#download-book")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, DownloadScreen)
            assert app.screen.source_index == 2
            assert app.screen.bytes_received == len(DATA)
            assert (tmp_path / "book.epub").read_bytes() == DATA
            assert "checksum verified" in str(
                app.screen.query_one("#download-status", Label).render()
            )

    asyncio.run(scenario())


def test_bookfinder_fills_terminal_and_adapts_while_dialog_open(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(144, 40)) as pilot:
            assert app.query_one("#browser").region.width == 144
            assert app.query_one("#preview").display
            await pilot.press(*"Austen", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            assert "Pride and Prejudice" in str(app.query_one("#book-title", Label).render())
            await pilot.press("enter")
            assert len(app.screen_stack) == 1
            assert app.focused.id == "download-book"
            await pilot.resize_terminal(60, 24)
            app.query_one("#results", OptionList).focus()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, BookScreen)
            await pilot.resize_terminal(60, 24)
            await pilot.press("escape")
            assert app.screen.has_class("compact")
            assert not app.query_one("#preview").display
            assert app.query_one("#browser").region.width == 60

    asyncio.run(scenario())


def test_failed_archive_source_offers_explicit_official_download(tmp_path, monkeypatch):
    from test_gutenberg import backend, epub, find_edition, selected

    from anna.errors import DownloadSourcesError

    with backend() as client:
        official = find_edition(client, selected())
    monkeypatch.setattr(Client, "info", lambda *_: selected())
    monkeypatch.setattr("anna.tui.lookup_official", lambda *_: official)
    monkeypatch.setattr(
        "anna.tui.download_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DownloadSourcesError("All archive sources timed out.")
        ),
    )

    async def scenario():
        app = AnnaApp(
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
            client_factory=lambda **_: backend(),
        )
        async with app.run_test(size=(90, 36)) as pilot:
            app.push_screen(DownloadScreen(selected(), {}, tmp_path, app.client_factory))
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, DownloadScreen)
            assert not screen.running
            assert screen.query_one("#official-download", Button).display
            assert not list(tmp_path.iterdir())  # Finding an alternative does not download it.
            await pilot.click("#official-download")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.screen_stack) == 2
            assert (tmp_path / "gutenberg-1342-illustrated.epub").read_bytes() == epub()
            assert "integrity checked" in str(screen.query_one("#download-status", Label).render())

    asyncio.run(scenario())


def test_browser_check_saves_session_then_retries_search(tmp_path, monkeypatch):
    from anna.tui import BrowserCheckScreen

    monkeypatch.setattr("anna.tui.import_firefox", lambda *_: "Firefox test agent")
    monkeypatch.setattr("anna.tui.remember_origin", lambda *_: None)

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 36)) as pilot:
            app.query_one("#query", Input).value = "Austen"
            await pilot.press("f2")
            assert isinstance(app.screen, BrowserCheckScreen)
            await pilot.click("#reuse-browser")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert len(app.screen_stack) == 1
            assert app.client_options["user_agent"] == "Firefox test agent"
            assert len(app.books) == 1

    asyncio.run(scenario())


def test_wide_selection_keeps_preview_and_download_opens_only_activity_strip(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(144, 40)) as pilot:
            await pilot.press(*"Austen", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.press("enter")
            assert len(app.screen_stack) == 1
            assert app.focused.id == "download-book"
            button = app.query_one("#download-book", Button)
            assert button.region.height == 3
            assert button.styles.border.top[0] == "round"
            assert app.query_one("#browser").region.x == 0
            assert app.query_one("#browser").region.y == 0
            assert app.query_one("Footer").region.bottom == 40
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, DownloadScreen)
            assert app.screen.query_one("#download-panel").region.height < 20
            assert app.screen_stack[0].query_one("#preview").display
            assert (tmp_path / "book.epub").read_bytes() == DATA

    asyncio.run(scenario())


def test_archive_remains_primary_when_official_alternative_exists(tmp_path, monkeypatch):
    from test_gutenberg import backend, find_edition, selected

    with backend() as client:
        official = find_edition(client, selected())
    monkeypatch.setattr("anna.tui.lookup_official", lambda *_: official)

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(144, 40)) as pilot:
            await pilot.press(*"Austen", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.press("enter")
            assert len(app.screen_stack) == 1
            assert app.focused.id == "download-book"
            assert app.focused.variant == "primary"
            archive = app.query_one("#download-book", Button)
            assert str(archive.label) == "Download archive file"
            assert archive.variant == "primary"
            assert app.query_one("#official-download", Button).variant == "default"

    asyncio.run(scenario())
