import asyncio
import json
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner
from textual.widgets import Checkbox, Input, Label, OptionList, Select

from anna.cli import main
from anna.client import Client
from anna.parsing import Book, exact_matches, parse_info, parse_search
from anna.tui import AnnaApp, BookScreen


@pytest.mark.parametrize("size", [(90, 36), (120, 36)])
def test_fifty_results_visible_while_first_detail_is_blocked_and_selection_prioritised(
    tmp_path, monkeypatch, size
):
    started = threading.Event()
    release = threading.Event()
    books = [Book(f"{i:032x}", f"Volume {i}", "") for i in range(1, 51)]

    class Backend(Client):
        reads = []

        def search(self, *args, **kw):
            return books

        def info(self, md5):
            self.reads.append(md5)
            if len(self.reads) == 1:
                started.set()
                assert release.wait(5)
            return Book(md5, f"Ready volume {int(md5, 16)}", "")

        def statistics(self, md5):
            return {"downloads_total": int(md5, 16)}

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=size) as pilot:
            try:
                app.query_one("#query", Input).value = "Volume"
                app.submit_search()
                assert await asyncio.to_thread(started.wait, 3)
                await pilot.pause()
                assert len(app.books) == 50
                assert app.query_one("#results", OptionList).option_count == 50
                assert not app.search_busy
                assert not app.detail_cache
                app.query_one("#results", OptionList).highlighted = 49
                await pilot.pause()
                if size[0] == 90:
                    await pilot.press("enter")
                    assert isinstance(app.screen, BookScreen)
                    assert app.screen.waiting
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert Backend.reads[:2] == [books[0].md5, books[49].md5]
                assert len(Backend.reads) == len(set(Backend.reads)) == 50
                assert app.selected_book.md5 == books[49].md5
                if size[0] == 90:
                    assert app.screen.loaded
                    assert "50 downloads" in str(
                        app.screen.query_one("#book-statistics", Label).render()
                    )
            finally:
                release.set()

    asyncio.run(scenario())


def test_download_sort_preserves_highlight_as_counts_arrive(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    books = [Book(str(i) * 32, f"Volume {i}", "") for i in range(1, 4)]

    class Backend(Client):
        reads = []

        def search(self, *args, **kw):
            assert kw["sort"] == ""
            return books

        def info(self, md5):
            self.reads.append(md5)
            if len(self.reads) == 1:
                started.set()
                assert release.wait(5)
            return next(book for book in books if book.md5 == md5)

        def statistics(self, md5):
            return (
                {"downloads_total": {books[0].md5: 10, books[1].md5: 100}[md5]}
                if md5 != books[2].md5
                else {}
            )

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=(120, 36)) as pilot:
            try:
                app.query_one("#query", Input).value = "Volume"
                app.submit_search()
                assert await asyncio.to_thread(started.wait, 3)
                await pilot.pause()
                selected = app.selected_book.md5
                app.query_one("#sort", Select).value = "downloads"
                await pilot.pause()
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert [book.md5 for book in app.books] == [
                    books[1].md5,
                    books[0].md5,
                    books[2].md5,
                ]
                assert app.selected_book.md5 == selected
                assert len(Backend.reads) == 3
            finally:
                release.set()

    asyncio.run(scenario())


def test_new_search_stays_visible_when_old_background_response_finishes(tmp_path, monkeypatch):
    old_started, new_started, release_old, old_finished = (threading.Event() for _ in range(4))
    old = Book("a" * 32, "Old result", "")
    new = Book("b" * 32, "New result", "")

    class Backend(Client):
        def search(self, query, **kw):
            return [new if query == "New" else old]

        def info(self, md5):
            if md5 == old.md5:
                old_started.set()
                assert release_old.wait(5)
                old_finished.set()
                return old
            new_started.set()
            return new

        def statistics(self, md5):
            return {}

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=(120, 36)) as pilot:
            try:
                app.query_one("#query", Input).value = "Old"
                app.submit_search()
                assert await asyncio.to_thread(old_started.wait, 3)
                app.query_one("#query", Input).value = "New"
                app.submit_search()
                assert await asyncio.to_thread(new_started.wait, 3)
                await pilot.pause()
                assert [book.title for book in app.books] == ["New result"]
                release_old.set()
                assert await asyncio.to_thread(old_finished.wait, 3)
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert [book.title for book in app.books] == ["New result"]
                assert old.md5 not in app.detail_cache
                assert app.selected_book.md5 == new.md5
            finally:
                release_old.set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "title,filename,query,expected",
    [
        ("Manga Volume 12", "", "Manga 13", False),
        ("Manga Volume 130", "", "Manga 13", False),
        ("Manga Volume 13", "", "manga 13", True),
        ("Manga", "Manga_Vol.13.epub", "manga 13", True),
        ("Manga", "Manga_Vol.12.epub", "manga 13", False),
        ("Manga", "Manga_Vol.13.epub", "Manga_Vol.13.epub", True),
    ],
)
def test_exact_words_and_numbers(title, filename, query, expected):
    assert exact_matches(Book("a" * 32, title, "", filename=filename), query) == expected


def test_original_filename_parsed_from_real_catalogue_layouts():
    fixtures = Path(__file__).parent / "fixtures"
    expected = "Jane Austen - Pride and Prejudice (1998, Project Gutenberg).epub"
    assert (
        parse_search((fixtures / "search-current.html").read_text(), "https://example.test")[
            0
        ].filename
        == expected
    )
    assert (
        parse_info(
            (fixtures / "info-current.html").read_text(), "https://example.test", "a" * 32
        ).filename
        == expected
    )


def test_cli_exact_and_download_sort(monkeypatch):
    records = [
        Book("a" * 32, "Series Volume 12", ""),
        Book("b" * 32, "Series Volume 13", ""),
        Book("c" * 32, "Series", "", filename="Series_Volume_13.epub"),
    ]
    monkeypatch.setattr(Client, "search", lambda *args, **kw: records)
    monkeypatch.setattr(
        Client, "statistics", lambda self, md5: {"downloads_total": 100 if md5 == "c" * 32 else 10}
    )
    result = CliRunner().invoke(
        main, ["search", "Series", "13", "--exact", "--sort", "downloads", "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["md5"] for row in rows] == ["c" * 32, "b" * 32]
    assert rows[0]["downloads"] == 100


def test_tui_exact_filter_applies_before_showing_results(tmp_path, monkeypatch):
    class Backend(Client):
        def search(self, *args, **kw):
            return [Book("a" * 32, "Series Volume 12", ""), Book("b" * 32, "Series Volume 13", "")]

        def info(self, md5):
            return Book(md5, "Series Volume 13", "")

        def statistics(self, md5):
            return {}

    monkeypatch.setattr("anna.tui.lookup_official", lambda *args: None)

    async def scenario():
        app = AnnaApp(
            client_factory=Backend,
            preferences={"language": "en", "format": "epub", "directory": str(tmp_path)},
        )
        async with app.run_test(size=(120, 36)) as pilot:
            app.query_one("#query", Input).value = "Series 13"
            app.query_one("#exact", Checkbox).value = True
            await pilot.pause()
            app.submit_search()
            await app.workers.wait_for_complete()
            assert [book.title for book in app.books] == ["Series Volume 13"]

    asyncio.run(scenario())
