import hashlib
from pathlib import Path

import httpx
import pytest

from anna.client import Client, download_record
from anna.errors import (
    DownloadCancelledError,
    DownloadSourcesError,
    FileExistsError,
    IntegrityError,
    RateLimitError,
)
from anna.parsing import Book, Link

DATA = b"%PDF-1.7\npublic-domain fallback test\n%%EOF"
MD5 = hashlib.md5(DATA).hexdigest()
BASE = "https://archive.example"


def record(count=3):
    return Book(
        MD5,
        "Public-domain test edition",
        BASE + "/md5/" + MD5,
        links=[Link(f"Source {i}", BASE + f"/source/{i}", "slow") for i in range(count)],
    )


def test_timeout_then_unusable_page_then_verified_file(tmp_path):
    attempts = []
    phases = []

    def handler(request):
        attempts.append(str(request.url))
        if request.url.path == "/source/0":
            return httpx.Response(
                200,
                text='<a download href="https://dead.example/file">Get</a>',
                headers={"content-type": "text/html"},
            )
        if request.url.host == "dead.example":
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == "/source/1":
            return httpx.Response(
                200,
                text="<html>Source unavailable; no download link</html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200, content=DATA, headers={"content-disposition": 'attachment; filename="book.pdf"'}
        )

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(
            client,
            record(),
            directory=tmp_path,
            source_progress=lambda i, total: phases.append((i, total)),
        )
    assert phases == [(1, 3), (2, 3), (3, 3)]
    assert Path(result["path"]).read_bytes() == DATA
    assert result["md5"] == MD5
    assert len(attempts) == 4


def test_partial_failure_cleaned_before_retry_and_progress_reset_signal(tmp_path):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 65536
            raise httpx.ReadError("disconnected")

    progress = []
    events = []

    def handler(request):
        if request.url.path == "/source/0":
            return httpx.Response(200, stream=Broken())
        assert list(tmp_path.iterdir()) == []
        return httpx.Response(200, content=DATA)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(
            client,
            record(2),
            directory=tmp_path,
            progress=progress.append,
            source_progress=lambda i, total: events.append((i, total)),
        )
    assert events == [(1, 2), (2, 2)]
    assert progress == [65536, len(DATA)]
    assert result["bytes"] == len(DATA)
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("limited"),
        IntegrityError("bad checksum"),
        FileExistsError("exists"),
        DownloadCancelledError("cancelled"),
    ],
)
def test_terminal_errors_never_try_another_source(tmp_path, error):
    class Backend:
        calls = 0

        def download(self, *args, **kwargs):
            self.calls += 1
            raise error

    backend = Backend()
    with pytest.raises(type(error)):
        download_record(backend, record(), directory=tmp_path)
    assert backend.calls == 1


def test_exhaustion_bounded_and_signed_url_not_disclosed():
    requests = []

    def handler(request):
        if request.url.host == "archive.example":
            return httpx.Response(
                302,
                headers={
                    "location": "https://files.example/private-token/file?token=do-not-display"
                },
            )
        requests.append(request)
        raise httpx.ReadTimeout("sensitive raw URL", request=request)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadSourcesError) as error:
            download_record(client, record(10))
    assert len(requests) == 3
    assert "files.example" in str(error.value)
    assert "timed out" in str(error.value)
    assert "private-token" not in str(error.value)
    assert "do-not-display" not in str(error.value)


def test_explicit_source_stays_pinned():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ConnectError("offline", request=request)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            download_record(client, record(), source=2)
    assert len(requests) == 1
    assert requests[0].url.path == "/source/1"


def test_request_phase_distinguishes_catalogue_and_file_server(tmp_path):
    phases = []

    def handler(request):
        if request.url.host == "archive.example":
            return httpx.Response(
                200,
                text='<a download href="https://files.example/book.pdf">Get</a>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, content=DATA)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        download_record(client, record(1), directory=tmp_path, request_progress=phases.append)
    assert phases == ["Requesting download link…", "Contacting file server…"]


def test_copy_only_page_uses_visible_short_url_without_executing_script():
    from anna.client import download_link

    html = (Path(__file__).parent / "fixtures/download-copy-url.html").read_text()
    assert download_link(html, BASE) == (
        "http://files.example:6060/example/annas-arch-111111111111.epub"
    )
    # The visible value wins even if the unexecuted script contains another URL.
    assert (
        download_link(
            '<button onclick="navigator.clipboard.writeText(whatever)">copy</button>'
            '<span class="break-all">https://files.example/visible.epub</span>',
            BASE,
        )
        == "https://files.example/visible.epub"
    )


def test_short_download_anchor_is_preferred():
    from anna.client import download_link

    assert (
        download_link(
            '<a href="https://files.example/long-name.epub">📚 Download now</a>'
            '<a href="https://files.example/compact.epub">Download with short filename</a>',
            BASE,
        )
        == "https://files.example/compact.epub"
    )


def test_cancellation_after_final_source_timeout_wins():
    cancelled = [False]

    class Backend:
        def download(self, *args, **kwargs):
            cancelled[0] = True
            raise httpx.ReadTimeout("timeout")

    with pytest.raises(DownloadCancelledError):
        download_record(Backend(), record(1), cancelled=lambda: cancelled[0])


def test_independent_library_source_precedes_repeated_partner_routes(tmp_path):
    book = record(17)
    direct = "https://libgen.li/ads.php?md5=" + MD5
    book.links.append(Link("Libgen.li", direct, "external"))
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/ads.php":
            return httpx.Response(
                200,
                text='<a href="/get.php?key=synthetic">GET</a>',
                headers={"content-type": "text/html"},
            )
        assert request.url.path == "/get.php"
        return httpx.Response(200, content=DATA)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(client, book, directory=tmp_path)
    assert str(requests[0].url) == direct
    assert len(requests) == 2
    assert result["md5"] == MD5
