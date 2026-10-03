import http.cookiejar
import os
import sqlite3
import time

import httpx
import pytest

from anna.client import DEFAULT_MIRRORS, Client
from anna.errors import AnnaError, ChallengeError
from anna.session import import_firefox, load_session, paths, preferred_origin, remember_origin


def test_firefox_import_is_site_scoped_private_and_reused(tmp_path, monkeypatch):
    profile = tmp_path / "firefox"
    profile.mkdir()
    with sqlite3.connect(profile / "cookies.sqlite") as db:
        db.execute(
            "CREATE TABLE moz_cookies "
            "(name,value,host,path,expiry,isSecure,isHttpOnly,originAttributes)"
        )
        for host, value, attrs in [
            (".annas-archive.gd", "site-session", ""),
            ("hotmail.com", "never-read-this-account", ""),
            ("annas-archive.gd.evil.test", "wrong-site", ""),
            (".annas-archive.gd", "container-session", "^userContextId=1"),
        ]:
            db.execute(
                "INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?)",
                ("verification", value, host, "/", int(time.time()) + 3600, 1, 1, attrs),
            )
    monkeypatch.setattr("anna.session.firefox_profile", lambda: profile)
    monkeypatch.setattr("anna.session.firefox_agent", lambda: "Firefox/test")
    assert import_firefox(DEFAULT_MIRRORS[0]) == "Firefox/test"
    jar, agent = load_session(DEFAULT_MIRRORS[0])
    assert agent == "Firefox/test"
    assert [(c.domain, c.value) for c in jar] == [(".annas-archive.gd", "site-session")]
    cookie_path, metadata = paths(DEFAULT_MIRRORS[0])
    if os.name != "nt":
        assert cookie_path.stat().st_mode & 0o777 == 0o600
        assert metadata.stat().st_mode & 0o777 == 0o600
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="<html>ok</html>")

    for _ in range(2):
        with Client(transport=httpx.MockTransport(handler)) as client:
            client.page("/test")
            client.http.get("https://unrelated.example/test")
    assert requests[0].headers["cookie"] == "verification=site-session"
    assert requests[0].headers["user-agent"] == "Firefox/test"
    assert "cookie" not in requests[1].headers
    assert requests[2].headers["cookie"] == "verification=site-session"


def test_server_session_updates_survive_a_new_client():
    def first(request):
        return httpx.Response(
            200, text="OK", headers={"set-cookie": "challenge=renewed; Path=/; Secure"}
        )

    with Client(transport=httpx.MockTransport(first)) as client:
        client.page("/test")
    jar, _ = load_session(DEFAULT_MIRRORS[0])
    assert [(c.name, c.value) for c in jar] == [("challenge", "renewed")]


def test_explicit_cookie_file_remains_pinned(tmp_path):
    file = tmp_path / "cookies.txt"
    http.cookiejar.MozillaCookieJar().save(str(file))
    with Client(
        cookies=file,
        user_agent="Pinned/test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    ) as client:
        assert client.http.headers["user-agent"] == "Pinned/test"


def test_verified_mirror_is_remembered_and_explicit_override_wins():
    remember_origin(DEFAULT_MIRRORS[1])
    assert preferred_origin(DEFAULT_MIRRORS) == DEFAULT_MIRRORS[1]
    with Client() as client:
        assert client.base_url == DEFAULT_MIRRORS[1]
    with Client(DEFAULT_MIRRORS[0]) as client:
        assert client.base_url == DEFAULT_MIRRORS[0]
    remember_origin("https://untrusted.example")
    assert preferred_origin(DEFAULT_MIRRORS) is None


def test_no_site_session_import_is_a_clear_error(tmp_path, monkeypatch):
    profile = tmp_path / "firefox"
    profile.mkdir()
    monkeypatch.setattr("anna.session.firefox_profile", lambda: profile)
    with pytest.raises(AnnaError, match="session database"):
        import_firefox(DEFAULT_MIRRORS[0], "Test")


def test_challenge_keeps_origin_for_guided_retry():
    with Client(
        DEFAULT_MIRRORS[1],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<title>DDOS-GUARD</title>")
        ),
    ) as client:
        with pytest.raises(ChallengeError) as error:
            client.info("a" * 32)
    assert error.value.origin == DEFAULT_MIRRORS[1]
    assert "anna connect" in str(error.value)


def test_download_source_session_is_reused_and_agent_is_site_scoped(tmp_path):
    from anna.session import save_session

    origin = "https://source.example"
    jar = http.cookiejar.MozillaCookieJar()
    jar.set_cookie(
        http.cookiejar.Cookie(
            0,
            "clearance",
            "saved",
            None,
            False,
            "source.example",
            False,
            False,
            "/",
            True,
            True,
            None,
            True,
            None,
            None,
            {},
            False,
        )
    )
    save_session(origin, jar, "Source Firefox/test")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"lawful book")

    with Client(transport=httpx.MockTransport(handle)) as client:
        client.download(origin + "/book.epub", directory=tmp_path)
        client.page("/catalogue")
    assert requests[0].headers["cookie"] == "clearance=saved"
    assert requests[0].headers["user-agent"] == "Source Firefox/test"
    assert "cookie" not in requests[1].headers
    assert requests[1].headers["user-agent"] != "Source Firefox/test"


def test_corrupt_session_metadata_is_ignored():
    cookie_path, metadata = paths(DEFAULT_MIRRORS[0])
    cookie_path.parent.mkdir(parents=True)
    http.cookiejar.MozillaCookieJar().save(str(cookie_path))
    metadata.write_text("null")
    jar, agent = load_session(DEFAULT_MIRRORS[0])
    assert list(jar) == [] and agent is None


@pytest.mark.parametrize("wal", [False, True])
def test_live_firefox_lock_imports_only_site_and_committed_wal(tmp_path, monkeypatch, wal):
    profile = tmp_path / "live-firefox"
    profile.mkdir()
    with sqlite3.connect(profile / "cookies.sqlite") as browser:
        browser.execute("PRAGMA locking_mode=EXCLUSIVE")
        if wal:
            browser.execute("PRAGMA journal_mode=WAL")
        browser.execute(
            "CREATE TABLE moz_cookies "
            "(name,value,host,path,expiry,isSecure,isHttpOnly,originAttributes)"
        )
        browser.commit()
        browser.execute(
            "INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?)",
            ("clearance", "test", ".annas-archive.gd", "/", int(time.time()) + 3600, 1, 1, ""),
        )
        browser.execute(
            "INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?)",
            ("unrelated", "never-import", "unrelated.example", "/", 0, 1, 1, ""),
        )
        browser.commit()
        monkeypatch.setattr("anna.session.firefox_profile", lambda: profile)
        monkeypatch.setattr("anna.session.firefox_agent", lambda: "Firefox/test")
        assert import_firefox(DEFAULT_MIRRORS[0]) == "Firefox/test"
        jar, _ = load_session(DEFAULT_MIRRORS[0])
        assert [(c.name, c.value) for c in jar] == [("clearance", "test")]


def test_signed_file_link_keeps_catalogue_browser_identity_without_cookies(tmp_path):
    import hashlib

    from anna.session import save_session

    jar = httpx.Cookies()
    jar.set("clearance", "catalogue-only", domain="annas-archive.gd", path="/")
    save_session(DEFAULT_MIRRORS[0], jar.jar, "Firefox/catalogue")
    payload = b"lawful exact archive file"
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "annas-archive.gd":
            return httpx.Response(
                200,
                text='<a download href="https://file.example/signed/book.epub">Download</a>',
                headers={"content-type": "text/html"},
            )
        if request.headers["user-agent"] != "Firefox/catalogue":
            return httpx.Response(403)
        return httpx.Response(200, content=payload)

    with Client(transport=httpx.MockTransport(handle)) as client:
        result = client.download(
            DEFAULT_MIRRORS[0] + "/source",
            directory=tmp_path,
            expected_md5=hashlib.md5(payload).hexdigest(),
        )
    assert result["bytes"] == len(payload)
    assert requests[0].headers["cookie"] == "clearance=catalogue-only"
    assert "cookie" not in requests[1].headers
    assert requests[1].headers["user-agent"] == "Firefox/catalogue"


def test_download_challenge_keeps_exact_page_for_browser_setup():
    target = DEFAULT_MIRRORS[0] + "/slow_download/" + "a" * 32 + "/0/7"
    with Client(
        DEFAULT_MIRRORS[0],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(403, text="<title>DDoS-Guard</title>")
        ),
    ) as client:
        with pytest.raises(ChallengeError) as error:
            client.download(target)
    assert error.value.origin == DEFAULT_MIRRORS[0]
    assert error.value.verification_url.endswith("/0/7")
    assert "/slow%5Fdownload/" in error.value.verification_url


@pytest.mark.parametrize("prior_success", [False, True])
def test_blocked_response_does_not_overwrite_imported_browser_session(prior_success):
    from anna.session import import_file

    cookie_path, metadata = paths(DEFAULT_MIRRORS[0])
    cookie_path.parent.mkdir(parents=True)
    cookie_path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".annas-archive.gd\tTRUE\t/\tTRUE\t0\tclearance\tbrowser-good\n"
    )
    import_file(DEFAULT_MIRRORS[0], cookie_path, "Firefox/test")
    before = (cookie_path.read_bytes(), metadata.read_bytes())

    def handle(request):
        if request.url.path == "/working":
            return httpx.Response(200, text="<html>Catalogue</html>")
        return httpx.Response(
            403,
            text="<title>DDOS-GUARD</title>",
            headers={"set-cookie": "clearance=blocked-replacement; Path=/; Secure"},
        )

    with Client(DEFAULT_MIRRORS[0], transport=httpx.MockTransport(handle)) as client:
        if prior_success:
            client.page("/working")
        with pytest.raises(ChallengeError):
            client.download(DEFAULT_MIRRORS[0] + "/slow_download/" + "a" * 32 + "/0/0")
    assert (cookie_path.read_bytes(), metadata.read_bytes()) == before


def test_session_check_uses_exact_page_without_following_file_link(tmp_path):
    requests = []
    target = DEFAULT_MIRRORS[0] + "/slow_download/" + "a" * 32 + "/0/7"

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(
            200, text='<html><a download href="https://files.example/book.epub">Download</a></html>'
        )

    with Client(DEFAULT_MIRRORS[0], transport=httpx.MockTransport(handle)) as client:
        client.verify_session(target)
        with pytest.raises(AnnaError, match="selected mirror"):
            client.verify_session("https://other.example/check")
    assert requests == [target]
    assert list(tmp_path.iterdir()) == []
