import hashlib
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from anna import cli
from anna.client import Client, safe_filename
from anna.errors import AnnaError, ChallengeError, DownloadWaitError, ParseError
from anna.parsing import parse_info, parse_search, record_id

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://archive.example"
MD5 = "1" * 32
BOOK = b"%PDF-1.7\npublic-domain test content\n%%EOF"
CHALLENGE = '<title>DDoS-Guard</title><script src="/ddos-guard/check.js"></script>'


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def client(handler):
    return Client(BASE, transport=httpx.MockTransport(handler))


def test_search_hidden_results_and_metadata():
    books = parse_search(fixture("search"), BASE)
    assert len(books) == 2
    assert books[0].title == "Pride & Prejudice"
    assert books[0].author == "Jane Austen"
    assert books[0].publisher == "Example Press, 1813"
    assert (books[1].language, books[1].format, books[1].size) == ("zh", "pdf", "5.6 MB")
    assert books[0].cover_url == BASE + "/covers/one.jpg"


def test_info_links_and_search_icons():
    book = parse_info(fixture("info"), BASE, MD5)
    assert book.title == "Pride & Prejudice"
    assert book.author == "Jane Austen"
    assert [link.kind for link in book.links] == ["fast", "slow", "external"]
    assert book.links[1].url.startswith(BASE + "/slow_download/")
    assert book.description == "Description A novel."


def test_current_search_cards_skip_cover_placeholders():
    books = parse_search(fixture("search-current"), BASE)
    assert len(books) == 1
    book = books[0]
    assert book.md5 == "51d2b22ca12a8b470b51f543298b34c9"
    assert book.title == "Pride and Prejudice"
    assert book.author == "Austen, Jane"
    assert book.publisher == "Project Gutenberg, 1998"
    assert (book.language, book.format, book.size) == ("en", "epub", "0.3MB")
    assert "Save" not in book.metadata
    assert book.cover_url == ""


def test_current_record_metadata_and_sources():
    md5 = "51d2b22ca12a8b470b51f543298b34c9"
    book = parse_info(fixture("info-current"), BASE, md5)
    assert book.title == "Pride and Prejudice"
    assert book.author == "Austen, Jane"
    assert book.publisher == "Project Gutenberg, 1998"
    assert (book.language, book.format, book.size) == ("en", "epub", "0.3MB")
    assert book.url == BASE + "/md5/" + md5
    assert [link.kind for link in book.links[:2]] == ["fast", "slow"]


def test_challenge_retry_preserves_query_and_session():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params["q"] == "中文 & Austen"
        assert request.url.params.get_list("ext") == ["epub", "pdf"]
        if len(requests) == 1:
            return httpx.Response(
                403, text=CHALLENGE, headers={"set-cookie": "session=test; Path=/"}
            )
        assert request.headers["cookie"] == "session=test"
        assert request.url.query.endswith(b"check=%31")
        return httpx.Response(200, text=fixture("search-current"))

    with client(handler) as api:
        assert api.search("中文 & Austen", ext=("epub", "pdf"))[0].title == "Pride and Prejudice"
    assert len(requests) == 2


def test_challenge_retry_is_bounded():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=CHALLENGE)

    with client(handler) as api, pytest.raises(ChallengeError):
        api.search("Austen")
    assert len(requests) == 2


def test_current_slow_source_to_verified_file(tmp_path):
    md5 = hashlib.md5(BOOK).hexdigest()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.startswith("/md5/"):
            return httpx.Response(200, text=fixture("info-current"))
        if request.url.raw_path.startswith(b"/slow_download/"):
            return httpx.Response(403, text=CHALLENGE)
        if request.url.raw_path.startswith(b"/slow%5Fdownload/"):
            return httpx.Response(
                200,
                text='<a href="/file">📚 Download now</a>',
                headers={"content-type": "text/html"},
            )
        assert request.url.path == "/file"
        return httpx.Response(200, content=BOOK)

    with client(handler) as api:
        book = api.info("51d2b22ca12a8b470b51f543298b34c9")
        result = api.download(book.links[1].url, directory=tmp_path, expected_md5=md5)
    assert Path(result["path"]).read_bytes() == BOOK
    assert result["md5"] == md5
    assert len(requests) == 4


@pytest.mark.parametrize("sleep_overrun", [0, 2.4])
def test_free_source_countdown_then_download(monkeypatch, tmp_path, sleep_overrun):
    now = [0.0]
    waits = []
    requests = []

    def sleep(seconds):
        now[0] += seconds + sleep_overrun

    monkeypatch.setattr("anna.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("anna.client.time.sleep", sleep)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text='<span class="js-partner-countdown">2</span>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, content=BOOK)

    with client(handler) as api:
        result = api.download(
            BASE + "/slow_download/" + MD5 + "/0/0",
            directory=tmp_path,
            expected_md5=hashlib.md5(BOOK).hexdigest(),
            max_wait=5,
            wait_progress=waits.append,
        )
    assert waits == ([3, 2, 1, 0] if sleep_overrun == 0 else [3, 0])
    assert now[0] == (3 if sleep_overrun == 0 else 3.4)
    assert len(requests) == 2
    assert Path(result["path"]).read_bytes() == BOOK


@pytest.mark.parametrize("max_wait", [0, 2])
def test_free_source_countdown_respects_budget(max_wait, tmp_path, monkeypatch):
    def sleep(_):
        pytest.fail("An over-budget countdown must not sleep.")

    monkeypatch.setattr("anna.client.time.sleep", sleep)
    with (
        client(
            lambda _: httpx.Response(
                200,
                text='<span class="js-partner-countdown">10</span>',
                headers={"content-type": "text/html"},
            )
        ) as api,
        pytest.raises(DownloadWaitError, match="10 seconds"),
    ):
        api.download(BASE + "/slow_download/" + MD5 + "/0/0", directory=tmp_path, max_wait=max_wait)
    assert list(tmp_path.iterdir()) == []


def test_download_challenge_retry_stays_on_configured_mirror(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, text=CHALLENGE)

    with client(handler) as api, pytest.raises(ChallengeError):
        api.download("https://external.example/slow_download/" + MD5 + "/0/0", directory=tmp_path)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "html,error",
    [
        (CHALLENGE, ChallengeError),
        ("<title>Loading...</title>Click for continue", ParseError),
        ("This domain may be for sale", ParseError),
        ('<form action="/search">Sign in</form>', ParseError),
    ],
)
def test_non_results_are_errors(html, error):
    with pytest.raises(error):
        parse_search(html, BASE)


def test_real_empty_result():
    assert parse_search("<h1>No files found.</h1>", BASE) == []


def test_record_id_validation():
    assert record_id(BASE + "/md5/" + "A" * 32 + "?x=1") == "a" * 32
    with pytest.raises(AnnaError):
        record_id("this is not a hash")
    with pytest.raises(AnnaError):
        record_id("https://[")


def test_search_request():
    def handler(request):
        assert request.url.params.get_list("ext") == ["epub", "pdf"]
        assert request.url.params["q"] == "中文 & Austen"
        assert request.url.params["page"] == "2"
        assert request.url.params["display"] == ""
        return httpx.Response(200, text=fixture("search"))

    with client(handler) as api:
        assert len(api.search("中文 & Austen", ext=("epub", "pdf"), page=2)) == 2


@pytest.mark.parametrize(
    "status,body,error",
    [
        (403, CHALLENGE, ChallengeError),
        (429, "limited", AnnaError),
        (404, "not found", AnnaError),
        (200, CHALLENGE, ChallengeError),
    ],
)
def test_http_errors(status, body, error):
    with client(lambda r: httpx.Response(status, text=body)) as api:
        with pytest.raises(error):
            api.search("test")


def test_download_redirect_and_checksum(tmp_path):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/file"})
        return httpx.Response(
            200,
            content=BOOK,
            headers={
                "content-type": "application/pdf",
                "content-disposition": "attachment; filename*=UTF-8''..%2F%E4%B9%A6.pdf",
            },
        )

    with client(handler) as api:
        result = api.download(
            BASE + "/start", directory=tmp_path, expected_md5=hashlib.md5(BOOK).hexdigest()
        )
    assert Path(result["path"]).name == "书.pdf"
    assert Path(result["path"]).read_bytes() == BOOK
    assert result["bytes"] == len(BOOK)
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "body,headers",
    [
        (CHALLENGE.encode(), {"content-type": "text/html"}),
        (b"<html>Login</html>", {"content-type": "application/octet-stream"}),
        (b'{"error":"denied"}', {"content-type": "application/json"}),
        (b"", {}),
        (BOOK, {"content-length": "1000"}),
    ],
)
def test_invalid_download_never_publishes(tmp_path, body, headers):
    with client(lambda r: httpx.Response(200, content=body, headers=headers)) as api:
        with pytest.raises(AnnaError):
            api.download(BASE + "/file", directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_hash_failure_and_no_overwrite(tmp_path):
    output = tmp_path / "book.pdf"
    with client(lambda r: httpx.Response(200, content=BOOK)) as api:
        with pytest.raises(AnnaError, match="MD5"):
            api.download(BASE + "/file", output=output, expected_md5=MD5)
        assert list(tmp_path.iterdir()) == []
        output.write_bytes(b"original")
        with pytest.raises(AnnaError, match="already exists"):
            api.download(BASE + "/file", output=output)
        assert output.read_bytes() == b"original"


def test_explicit_download_button(tmp_path):
    def handler(request):
        if request.url.path == "/landing":
            return httpx.Response(
                200,
                text='<a id="download" href="/file">Download</a>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, content=BOOK)

    with client(handler) as api:
        result = api.download(BASE + "/landing", directory=tmp_path)
    assert Path(result["path"]).read_bytes() == BOOK


def test_interrupted_download_cleans_temp_file(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield BOOK * 2000  # Enough data to open the temporary file.
            raise httpx.ReadError("connection lost")

    with client(lambda r: httpx.Response(200, stream=BrokenStream())) as api:
        with pytest.raises(httpx.ReadError):
            api.download(BASE + "/file", directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_cookie_domain_isolation_on_redirect(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\narchive.example\tFALSE\t/\tTRUE\t0\tsession\ttest-value\n"
        "archive.example\tFALSE\t/\tTRUE\t1\texpired\tdo-not-send\n"
    )

    def handler(request):
        if request.url.host == "archive.example":
            assert request.headers.get("cookie") == "session=test-value"
            return httpx.Response(302, headers={"location": "https://files.example/file"})
        assert "cookie" not in request.headers
        return httpx.Response(200, content=BOOK)

    with Client(BASE, cookies=cookie_file, transport=httpx.MockTransport(handler)) as api:
        api.download(BASE + "/file", directory=tmp_path)


def test_download_http_challenge(tmp_path):
    with client(lambda r: httpx.Response(403, text=CHALLENGE)) as api:
        with pytest.raises(ChallengeError):
            api.download(BASE + "/file", directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_filename_sanitizing():
    assert safe_filename("../../file.pdf") == "file.pdf"
    assert safe_filename("..\\file.pdf") == "file.pdf"
    assert safe_filename("\x00../") == "download.bin"
    assert len(safe_filename("书" * 300).encode()) <= 180


def test_cli_json_and_error(monkeypatch):
    monkeypatch.setattr(
        cli, "Client", lambda **kw: client(lambda r: httpx.Response(200, text=fixture("search")))
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["search", "Austen", "--limit", "1", "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 1
    monkeypatch.setattr(
        cli, "Client", lambda **kw: client(lambda r: httpx.Response(403, text=CHALLENGE))
    )
    for args in [["--json", "doctor"], ["doctor", "--json"]]:
        result = runner.invoke(cli.main, args)
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["type"] == "ChallengeError"


def test_cli_record_download_selects_source_and_verifies(tmp_path, monkeypatch):
    digest = hashlib.md5(BOOK).hexdigest()

    def handler(request):
        if request.url.path.startswith("/md5/"):
            return httpx.Response(200, text=fixture("info"))
        assert request.url.host == "files.example"
        return httpx.Response(200, content=BOOK)

    monkeypatch.setattr(cli, "Client", lambda **kw: client(handler))
    output = tmp_path / "book.pdf"
    result = CliRunner().invoke(
        cli.main, ["download", digest, "--source", "3", "-o", str(output), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["md5"] == digest
    assert output.read_bytes() == BOOK


def test_cli_countdown_progress_keeps_stdout_json(tmp_path, monkeypatch):
    requests = []
    digest = hashlib.md5(BOOK).hexdigest()
    now = [0.0]

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text='<span class="js-partner-countdown">1</span>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, content=BOOK)

    monkeypatch.setattr(cli, "Client", lambda **kw: client(handler))
    monkeypatch.setattr("anna.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "anna.client.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    result = CliRunner().invoke(
        cli.main,
        [
            "download",
            BASE + "/slow_download/" + MD5 + "/0/0",
            "--md5",
            digest,
            "-o",
            str(tmp_path / "book.pdf"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["md5"] == digest
    assert result.stderr == "Free source countdown: waiting 2 seconds...\n"


@pytest.mark.parametrize(
    "args",
    [
        ["search", "test", "--page", "0"],
        ["search", "test", "--limit", "0"],
        ["--timeout", "0", "doctor"],
    ],
)
def test_bad_options(args):
    assert CliRunner().invoke(cli.main, args).exit_code == 2
