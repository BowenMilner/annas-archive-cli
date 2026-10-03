"""Private, site-scoped browser sessions; never read unrelated browser cookies."""

import configparser
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from anna.config import config_path
from anna.errors import AnnaError


def paths(origin):
    key = hashlib.sha256(origin.rstrip("/").encode()).hexdigest()[:20]
    folder = config_path().parent / "sessions"
    return folder / (key + ".cookies.txt"), folder / (key + ".json")


def preferred_origin(allowed):
    try:
        value = json.loads((config_path().parent / "sessions/preferred.json").read_text())
        return value if value in allowed else None
    except (OSError, ValueError):
        return None


def remember_origin(origin):
    path = config_path().parent / "sessions/preferred.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".preferred-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(origin, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def scoped(jar, origin):
    host = urlsplit(origin).hostname
    result = http.cookiejar.MozillaCookieJar()
    for cookie in jar:
        if cookie.domain.lstrip(".") == host and not cookie.is_expired():
            result.set_cookie(cookie)
    return result


def load_session(origin):
    cookie_path, metadata = paths(origin)
    jar = http.cookiejar.MozillaCookieJar()
    agent = None
    try:
        if cookie_path.exists():
            jar.load(str(cookie_path), ignore_discard=True)
            jar = scoped(jar, origin)
            data = json.loads(metadata.read_text())
            if not isinstance(data, dict):
                return http.cookiejar.MozillaCookieJar(), None
            agent = data.get("user_agent") if data.get("origin") == origin else None
    except (OSError, ValueError, http.cookiejar.LoadError):
        return http.cookiejar.MozillaCookieJar(), None
    return jar, agent if isinstance(agent, str) and agent else None


def save_session(origin, jar, user_agent):
    jar = scoped(jar, origin)
    if not list(jar):
        return False
    cookie_path, metadata = paths(origin)
    cookie_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=cookie_path.parent)
    os.close(fd)
    try:
        jar.save(temporary, ignore_discard=True)
        os.replace(temporary, cookie_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".agent-", dir=metadata.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"origin": origin, "user_agent": user_agent}, stream)
        os.replace(temporary, metadata)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def import_file(origin, path, user_agent):
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        raise AnnaError(
            "Cannot read that session file. Choose a Netscape cookies.txt export."
        ) from exc
    for cookie in jar:
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
    if not save_session(origin, jar, user_agent):
        raise AnnaError("No current cookies for this site were found in that file.")


def firefox_profile():
    root = Path.home() / ".mozilla/firefox"
    ini = configparser.ConfigParser()
    try:
        ini.read(root / "profiles.ini")
    except configparser.Error as exc:
        raise AnnaError(
            "Cannot read Firefox profiles. Use the session-file option instead."
        ) from exc
    for section in ini.sections():
        if section.startswith("Install") and ini.has_option(section, "Default"):
            return root / ini[section]["Default"]
    for section in ini.sections():
        if section.startswith("Profile") and ini[section].get("Default") == "1":
            path = Path(ini[section]["Path"])
            return root / path if ini[section].get("IsRelative") == "1" else path
    raise AnnaError("No default Firefox profile found. Use the session-file option instead.")


def firefox_agent():
    executable = shutil.which("firefox")
    if not executable:
        raise AnnaError("Firefox was not found. Use the session-file option instead.")
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5)
    match = re.search(r"Firefox\s+(\d+)", result.stdout)
    if not match:
        raise AnnaError("Cannot read the Firefox version. Enter its User-Agent under Advanced.")
    version = match[1] + ".0"
    return f"Mozilla/5.0 (X11; Linux x86_64; rv:{version}) Gecko/20100101 Firefox/{version}"


def import_firefox(origin, user_agent=None):
    profile = firefox_profile()
    database = profile / "cookies.sqlite"
    host = urlsplit(origin).hostname
    jar = http.cookiejar.MozillaCookieJar()
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            # The query excludes every other site's cookies, including signed-in accounts.
            rows = db.execute(
                "SELECT name,value,host,path,expiry,isSecure,isHttpOnly FROM moz_cookies "
                "WHERE host IN (?, ?) AND originAttributes IN ('', ?)",
                (host, "." + str(host), f"^partitionKey=({urlsplit(origin).scheme},{host})"),
            ).fetchall()
    except sqlite3.Error as exc:
        raise AnnaError(
            "Cannot read this site's Firefox session. Finish the browser check first."
        ) from exc
    for name, value, domain, path, expiry, secure, http_only in rows:
        jar.set_cookie(
            http.cookiejar.Cookie(
                0,
                name,
                value,
                None,
                False,
                domain,
                True,
                domain.startswith("."),
                path,
                True,
                bool(secure),
                expiry or None,
                not bool(expiry),
                None,
                None,
                {"HTTPOnly": ""} if http_only else {},
                False,
            )
        )
    agent = user_agent or firefox_agent()
    if not save_session(origin, jar, agent):
        raise AnnaError(
            "No current session for this site was found. Finish its check in Firefox first."
        )
    return agent
