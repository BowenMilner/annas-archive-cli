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
