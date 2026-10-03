import hashlib
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from anna import cli
from anna.client import DEFAULT_MIRRORS, Client
from anna.config import load_config
from anna.errors import AnnaError, RateLimitError
from anna.parsing import Book, Link

FIXTURES = Path(__file__).parent / "fixtures"
CHALLENGE = "<title>DDoS-Guard</title>"


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    monkeypatch.setenv("ANNA_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("ANNA_BASE_URL", raising=False)


@pytest.mark.parametrize("failure", ["challenge", "layout", "network", "http"])
def test_mirror_fallback_preserves_filters(failure):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host.endswith(".gd"):
            if failure == "network":
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(
                503 if failure == "http" else 200,
                text=CHALLENGE if failure == "challenge" else "<h1>Unrecognised layout</h1>",
            )
        assert request.url.params.get_list("ext") == ["epub"]
        assert request.url.params["q"] == "Pride and Prejudice"
        return httpx.Response(200, text=(FIXTURES / "search-current.html").read_text())

    with Client(transport=httpx.MockTransport(handler)) as client:
        books = client.search("Pride and Prejudice", ext=("epub",))
        assert books[0].title == "Pride and Prejudice"
        assert client.base_url.endswith(".gl")
        client.search("Pride and Prejudice", ext=("epub",))
    assert requests[0].url.host.endswith(".gd")
    assert requests[-1].url.host.endswith(".gl")


def test_blocked_gl_falls_back_to_gd(monkeypatch):
    monkeypatch.setattr(
        "anna.client.DEFAULT_MIRRORS", ("https://annas-archive.gl", "https://annas-archive.gd")
    )

    def handler(request):
        if request.url.host.endswith(".gl"):
            return httpx.Response(403, text=CHALLENGE)
        return httpx.Response(200, text=(FIXTURES / "search-current.html").read_text())

    with Client(transport=httpx.MockTransport(handler)) as client:
        assert client.search("Austen")
        assert client.base_url.endswith(".gd")


def test_all_mirrors_fail_bounded_and_clear():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=CHALLENGE)

    with Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AnnaError, match="No working mirror"):
            client.search("Austen")
    assert len(requests) == 2 * len(DEFAULT_MIRRORS)


def test_rate_limit_does_not_switch_mirrors():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429)

    with Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RateLimitError):
            client.search("Austen")
    assert len(requests) == 1


def test_config_save_reload_and_invalid(monkeypatch, tmp_path):
    runner = CliRunner()
    assert runner.invoke(cli.main, ["config", "set", "directory", str(tmp_path)]).exit_code == 0
    assert load_config()["directory"] == str(tmp_path)
    shown = runner.invoke(cli.main, ["config", "show"])
    assert str(tmp_path) in shown.output
    Path(tmp_path / "config.json").write_text("[]")
    error = runner.invoke(cli.main, ["--json", "search", "Austen"])
    assert error.exit_code == 1
    assert "Invalid preferences" in json.loads(error.stdout)["error"]["message"]


def install_client(monkeypatch, tmp_path):
    data = b"synthetic public-domain book"
    digest = hashlib.md5(data).hexdigest()
    calls = []

    class FakeClient:
        def __init__(self, **options):
            calls.append(("options", options))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def search(self, query, **filters):
            calls.append(("search", query, filters))
            return [
                Book("1" * 32, "A variation", "", author="Someone Else"),
                Book(digest, "Pride and Prejudice", "", author="Austen, Jane", format="epub"),
            ]

        def info(self, md5):
            calls.append(("info", md5))
            return Book(
                md5,
                "Pride and Prejudice",
                "",
                links=[Link("free", "https://files.example/book.epub", "external")],
            )

        def download(self, target, output, directory, expected_md5, **options):
            calls.append(("download", target, directory, expected_md5))
            path = output or directory / "book.epub"
            return {"path": str(path), "bytes": len(data), "md5": expected_md5}

    monkeypatch.setattr(cli, "Client", FakeClient)
    monkeypatch.setattr(cli, "input_is_terminal", lambda: False)
    return calls, digest


def test_get_author_defaults_and_json_selection(monkeypatch, tmp_path):
    calls, digest = install_client(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        cli.main,
        [
            "get",
            "Pride and Prejudice",
            "--author",
            "Jane Austen",
            "--format",
            "epub",
            "--choose",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["md5"] == digest
    search = next(call for call in calls if call[0] == "search")
    assert search[1] == "Pride and Prejudice Jane Austen"
    assert search[2]["lang"] == ("en",)
    download = next(call for call in calls if call[0] == "download")
    assert download[2] == Path.home() / "Books"
    assert download[3] == digest


@pytest.mark.parametrize(
    "arguments, message",
    [
        ([], "Choose a result"),
        (["--choose", "3"], "out of range"),
        (["--author", "Nonexistent", "--choose", "1"], "No matching"),
        (["--json"], "require --choose"),
    ],
)
def test_get_selection_errors_do_not_download(monkeypatch, tmp_path, arguments, message):
    calls, _ = install_client(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.main, ["get", "Pride", *arguments])
    assert result.exit_code == 1
    assert message in result.output
    assert not any(call[0] == "download" for call in calls)


@pytest.mark.parametrize("number, downloaded", [("2", True), ("0", False)])
def test_interactive_search_and_cancel(monkeypatch, tmp_path, number, downloaded):
    calls, digest = install_client(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "input_is_terminal", lambda: True)
    result = CliRunner().invoke(cli.main, ["search", "Pride", "--select"], input=number + "\n")
    assert result.exit_code == (0 if downloaded else 1), result.output
    assert any(call[0] == "download" for call in calls) == downloaded
    if downloaded:
        assert next(call for call in calls if call[0] == "info")[1] == digest


def test_preferences_and_explicit_overrides(monkeypatch, tmp_path):
    calls, _ = install_client(monkeypatch, tmp_path)
    runner = CliRunner()
    for key, value in (("format", "pdf"), ("language", "fr"), ("directory", str(tmp_path))):
        assert runner.invoke(cli.main, ["config", "set", key, value]).exit_code == 0
    assert runner.invoke(cli.main, ["get", "Pride", "--choose", "2"]).exit_code == 0
    assert next(call for call in calls if call[0] == "search")[2]["ext"] == ("pdf",)
    assert next(call for call in calls if call[0] == "download")[2] == tmp_path
    calls.clear()
    result = runner.invoke(
        cli.main,
        [
            "--base-url",
            "https://custom.example",
            "get",
            "Pride",
            "--choose",
            "2",
            "--format",
            "epub",
            "--lang",
            "en",
            "-d",
            str(tmp_path / "override"),
        ],
    )
    assert result.exit_code == 0
    assert (
        next(call for call in calls if call[0] == "options")[1]["base_url"]
        == "https://custom.example"
    )
    assert next(call for call in calls if call[0] == "search")[2]["ext"] == ("epub",)
    assert next(call for call in calls if call[0] == "download")[2] == tmp_path / "override"
