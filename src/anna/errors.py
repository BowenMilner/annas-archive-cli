class AnnaError(Exception):
    origin: str | None = None
    verification_url: str | None = None
    """An actionable error safe to display without a traceback."""

    code = "operation_failed"


class ChallengeError(AnnaError):
    code = "browser_verification_required"


class ParseError(AnnaError):
    code = "unrecognized_page"


class InvalidInputError(AnnaError):
    code = "invalid_input"


class IntegrityError(AnnaError):
    code = "integrity_error"


class FileExistsError(AnnaError):
    code = "file_exists"


class RateLimitError(AnnaError):
    code = "rate_limited"


class HTTPStatusError(AnnaError):
    status_code: int | None = None

    code = "http_error"


class DownloadWaitError(AnnaError):
    code = "download_wait_required"

    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(
            f"Free download requires another {seconds} seconds; increase --max-wait or retry later."
        )


class DownloadCancelledError(AnnaError):
    code = "download_cancelled"


class DownloadSourcesError(AnnaError):
    code = "download_sources_unavailable"


class DownloadPageError(AnnaError):
    code = "download_page_unavailable"
