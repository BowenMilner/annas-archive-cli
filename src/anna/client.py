import hashlib
import http.cookiejar
import math
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from anna.errors import (
    AnnaError,
    ChallengeError,
    DownloadWaitError,
    FileExistsError,
    HTTPStatusError,
    IntegrityError,
    InvalidInputError,
    RateLimitError,
)
from anna.parsing import Book, document, parse_info, parse_search, record_id, text

DEFAULT_BASE_URL = "https://annas-archive.gl"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise InvalidInputError("Use a valid HTTP(S) URL without embedded credentials.")
    return value


def safe_filename(name: str) -> str:
    name = unquote(name).replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).strip(" .")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.I):
        name = "_" + name
    # Bound UTF-8 bytes to leave room for the temporary filename suffix.
    while len(name.encode("utf-8")) > 180:
        name = name[:-1]
    return name or "download.bin"


def response_filename(response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    ordinary = re.search(r'filename="([^"]+)"|filename=([^;]+)', disposition, re.I)
    if encoded:
        return safe_filename(encoded[1])
    if ordinary:
        return safe_filename(ordinary[1] or ordinary[2])
    return safe_filename(urlsplit(str(response.url)).path.rsplit("/", 1)[-1])


def check_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")
        suffix = f" (Retry-After: {retry})" if retry else ""
        raise RateLimitError(f"Rate limited; retry later{suffix}.")
    if response.status_code >= 400:
        if response.status_code in {401, 403, 503}:
            # Inspect a bounded body; challenge responses may use an error status.
            document(response.text[:1_000_000])
        raise HTTPStatusError(
            f"Server returned HTTP {response.status_code}; "
            "check the mirror, record or access permissions."
        )


def download_link(html: str, base_url: str) -> str:
    soup = document(html)
    countdown = soup.select_one(".js-partner-countdown")
    if countdown is not None:
        value = text(countdown)
        if not re.fullmatch(r"\d{1,6}", value):
            raise AnnaError("Unrecognized download countdown; no file was saved.")
        raise DownloadWaitError(int(value))
    candidates = soup.select("a[download][href], a#download[href]")
    if not candidates:
        candidates = [
            a
            for a in soup.select("a[href]")
            if text(a).lower().removeprefix("📚").strip()
            in {"get", "download now", "download file", "立即下载"}
        ]
    for anchor in candidates:
        target = urljoin(base_url, str(anchor["href"]))
        if target != base_url:
            return http_url(target)
    raise AnnaError(
        "Download returned a web page requiring verification, login or waiting. "
        "Obtain the final file URL in your browser and run anna download URL; "
        "no page was saved."
    )


class Client:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30,
        cookies: Path | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = http_url(base_url).rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.path or parsed.query or parsed.fragment:
            raise AnnaError("--base-url requires an origin URL, e.g. https://annas-archive.gl.")
        jar = http.cookiejar.MozillaCookieJar()
        if cookies:
            try:
                jar.load(str(cookies), ignore_discard=True, ignore_expires=True)
                # Browser exporters commonly encode session expiry as 0;
                # MozillaCookieJar otherwise treats it as January 1970.
                for cookie in jar:
                    if cookie.expires == 0:
                        cookie.expires = None
                        cookie.discard = True
                jar.clear_expired_cookies()
            except (OSError, http.cookiejar.LoadError) as exc:
                raise AnnaError(
                    "Cannot read Cookies; use the Netscape cookies.txt format."
                ) from exc
        self.http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=10,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
            cookies=jar,
            transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def page(self, path: str, params: dict | None = None) -> httpx.Response:
        response = self.http.get(self.base_url + path, params=params)
        try:
            check_status(response)
            document(response.text)
        except ChallengeError:
            # The current public site accepts an equivalent percent-encoded check
            # value. Retry once in this session; this is not a JS solver.
            url = httpx.URL(self.base_url + path, params=params)
            query = url.query + (b"&" if url.query else b"") + b"check=%31"
            response = self.http.get(url.copy_with(query=query))
            check_status(response)
            document(response.text)
        return response

    def search(self, query: str, **filters) -> list[Book]:
        if not query.strip():
            raise AnnaError("Search query cannot be empty.")
        params = {"q": query, "display": "", **{k: v for k, v in filters.items() if v}}
        response = self.page("/search", params)
        return parse_search(response.text, str(response.url))

    def info(self, value: str) -> Book:
        md5 = record_id(value)
        response = self.page(f"/md5/{md5}")
        return parse_info(response.text, str(response.url), md5)

    def download(
        self,
        url: str,
        output: Path | None = None,
        directory: Path = Path("."),
        expected_md5: str | None = None,
        progress: Callable[[int], None] | None = None,
        max_wait: int = 300,
        wait_progress: Callable[[int], None] | None = None,
    ) -> dict:
        if max_wait < 0:
            raise InvalidInputError("--max-wait cannot be negative.")
        deadline = time.monotonic() + max_wait
        parsed = httpx.URL(http_url(url))
        base = httpx.URL(self.base_url)
        same_mirror_slow = (parsed.scheme, parsed.host, parsed.port) == (
            base.scheme,
            base.host,
            base.port,
        ) and bool(re.fullmatch(r"/slow_download/[0-9a-f]{32}/\d+/\d+/?", parsed.path))
        for _ in range(6):
            try:
                return self._download(url, output, directory, expected_md5, progress)
            except ChallengeError:
                if not same_mirror_slow or not parsed.raw_path.startswith(b"/slow_download/"):
                    raise
                # Keep retries on this mirror and preserve the server's signed query.
                parsed = parsed.copy_with(
                    raw_path=parsed.raw_path.replace(b"/slow_download/", b"/slow%5Fdownload/", 1)
                )
                url = str(parsed)
            except DownloadWaitError as exc:
                delay = exc.seconds + 1
                if not same_mirror_slow or delay > deadline - time.monotonic():
                    raise
                wait_until = time.monotonic() + delay
                remaining = delay
                while remaining > 0:
                    if wait_progress:
                        wait_progress(remaining)
                    time.sleep(min(1, max(0, wait_until - time.monotonic())))
                    remaining = max(0, math.ceil(wait_until - time.monotonic()))
                if wait_progress:
                    wait_progress(0)
        raise AnnaError("Download did not become available after bounded retries; try later.")

    def _download(
        self,
        url: str,
        output: Path | None,
        directory: Path,
        expected_md5: str | None,
        progress: Callable[[int], None] | None,
    ) -> dict:
        if expected_md5:
            expected_md5 = record_id(expected_md5)
        for _ in range(4):
            http_url(url)
            with self.http.stream("GET", url) as response:
                if response.status_code >= 400:
                    check_status(
                        httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            content=self._read_page(response),
                            request=response.request,
                        )
                    )
                if response.status_code != 200:
                    raise AnnaError(
                        f"A complete file is required; server returned HTTP {response.status_code}."
                    )
                chunks = response.iter_bytes(chunk_size=65536)
                first = next(chunks, b"")
                content_type = response.headers.get("content-type", "").lower()
                prefix = first.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
                is_html = "html" in content_type or prefix.startswith(
                    (b"<!doctype html", b"<html", b"<head", b"<script", b"<body")
                )
                if is_html:
                    body = bytearray(first)
                    for chunk in chunks:
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise AnnaError(
                                "Download returned an oversized HTML page; no file was saved."
                            )
                    url = download_link(body.decode("utf-8", errors="replace"), str(response.url))
                    continue
                if ("json" in content_type or prefix.startswith((b'{"', b"{\n"))) or (
                    "xml" in content_type or prefix.startswith(b"<?xml")
                ):
                    raise AnnaError("Download returned JSON/XML; no ebook was saved.")
                if not first:
                    raise AnnaError("Server returned an empty file; download cancelled.")
                destination = output or directory / response_filename(response)
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(
                        f"File already exists; refusing to overwrite: {destination}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(
                    prefix=".anna-", suffix=".part", dir=destination.parent
                )
                digest = hashlib.md5(usedforsecurity=False)
                size = 0
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(first)
                        digest.update(first)
                        size += len(first)
                        if progress:
                            progress(len(first))
                        for chunk in chunks:
                            stream.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                            if progress:
                                progress(len(chunk))
                        stream.flush()
                        os.fsync(stream.fileno())
                    declared = response.headers.get("content-length")
                    if declared and not response.headers.get("content-encoding"):
                        if not declared.isdigit():
                            raise AnnaError("Invalid Content-Length; download cancelled.")
                        if int(declared) != size:
                            raise IntegrityError(
                                "Download size does not match Content-Length; damaged file removed."
                            )
                    checksum = digest.hexdigest()
                    if expected_md5 and checksum != expected_md5:
                        raise IntegrityError("File MD5 verification failed; damaged file removed.")
                    # Atomic publication with no overwrite, including concurrent invocations.
                    os.link(temporary, destination)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                return {"path": str(destination.resolve()), "bytes": size, "md5": checksum}
        raise AnnaError("Too many download landing pages; provide the final file URL.")

    @staticmethod
    def _read_page(response: httpx.Response) -> bytes:
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise AnnaError(f"Server returned HTTP {response.status_code}.")
        return bytes(body)
