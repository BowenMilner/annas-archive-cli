import asyncio
import hashlib
from pathlib import Path

import pytest
from click.testing import CliRunner
from textual.widgets import Button, Input, Label, OptionList, Select

from anna.cli import main
from anna.client import Client
from anna.errors import AnnaError, DownloadCancelledError, RateLimitError
from anna.library import edition_path, history, open_saved, remember, save_download, verified_copy
from anna.parsing import Book
from anna.tui import AnnaApp, BookScreen, HistoryScreen

DATA = b"a lawful synthetic edition"
MD5 = hashlib.md5(DATA).hexdigest()


def edition():
    return Book(MD5, "Pride and Prejudice", "", author="Jane Austen", format="epub")


def receipt(path):
    path.write_bytes(DATA)
    return {"path": str(path), "bytes": len(DATA), "md5": MD5}


def test_history_reuses_verified_copy_without_request_and_detects_tampering(tmp_path):
    book = edition()
    result = receipt(tmp_path / "renamed.epub")
    remember(book, result)
    assert history()[0]["identity"] == MD5
    assert "url" not in history()[0]
    assert save_download(book, lambda **kw: pytest.fail("Unexpected network request"))[
        "already_downloaded"
    ]
    Path(result["path"]).write_bytes(b"x" * len(DATA))
    assert verified_copy(book) is None
    Path(result["path"]).unlink()
    assert verified_copy(book) is None


def test_explicit_output_downloads_again_and_failed_download_has_no_receipt(tmp_path):
    book = edition()
    remember(book, receipt(tmp_path / "first.epub"))
    target = tmp_path / "second.epub"
    result = save_download(book, lambda **kw: receipt(kw["output"]), output=target)
    assert result["path"] == str(target)
    assert len(history()) == 2
    other = Book("a" * 32, "Other edition", "")

    def failed(**kw):
        raise AnnaError("No file")

    with pytest.raises(AnnaError):
        save_download(other, failed, directory=tmp_path)
    assert len(history()) == 2


def test_cancel_during_duplicate_verification_is_not_swallowed(tmp_path):
    book = edition()
    remember(book, receipt(tmp_path / "first.epub"))
    with pytest.raises(DownloadCancelledError):
        save_download(book, lambda **kw: pytest.fail("Unexpected request"), cancelled=lambda: True)


def test_readable_names_are_safe_bounded_and_distinguish_editions(tmp_path):
    book = edition()
    assert (
        edition_path(book, tmp_path).name == f"Jane Austen — Pride and Prejudice [{MD5[:12]}].epub"
    )
    book.title = "../../" + "書" * 200
    book.author = "../bad\nname"
    path = edition_path(book, tmp_path)
    assert path.parent == tmp_path
    assert len(path.name.encode()) < 255
    assert "\n" not in path.name
    book.md5 = "b" * 32
    assert path != edition_path(book, tmp_path)


def test_cli_history_and_explicit_open_actions(tmp_path, monkeypatch):
    path = tmp_path / "book with spaces.epub"
    remember(edition(), receipt(path))
    result = CliRunner().invoke(main, ["history", "--json"])
    assert result.exit_code == 0
    assert MD5 in result.output and str(path) in result.output
    calls = []
    monkeypatch.setattr("anna.library.subprocess.run", lambda command, **kw: calls.append(command))
    open_saved(path)
    open_saved(path, folder=True)
    assert calls == [["xdg-open", str(path)], ["xdg-open", str(tmp_path)]]
    path.unlink()
    with pytest.raises(AnnaError, match="missing"):
        open_saved(path)
    assert len(calls) == 2


def test_history_ui_opens_only_on_explicit_action(tmp_path, monkeypatch):
    path = tmp_path / "book.epub"
    remember(edition(), receipt(path))
    calls = []
    monkeypatch.setattr("anna.tui.open_saved", lambda path, folder: calls.append((path, folder)))

    async def scenario():
        app = AnnaApp(preferences={"language": "en", "format": "epub", "directory": str(tmp_path)})
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.click("#history")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, HistoryScreen)
            assert len(app.screen.receipts) == 1
            assert calls == []
            await pilot.click("#history-folder")
            await app.workers.wait_for_complete()
            assert calls == [(str(path), True)]
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(90, 36), (120, 36)])
def test_all_page_details_preloaded_and_browsing_never_refetches(tmp_path, monkeypatch, size):
    class Backend(Client):
        reads = []
        stats = []

        def search(self, *args, **kw):
            return [edition(), Book("b" * 32, "Second edition", "", author="Jane Austen")]

        def info(self, md5):
            assert self.http.timeout.read <= 10
            self.reads.append(md5)
            return Book(md5, "Ready details", "", author="Jane Austen")

        def statistics(self, md5):
            self.stats.append(md5)
            return {"downloads_total": 123}

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=size) as pilot:
            app.query_one("#query", Input).value = "Pride"
            app.submit_search()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert Backend.reads == Backend.stats == [MD5, "b" * 32]
            assert len(app.detail_cache) == 2
            await pilot.press("down", "up", "down", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            if size[0] == 90:
                assert isinstance(app.screen, BookScreen)
                assert "123 downloads" in str(
                    app.screen.query_one("#book-statistics", Label).render()
                )
            assert Backend.reads == [MD5, "b" * 32]
            app.submit_search()
            await app.workers.wait_for_complete()
            assert Backend.reads == [MD5, "b" * 32]

    asyncio.run(scenario())


def test_page_preload_stops_after_rate_limit_without_hiding_results(tmp_path, monkeypatch):
    class Backend(Client):
        reads = []

        def search(self, *args, **kw):
            return [edition(), Book("b" * 32, "Second", ""), Book("c" * 32, "Third", "")]

        def info(self, md5):
            self.reads.append(md5)
            raise RateLimitError("Please try later.")

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=(120, 36)) as pilot:
            app.query_one("#query", Input).value = "Pride"
            app.submit_search()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.books) == 3
            assert Backend.reads == [MD5]
            await pilot.press("down", "down", "up")
            await app.workers.wait_for_complete()
            assert Backend.reads == [MD5]

    asyncio.run(scenario())


def test_search_pagination_filters_sorting_duplicates_and_retry(tmp_path):
    class Backend:
        calls = []
        fail_next = False

        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def search(self, query, **filters):
            self.calls.append((query, filters))
            if self.fail_next:
                Backend.fail_next = False
                raise AnnaError("Please retry")
            page = filters["page"]
            books = (
                [edition()]
                if page == 1
                else [edition(), Book("b" * 32, "Another", "", author="Jane Austen")]
            )
            return books if page <= 2 else []

        def info(self, md5):
            return edition()

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=(110, 36)) as pilot:
            app.query_one("#query", Input).value = "Pride"
            app.query_one("#author", Input).value = "Jane Austen"
            app.submit_search()
            await app.workers.wait_for_complete()
            await pilot.pause()
            Backend.fail_next = True
            await pilot.click("#load-more")
            await app.workers.wait_for_complete()
            await pilot.pause(0.21)
            assert len(app.books) == 1 and app.search_page == 1
            assert not app.query_one("#load-more", Button).disabled
            await pilot.click("#load-more")
            await app.workers.wait_for_complete()
            await pilot.pause(0.21)
            assert len(app.books) == 2 and app.search_page == 2
            assert app.query_one("#results", OptionList).highlighted == 0
            app.query_one("#sort", Select).value = "smallest"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.search_page == 1 and len(app.books) == 1
            query, filters = Backend.calls[-1]
            assert query == "Pride Jane Austen"
            assert filters == {"page": 1, "sort": "smallest", "lang": ("en",), "ext": ("epub",)}
            await pilot.click("#load-more")
            await app.workers.wait_for_complete()
            await pilot.pause(0.21)
            await pilot.click("#load-more")
            await app.workers.wait_for_complete()
            await pilot.pause(0.21)
            assert app.query_one("#load-more", Button).disabled
            assert "No further results" in str(app.query_one("#status", Label).render())

    asyncio.run(scenario())
