import io
import zipfile

import httpx
import pytest

from anna.client import Client
from anna.errors import FileExistsError, IntegrityError
from anna.gutenberg import download_edition, find_edition
from anna.parsing import Book

SEARCH = """<li class="booklink"><a class="link" href="/ebooks/1342">
<span class="title">Pride and Prejudice</span><span class="subtitle">Jane Austen</span></a></li>"""
PAGE = """<table class="bibrec">
<tr><th>Title</th><td>Pride and Prejudice</td></tr>
<tr><th>Author</th><td>Austen, Jane, 1775-1817</td></tr>
<tr content="en" itemprop="inLanguage"><th>Language</th><td>English</td></tr>
<tr><th>Copyright</th><td>Public domain in the USA.</td></tr>
<tr><th>Downloads</th><td>190030 downloads in the last 30 days.</td></tr>
</table><a href="/ebooks/1342.epub.images">EPUB (older e-readers)</a>"""


def selected():
    return Book(
        "a" * 32, "Pride and Prejudice", "", author="Jane Austen", language="en", format="epub"
    )


def epub():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr(zipfile.ZipInfo("META-INF/container.xml"), "test")
    return data.getvalue()


def backend(search=SEARCH, page=PAGE, data=None):
    def handle(request):
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, text=search)
        if request.url.path == "/ebooks/1342":
            return httpx.Response(200, text=page)
        assert request.url.host == "www.gutenberg.org"
        return httpx.Response(
            200,
            content=data if data is not None else epub(),
            headers={"content-type": "application/epub+zip"},
        )

    return Client(transport=httpx.MockTransport(handle))


def test_explicit_official_edition_has_its_own_identity_and_integrity(tmp_path):
    original = selected()
    with backend() as client:
        book = find_edition(client, original)
        assert book is not None
        assert book.source == "gutenberg" and book.md5 == ""
        assert book.source_id == "1342" and book.downloads == 190030
        assert original.md5 == "a" * 32
        result = download_edition(client, book, directory=tmp_path)
        assert result["bytes"] == len(epub())
        assert (tmp_path / "gutenberg-1342-illustrated.epub").read_bytes() == epub()
        with pytest.raises(FileExistsError):
            download_edition(client, book, directory=tmp_path)


@pytest.mark.parametrize(
    "page",
    [
        PAGE.replace("Austen, Jane", "Other, Author"),
        PAGE.replace("Pride and Prejudice", "Another book"),
        PAGE.replace('content="en"', 'content="fr"'),
        PAGE.replace("Public domain", "Copyright reserved"),
    ],
)
def test_rejects_wrong_author_title_language_or_non_public_domain_record(page):
    with backend(page=page) as client:
        assert find_edition(client, selected()) is None


def test_unrelated_author_and_title_are_not_offered():
    with backend(search=SEARCH.replace("Jane Austen", "Another Writer")) as client:
        assert find_edition(client, selected()) is None


def test_bad_official_epub_is_never_published(tmp_path):
    with backend(data=b"not an EPUB") as client:
        book = find_edition(client, selected())
        assert book is not None
        with pytest.raises(IntegrityError):
            download_edition(client, book, directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_missing_statistic_is_distinct_from_zero():
    with Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"downloads_total": 0, "lists_count": True, "reports_count": -1}
            )
        )
    ) as client:
        assert client.statistics("a" * 32) == {"downloads_total": 0}
