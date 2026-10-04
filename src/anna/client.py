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
    DownloadCancelledError,
    DownloadPageError,
    DownloadSourcesError,
    DownloadWaitError,
    FileExistsError,
    HTTPStatusError,
    IntegrityError,
    InvalidInputError,
    ParseError,
    RateLimitError,
)
from anna.library import save_download
from anna.parsing import Book, document, parse_info, parse_search, record_id, text
from anna.session import load_session, preferred_origin, remember_origin, save_session

DEFAULT_BASE_URL = "https://annas-archive.gd"
DEFAULT_MIRRORS = (DEFAULT_BASE_URL, "https://annas-archive.gl", "https://annas-archive.pk")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def check_cancelled(cancelled):
    if cancelled and cancelled():
        raise DownloadCancelledError("Download cancelled; unfinished file removed.")


def download_record(client, book: Book, **options) -> dict:
    return save_download(book, lambda **kw: _download_record(client, book, **kw), **options)


def _download_record(
    client,
    book: Book,
    *,
    source: int | None = None,
    source_progress: Callable[[int, int], None] | None = None,
    retry_budget: float = 90,
    **options,
) -> dict:
    """Try up to sixteen listed free sources, keeping the selected record checksum."""
    if source is not None:
        if not 1 <= source <= len(book.links):
            raise AnnaError(f"Record has {len(book.links)} sources; --source is out of range.")
        candidates = [book.links[source - 1]]
    else:
        eligible = [
            link
            for link in book.links
            if link.kind != "fast" and link.url.startswith(("http://", "https://"))
        ]

        # The listed Libgen file page has a direct GET control and avoids the
        # partner countdown. Other external catalogue/login pages stay last.
        def priority(link):
            parsed = urlsplit(link.url)
            if parsed.hostname == "libgen.li" and parsed.path == "/ads.php":
                return 0
            return 1 if link.kind == "slow" else 2

        eligible.sort(key=priority)
        candidates = []
        seen = set()
        for link in eligible:
            if link.url not in seen:
                seen.add(link.url)
                candidates.append(link)
        candidates = candidates[:16]
    if not candidates:
        raise AnnaError("No free HTTP download source; try another edition.")
    if retry_budget <= 0:
        raise InvalidInputError("Download retry budget must be positive.")
    network_spent = 0.0
    failures = []
    challenged_origin = None
    challenged_url = None
    blocked_origins = set()
    skipped_checks = 0
    started = time.monotonic()
    max_wait = options.get("max_wait", 300)
    if max_wait < 0:
        raise InvalidInputError("--max-wait cannot be negative.")
    for index, link in enumerate(candidates, 1):
        parsed_link = urlsplit(link.url)
        link_origin = f"{parsed_link.scheme}://{parsed_link.netloc}"
        if link.kind == "slow" and link_origin in blocked_origins:
            skipped_checks += 1
            continue
        check_cancelled(options.get("cancelled"))
        if source_progress:
            source_progress(index, len(candidates))
        check_cancelled(options.get("cancelled"))
        remaining = retry_budget - network_spent
        if remaining <= 0:
            break
        attempt_started = time.monotonic()
        queue_started = None
        queue_spent = 0.0
        original_wait = options.get("wait_progress")

        def report_wait(seconds):
            nonlocal queue_started, queue_spent
            if seconds and queue_started is None:
                queue_started = time.monotonic()
            elif not seconds and queue_started is not None:
                queue_spent += time.monotonic() - queue_started
                queue_started = None
            if original_wait:
                original_wait(seconds)

        attempt_options = {
            **options,
            "max_wait": max(0, int(max_wait - (time.monotonic() - started))),
            "wait_progress": report_wait,
        }
        if isinstance(client, Client):
            if source is None:
                attempt_options["contact_deadline"] = time.monotonic() + remaining
            else:
                attempt_options["mirror_fallback"] = False
        try:
            return client.download(link.url, expected_md5=book.md5, **attempt_options)
        except (
            httpx.TransportError,
            HTTPStatusError,
            ChallengeError,
            DownloadPageError,
            DownloadWaitError,
        ) as exc:
            check_cancelled(options.get("cancelled"))
            network_spent += max(0, time.monotonic() - attempt_started - queue_spent)
            if source is not None:
                raise  # An explicit source must remain pinned.
            host = urlsplit(link.url).hostname or "the source"
            if isinstance(exc, httpx.RequestError):
                try:
                    host = exc.request.url.host
                except RuntimeError:
                    pass
            reason = "timed out" if isinstance(exc, httpx.TimeoutException) else "was unavailable"
            if isinstance(exc, DownloadWaitError):
                reason = "exceeded the remaining queue budget"
            elif isinstance(exc, ChallengeError):
                reason = "needs a browser check"
            elif isinstance(exc, HTTPStatusError):
                if exc.origin:
                    host = urlsplit(exc.origin).hostname or host
                status = re.search(r"HTTP (\d{3})", str(exc))
                if status:
                    reason = f"returned HTTP {status[1]}"
                    if exc.status_code == 504:
                        reason += " (upstream timeout)"
                    elif exc.status_code == 502:
                        reason += " (upstream failure)"
            failures.append(f"Source {index} {reason} ({host})")
            if isinstance(exc, ChallengeError):
                parsed = urlsplit(link.url)
                challenged_origin = exc.origin or f"{parsed.scheme}://{parsed.netloc}"
                challenged_url = exc.verification_url or link.url
                if challenged_origin == link_origin and link.kind == "slow":
                    blocked_origins.add(challenged_origin)
    budget_exhausted = network_spent >= retry_budget
    error = DownloadSourcesError(
        (
            f"File-server retry budget exhausted; stopped after {len(failures)} "
            f"of {len(candidates)} listed routes. No book was saved. "
            if budget_exhausted
            else "No free source completed the download. "
        )
        + "; ".join(failures)
        + (
            f". Skipped {skipped_checks} routes behind the same browser check"
            if skipped_checks
            else ""
        )
        + ". Try again later or choose another edition."
    )
    error.origin = challenged_origin
    error.verification_url = challenged_url
    raise error


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
        code = response.status_code
        server_failures = {
            500: "the service failed internally",
            502: "its upstream service failed",
            503: "the service is temporarily unavailable",
            504: "its upstream service timed out",
        }
        message = (
            f"Server returned HTTP {code}: {server_failures[code]}. Try again later."
            if code in server_failures
            else f"Server returned HTTP {code}; check the mirror, record or access permissions."
        )
        error = HTTPStatusError(message)
        error.status_code = code
        try:
            target = urlsplit(str(response.url))
            error.origin = f"{target.scheme}://{target.netloc}"
        except RuntimeError:
            pass  # Standalone responses may not have an attached request.
        raise error


def download_link(html: str, base_url: str) -> str:
    try:
        soup = document(html)
    except ChallengeError as exc:
        parsed = urlsplit(base_url)
        exc.origin = f"{parsed.scheme}://{parsed.netloc}"
        raise
    countdown = soup.select_one(".js-partner-countdown")
    if countdown is not None:
        value = text(countdown)
        if not re.fullmatch(r"\d{1,6}", value):
            raise AnnaError("Unrecognized download countdown; no file was saved.")
        raise DownloadWaitError(int(value))
    # Some current partner routes advertise a compact link or a visible copy-only
    # URL rather than an anchor. Read the visible control; never execute its script.
    for anchor in soup.select("a[href]"):
        if text(anchor).casefold() == "download with short filename":
            return http_url(urljoin(base_url, str(anchor["href"])))
    copied = []
    for button in soup.select('button[onclick*="navigator.clipboard.writeText"]'):
        sibling = button.find_next_sibling("span", class_="break-all")
        if sibling is not None:
            value = text(sibling)
            if value.startswith(("http://", "https://")):
                copied.append(http_url(value))
    if copied:
        return next(
            (
                value
                for value in copied
                if urlsplit(value).path.rsplit("/", 1)[-1].startswith("annas-arch-")
            ),
            copied[0],
        )
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
    raise DownloadPageError(
        "Download returned a web page requiring verification, login or waiting. "
        "Obtain the final file URL in your browser and run anna download URL; "
        "no page was saved."
    )


class Client:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30,
        cookies: Path | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.mirrors = (base_url,) if base_url else DEFAULT_MIRRORS
        preferred = preferred_origin(self.mirrors) if base_url is None else None
        self.base_url = http_url(preferred or self.mirrors[0]).rstrip("/")
        self._catalogue_success = False
        self._explicit_cookies = cookies is not None
        self._default_agent = user_agent
        self._session_origins: dict[str, str] = {}
        self._saved_session_agents: dict[str, str | None] = {}
        self._verified_session_origins: set[str] = set()
        parsed = urlsplit(self.base_url)
        if parsed.path or parsed.query or parsed.fragment:
            raise AnnaError("--base-url requires an origin URL, e.g. https://annas-archive.gl.")
        jar = http.cookiejar.MozillaCookieJar()
        if not cookies:
            for mirror in self.mirrors:
                saved, agent = load_session(mirror)
                for cookie in saved:
                    jar.set_cookie(cookie)
                if agent and mirror == self.base_url and user_agent == DEFAULT_USER_AGENT:
                    user_agent = agent
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
        try:
            for origin, agent in self._session_origins.items():
                if origin in self._verified_session_origins:
                    save_session(origin, self.http.cookies.jar, agent)
            if self._catalogue_success:
                remember_origin(self.base_url)
        except OSError:
            pass  # Optional session caching must not change a completed transfer's outcome.
        self.http.close()

    def activate_session(self, url, inherited_agent=None):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        agent = inherited_agent or self._default_agent
        if not self._explicit_cookies:
            if origin not in self._session_origins:
                jar, saved_agent = load_session(origin)
                for cookie in jar:
                    self.http.cookies.jar.set_cookie(cookie)
                self._saved_session_agents[origin] = saved_agent
            saved_agent = self._saved_session_agents.get(origin)
            if saved_agent and self._default_agent == DEFAULT_USER_AGENT:
                agent = saved_agent
        self.http.headers["User-Agent"] = agent
        self._session_origins[origin] = agent

    def _verified_session(self, url: str) -> None:
        target = urlsplit(url)
        origin = f"{target.scheme}://{target.netloc}"
        self._verified_session_origins.add(origin)
        if origin == self.base_url and origin in self.mirrors:
            self._catalogue_success = True

    def verify_session(self, url: str) -> None:
        """Check an imported session on its exact page, without downloading a file."""
        target, base = urlsplit(http_url(url)), urlsplit(self.base_url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise InvalidInputError("Session checks must stay on the selected mirror.")
        self.activate_session(url)
        address = httpx.URL(url)
        if re.fullmatch(r"/slow_download/[0-9a-f]{32}/\d+/\d+/?", address.path):
            retry = address.copy_with(
                raw_path=address.raw_path.replace(b"/slow_download/", b"/slow%5Fdownload/", 1)
            )
        else:
            query = address.query + (b"&" if address.query else b"") + b"check=%31"
            retry = address.copy_with(query=query)
        for index, candidate in enumerate((address, retry)):
            try:
                with self.http.stream("GET", candidate) as response:
                    checked = httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=self._read_page(response),
                        request=response.request,
                    )
                    check_status(checked)
                    document(checked.text)
                self._verified_session(str(response.url))
                return
            except ChallengeError as exc:
                self._verified_session_origins.discard(self.base_url)
                if index:
                    exc.origin = self.base_url
                    exc.verification_url = url
                    raise

    def page(self, path: str, params: dict | None = None) -> httpx.Response:
        self.activate_session(self.base_url)
        response = self.http.get(self.base_url + path, params=params)
        try:
            check_status(response)
            try:
                document(response.text)
            except ChallengeError as exc:
                exc.origin = self.base_url
                raise
        except ChallengeError:
            self._verified_session_origins.discard(self.base_url)
            # The current public site accepts an equivalent percent-encoded check
            # value. Retry once in this session; this is not a JS solver.
            url = httpx.URL(self.base_url + path, params=params)
            query = url.query + (b"&" if url.query else b"") + b"check=%31"
            response = self.http.get(url.copy_with(query=query))
            try:
                check_status(response)
                document(response.text)
            except ChallengeError as exc:
                exc.origin = self.base_url
                raise
        self._verified_session_origins.add(self.base_url)
        return response

    def parsed_page(self, path, params, parser):
        failures = []
        challenged_origin = None
        mirrors = (self.base_url,) + tuple(m for m in self.mirrors if m != self.base_url)
        for mirror in mirrors:
            self.base_url = mirror.rstrip("/")
            try:
                response = self.page(path, params)
                result = parser(response.text, str(response.url))
                self._catalogue_success = True
                return result
            except RateLimitError:
                raise  # Respect rate limits rather than routing round them.
            except (ChallengeError, HTTPStatusError, ParseError, httpx.TransportError) as exc:
                if len(mirrors) == 1:
                    raise
                failures.append(type(exc).__name__)
                if isinstance(exc, ChallengeError):
                    challenged_origin = getattr(exc, "origin", mirror)
        error = (ChallengeError if challenged_origin else AnnaError)(
            "No working mirror found (" + ", ".join(failures) + "). "
            "Try again later, check your connection, or use anna --base-url URL doctor. "
            "Run anna --help for advanced connection options."
        )
        if challenged_origin:
            error.origin = challenged_origin
        raise error

    def search(self, query: str, **filters) -> list[Book]:
        if not query.strip():
            raise AnnaError("Search query cannot be empty.")
        params = {"q": query, "display": "", **{k: v for k, v in filters.items() if v}}
        return self.parsed_page("/search", params, parse_search)

    def info(self, value: str) -> Book:
        md5 = record_id(value)
        return self.parsed_page(f"/md5/{md5}", None, lambda html, url: parse_info(html, url, md5))

    def statistics(self, md5: str) -> dict[str, int]:
        """Optional edition statistics; callers must not depend on their availability."""
        response = self.http.get(
            self.base_url + "/dyn/md5/inline_info/" + record_id(md5),
            headers={"Accept": "text/css"},
        )
        check_status(response)
        values = response.json()
        if not isinstance(values, dict):
            return {}
        return {
            key: value
            for key, value in values.items()
            if key in {"downloads_total", "lists_count", "reports_count", "great_quality_count"}
            and type(value) is int
            and value >= 0
        }

    def download(
        self,
        url: str,
        output: Path | None = None,
        directory: Path = Path("."),
        expected_md5: str | None = None,
        progress: Callable[[int], None] | None = None,
        max_wait: int = 300,
        wait_progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        total_progress: Callable[[int | None], None] | None = None,
        request_progress: Callable[[str], None] | None = None,
        file_validator: Callable[[Path], None] | None = None,
        contact_deadline: float | None = None,
        mirror_fallback: bool = True,
    ) -> dict:
        if max_wait < 0:
            raise InvalidInputError("--max-wait cannot be negative.")
        deadline = time.monotonic() + max_wait
        parsed = httpx.URL(http_url(url))
        base = httpx.URL(self.base_url)
        slow_path = bool(re.fullmatch(r"/slow_download/[0-9a-f]{32}/\d+/\d+/?", parsed.path))
        route_origin = f"{parsed.scheme}://{parsed.netloc.decode()}"
        automatic_route = mirror_fallback and not parsed.query
        if automatic_route and len(self.mirrors) > 1 and slow_path and route_origin in self.mirrors:
            parsed = parsed.copy_with(scheme=base.scheme, host=base.host, port=base.port)
            url = str(parsed)
        same_mirror_slow = (parsed.scheme, parsed.host, parsed.port) == (
            base.scheme,
            base.host,
            base.port,
        ) and slow_path
        mirror_retries = (
            [mirror for mirror in self.mirrors if mirror != self.base_url]
            if automatic_route
            else []
        )
        for _ in range(6 + 2 * len(mirror_retries)):
            check_cancelled(cancelled)
            try:
                return self._download(
                    url,
                    output,
                    directory,
                    expected_md5,
                    progress,
                    cancelled,
                    total_progress,
                    request_progress,
                    file_validator,
                    contact_deadline,
                )
            except ChallengeError as exc:
                target = urlsplit(url)
                exc.origin = exc.origin or f"{target.scheme}://{target.netloc}"
                exc.verification_url = url
                self._verified_session_origins.discard(exc.origin)
                if not same_mirror_slow:
                    raise
                if parsed.raw_path.startswith(b"/slow_download/"):
                    # Preserve the server's signed query for this same-mirror retry.
                    parsed = parsed.copy_with(
                        raw_path=parsed.raw_path.replace(
                            b"/slow_download/", b"/slow%5Fdownload/", 1
                        )
                    )
                    url = str(parsed)
                elif mirror_retries and exc.origin == self.base_url:
                    # Only remap the catalogue's public slow route, never a signed
                    # file URL or a challenged external partner. Explicit mirrors
                    # have no candidates and remain pinned.
                    self.base_url = mirror_retries.pop(0).rstrip("/")
                    replacement = httpx.URL(self.base_url)
                    parsed = parsed.copy_with(
                        scheme=replacement.scheme,
                        host=replacement.host,
                        port=replacement.port,
                        raw_path=parsed.raw_path.replace(
                            b"/slow%5Fdownload/", b"/slow_download/", 1
                        ),
                    )
                    url = str(parsed)
                else:
                    raise
            except DownloadWaitError as exc:
                delay = exc.seconds + 1
                if not same_mirror_slow or delay > deadline - time.monotonic():
                    raise
                if contact_deadline is not None:
                    contact_deadline += delay
                wait_until = time.monotonic() + delay
                remaining = delay
                while remaining > 0:
                    check_cancelled(cancelled)
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
        cancelled: Callable[[], bool] | None = None,
        total_progress: Callable[[int | None], None] | None = None,
        request_progress: Callable[[str], None] | None = None,
        file_validator: Callable[[Path], None] | None = None,
        contact_deadline: float | None = None,
    ) -> dict:
        if expected_md5:
            expected_md5 = record_id(expected_md5)
        for _ in range(4):
            check_cancelled(cancelled)
            http_url(url)
            if request_progress:
                request_progress(
                    "Requesting download link…"
                    if urlsplit(url).hostname == urlsplit(self.base_url).hostname
                    else "Contacting file server…"
                )
            # A signed file link must retain the identity used to obtain it.
            # Cookie domains still control which cookies reach the next host.
            inherited_agent = self.http.headers.get("User-Agent")
            self.activate_session(url, inherited_agent=inherited_agent)
            request_timeout = self.http.timeout
            if contact_deadline is not None:
                remaining = contact_deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.ReadTimeout(
                        "File-server retry budget exhausted", request=httpx.Request("GET", url)
                    )
                configured = self.http.timeout
                request_timeout = httpx.Timeout(
                    **{
                        name: min(value, remaining) if value is not None else remaining
                        for name, value in configured.as_dict().items()
                    }
                )
            with self.http.stream("GET", url, timeout=request_timeout) as response:
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
                check_cancelled(cancelled)
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
                        check_cancelled(cancelled)
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise AnnaError(
                                "Download returned an oversized HTML page; no file was saved."
                            )
                    try:
                        next_url = download_link(
                            body.decode("utf-8", errors="replace"), str(response.url)
                        )
                    except DownloadWaitError:
                        self._verified_session(str(response.url))
                        raise
                    self._verified_session(str(response.url))
                    url = next_url
                    continue
                if ("json" in content_type or prefix.startswith((b'{"', b"{\n"))) or (
                    "xml" in content_type or prefix.startswith(b"<?xml")
                ):
                    raise AnnaError("Download returned JSON/XML; no ebook was saved.")
                if not first:
                    raise AnnaError("Server returned an empty file; download cancelled.")
                declared_total = response.headers.get("content-length", "")
                total = (
                    int(declared_total)
                    if declared_total.isdigit() and not response.headers.get("content-encoding")
                    else None
                )
                if total_progress:
                    total_progress(total)
                check_cancelled(cancelled)
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
                            check_cancelled(cancelled)
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
                    if file_validator:
                        file_validator(Path(temporary))
                    check_cancelled(cancelled)
                    # Atomic publication with no overwrite, including concurrent invocations.
                    os.link(temporary, destination)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                self._verified_session(str(response.url))
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
