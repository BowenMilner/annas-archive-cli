"""Explicit official-edition alternatives; never replace an Anna catalogue checksum."""

import re
import zipfile
import zlib
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from anna.client import check_status
from anna.errors import IntegrityError, ParseError
from anna.parsing import Book, Link, author_matches, text

ORIGIN = "https://www.gutenberg.org"


def title_key(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def find_edition(client, selected: Book) -> Book | None:
    """Find an exact title and whole-name match, then verify the official record."""
    if not selected.title or not selected.author or selected.format not in {"", "epub"}:
        return None
    response = client.http.get(
        ORIGIN + "/ebooks/search/",
        params={"query": selected.title},
    )
    check_status(response)
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    for card in soup.select(".booklink"):
        anchor = card.select_one("a.link[href]")
        if anchor is None:
            continue
        path = urlsplit(str(anchor["href"])).path
        match = re.fullmatch(r"/ebooks/(\d+)", path)
        author = text(card.select_one(".subtitle"))
        if match and title_key(text(card.select_one(".title"))) == title_key(selected.title):
            # Catalogue authors may include dates and contributors. Every name word
            # in the official author must occur in the selected author string.
            if author_matches(selected.author, author):
                candidates.append(match[1])
    if not candidates:
        return None
    number = candidates[0]
    page_url = ORIGIN + "/ebooks/" + number
    response = client.http.get(page_url)
    check_status(response)
    soup = BeautifulSoup(response.text, "html.parser")
    fields = {}
    for row in soup.select(".bibrec tr"):
        heading, value = row.select_one("th"), row.select_one("td")
        if heading is not None and value is not None:
            fields[text(heading)] = text(value)
    author = fields.get("Author", "")
    name_words = re.sub(r"\d+(?:-\d+)?", "", author)
    if title_key(fields.get("Title", "")) != title_key(selected.title) or not author_matches(
        selected.author, name_words
    ):
        return None
    if "public domain" not in fields.get("Copyright", "").casefold():
        return None
    language = soup.select_one('.bibrec tr[itemprop="inLanguage"]')
    code = str(language.get("content", "")) if language else ""
    if selected.language and code != selected.language:
        return None
    # Use the explicitly advertised illustrated EPUB, avoiding cloud-send routes.
    paths = [f"/ebooks/{number}.epub.images", f"/ebooks/{number}.epub3.images"]
    target = next(
        (path for path in paths if soup.select_one(f'a[href="{path}"]') is not None), None
    )
    if target is None:
        return None
    downloads = re.search(r"(\d[\d,]*) downloads in the last 30 days", fields.get("Downloads", ""))
    return Book(
        "",
        fields["Title"],
        page_url,
        author=author,
        publisher="Project Gutenberg",
        language=code,
        format="epub",
        description=fields.get("Credits", ""),
        links=[Link("Official illustrated EPUB", urljoin(ORIGIN, target), "external")],
        source="gutenberg",
        source_id=number,
        downloads=int(downloads[1].replace(",", "")) if downloads else None,
    )


def validate_epub(path):
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.read("mimetype") != b"application/epub+zip" or archive.testzip() is not None:
                raise IntegrityError("Official EPUB integrity check failed; no file was saved.")
    except (
        zipfile.BadZipFile,
        KeyError,
        RuntimeError,
        EOFError,
        zlib.error,
        NotImplementedError,
    ) as exc:
        raise IntegrityError(
            "Official source returned an invalid EPUB; no file was saved."
        ) from exc


def download_edition(client, book: Book, **options):
    if book.source != "gutenberg" or not book.source_id.isdigit() or len(book.links) != 1:
        raise ParseError("Invalid official edition metadata.")
    url = book.links[0].url
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.gutenberg.org"
        or parsed.path
        not in {
            f"/ebooks/{book.source_id}.epub.images",
            f"/ebooks/{book.source_id}.epub3.images",
        }
    ):
        raise ParseError("Official edition link is not a recognised Gutenberg EPUB.")
    return client.download(
        url,
        output=options["directory"] / f"gutenberg-{book.source_id}-illustrated.epub",
        file_validator=validate_epub,
        **options,
    )
