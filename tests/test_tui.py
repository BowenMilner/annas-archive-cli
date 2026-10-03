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


@pytest.mark.parametrize("size", [(80, 24), (100, 36)])
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


@pytest.mark.parametrize("size", [(80, 24), (100, 36)])
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
