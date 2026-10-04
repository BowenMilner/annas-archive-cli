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
            download_record(client, record(20))
    assert len(requests) == 16
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


def test_working_fourth_source_is_not_discarded(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if request.url.path != "/source/3":
            raise httpx.ReadTimeout("offline", request=request)
        return httpx.Response(200, content=DATA)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(client, record(4), directory=tmp_path)
    assert result["md5"] == MD5
    assert attempts == ["/source/0", "/source/1", "/source/2", "/source/3"]


def test_exhausted_queue_tries_another_source_without_waiting(tmp_path):
    def handler(request):
        if request.url.path.endswith("/0/0"):
            return httpx.Response(
                200,
                text='<span class="js-partner-countdown">600</span>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, content=DATA)

    book = record(2)
    book.links[0].url = BASE + "/slow_download/" + MD5 + "/0/0"
    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(client, book, directory=tmp_path, max_wait=0)
    assert result["md5"] == MD5


def test_failed_network_attempts_share_budget_and_do_not_walk_all_routes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("anna.client.time.monotonic", lambda: clock[0])

    class Backend:
        calls = 0

        def download(self, *args, **kwargs):
            self.calls += 1
            clock[0] += 30
            raise httpx.ReadTimeout("no file headers")

    backend = Backend()
    with pytest.raises(DownloadSourcesError, match="stopped after 3 of 16 listed routes"):
        download_record(backend, record(16))
    assert backend.calls == 3


def test_queue_time_does_not_consume_network_retry_budget(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr("anna.client.time.monotonic", lambda: clock[0])

    class Backend:
        calls = 0

        def download(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                kwargs["wait_progress"](120)
                clock[0] += 120
                kwargs["wait_progress"](0)
                clock[0] += 10
                raise httpx.ReadTimeout("no file headers")
            return {"md5": kwargs["expected_md5"]}

    backend = Backend()
    assert download_record(backend, record(2), retry_budget=30)["md5"] == MD5
    assert backend.calls == 2


def test_remaining_retry_budget_clips_next_network_timeout(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr("anna.client.time.monotonic", lambda: clock[0])
    reads = []

    def handler(request):
        reads.append(request.extensions["timeout"]["read"])
        if len(reads) == 1:
            clock[0] += 30
            raise httpx.ReadTimeout("no file headers", request=request)
        return httpx.Response(200, content=DATA)

    with Client(BASE, timeout=30, transport=httpx.MockTransport(handler)) as client:
        result = download_record(client, record(2), directory=tmp_path, retry_budget=35)
    assert reads == [30, 5]
    assert result["md5"] == MD5


def test_mirror_check_is_not_repeated_across_alias_routes(tmp_path):

    book = record(16)
    requests = []

    def handler(request):
        requests.append(request.url.host)
        if request.url.host == "archive.example":
            return httpx.Response(403, text="<title>DDOS-GUARD</title>")
        return httpx.Response(200, content=DATA)

    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadSourcesError, match="Skipped 15 routes") as error:
            download_record(client, book, directory=tmp_path)
        assert error.value.origin == BASE
        assert error.value.verification_url == BASE + "/source/0"
    assert requests == ["archive.example"]
    assert not list(tmp_path.iterdir())
    book.links.append(Link("Independent source", "https://other.example/book.pdf", "external"))
    # Keep it within the bounded list of eligible routes.
    book.links.pop(1)
    requests.clear()
    with Client(BASE, transport=httpx.MockTransport(handler)) as client:
        result = download_record(client, book, directory=tmp_path)
    assert result["md5"] == MD5
    assert requests == ["archive.example", "other.example"]


def test_auto_download_mirror_fallback_keeps_exact_record_and_cookie_scope(tmp_path):
    from anna.client import DEFAULT_MIRRORS
    from anna.session import import_file

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n.annas-archive.gd\tTRUE\t/\tTRUE\t0\tclearance\tprivate-gd\n"
    )
    import_file(DEFAULT_MIRRORS[0], cookie_file, "Firefox/test")
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host != "annas-archive.pk":
            return httpx.Response(403, text="<title>DDOS-GUARD</title>")
        assert MD5 in request.url.path
        assert request.url.path.endswith("/0/7")
        assert "cookie" not in request.headers
        return httpx.Response(200, content=DATA)

    with Client(transport=httpx.MockTransport(handle)) as client:
        result = client.download(
            DEFAULT_MIRRORS[0] + "/slow_download/" + MD5 + "/0/7",
            directory=tmp_path,
            expected_md5=MD5,
        )
        assert client.base_url == "https://annas-archive.pk"
    assert result["md5"] == MD5
    assert [request.url.host for request in requests] == [
        "annas-archive.gd",
        "annas-archive.gd",
        "annas-archive.gl",
        "annas-archive.gl",
        "annas-archive.pk",
    ]


def test_explicit_download_mirror_stays_pinned_after_check(tmp_path):
    from anna.errors import ChallengeError

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(403, text="<title>DDOS-GUARD</title>")

    with Client(BASE, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ChallengeError):
            client.download(BASE + "/slow_download/" + MD5 + "/0/0", directory=tmp_path)
    assert len(requests) == 2
    assert all(request.url.host == "archive.example" for request in requests)


def test_rate_limit_on_auto_download_never_switches_mirror(tmp_path):
    from anna.client import DEFAULT_MIRRORS

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429)

    with Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RateLimitError):
            client.download(
                DEFAULT_MIRRORS[0] + "/slow_download/" + MD5 + "/0/0", directory=tmp_path
            )
    assert len(requests) == 1


def test_later_slow_routes_use_mirror_selected_during_previous_download(tmp_path):
    from anna.client import DEFAULT_MIRRORS

    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host != "annas-archive.pk":
            return httpx.Response(403, text="<title>DDOS-GUARD</title>")
        if request.url.path.endswith("/0/0"):
            raise httpx.ReadTimeout("route offline", request=request)
        return httpx.Response(200, content=DATA)

    book = record(2)
    for i, link in enumerate(book.links):
        link.url = DEFAULT_MIRRORS[0] + "/slow_download/" + MD5 + f"/0/{i}"
    with Client(transport=httpx.MockTransport(handle)) as client:
        result = download_record(client, book, directory=tmp_path)
    assert result["md5"] == MD5
    assert requests[-1].url.host == "annas-archive.pk"
    assert requests[-1].url.path.endswith("/0/1")
    assert len(requests) == 6


def test_working_download_page_mirror_is_remembered_even_when_file_server_stalls(tmp_path):
    from anna.client import DEFAULT_MIRRORS
    from anna.session import preferred_origin

    def handle(request):
        if request.url.host == "files.example":
            raise httpx.ReadTimeout("upstream stalled", request=request)
        if request.url.host != "annas-archive.pk":
            return httpx.Response(403, text="<title>DDOS-GUARD</title>")
        return httpx.Response(
            200,
            text='<html><a download href="https://files.example/book.pdf">Download</a></html>',
            headers={"content-type": "text/html"},
        )

    with Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.download(
                DEFAULT_MIRRORS[0] + "/slow_download/" + MD5 + "/0/0", directory=tmp_path
            )
    assert preferred_origin(DEFAULT_MIRRORS) == "https://annas-archive.pk"
    assert not list(tmp_path.iterdir())


def test_download_route_with_query_is_not_copied_to_another_mirror(tmp_path):
    from anna.client import DEFAULT_MIRRORS
    from anna.errors import ChallengeError

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(403, text="<title>DDOS-GUARD</title>")

    with Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ChallengeError):
            client.download(
                DEFAULT_MIRRORS[0] + "/slow_download/" + MD5 + "/0/0?token=site-specific",
                directory=tmp_path,
            )
    assert len(requests) == 2
    assert all(request.url.host == "annas-archive.gd" for request in requests)


def test_gateway_timeout_reports_actual_file_host_without_signed_url():
    def handle(request):
        if request.url.host == "archive.example":
            return httpx.Response(
                200,
                text='<a download href="https://files.example/private/file?token=secret">Download</a>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(504, text="<html>Gateway Time-out</html>")

    with Client(BASE, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(DownloadSourcesError) as error:
            download_record(client, record(1))
    message = str(error.value)
    assert "HTTP 504 (upstream timeout) (files.example)" in message
    assert "secret" not in message and "/private" not in message
    assert not error.value.verification_url
