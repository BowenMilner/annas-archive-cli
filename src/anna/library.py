"""Local download receipts, safe edition names and explicit desktop actions."""

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from anna.config import config_path
from anna.errors import AnnaError, DownloadCancelledError


def history():
    """Return local receipts, newest first; never store source URLs or cookies."""
    path = config_path().parent / "history.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path) as db:
            os.chmod(path, 0o600)
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE IF NOT EXISTS downloads (
                source TEXT, identity TEXT, title TEXT, author TEXT, path TEXT,
                bytes INTEGER, md5 TEXT, saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, identity, path))""")
            return [
                dict(row)
                for row in db.execute("SELECT * FROM downloads ORDER BY saved_at DESC, rowid DESC")
            ]
    except sqlite3.Error as exc:
        raise AnnaError("Cannot read local download history.") from exc


def remember(book, result):
    history()
    try:
        with sqlite3.connect(config_path().parent / "history.sqlite3") as db:
            db.execute(
                """INSERT OR REPLACE INTO downloads
                (source, identity, title, author, path, bytes, md5)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    book.source,
                    book.md5 or book.source_id,
                    book.title,
                    book.author,
                    str(Path(result["path"]).resolve()),
                    result["bytes"],
                    result["md5"],
                ),
            )
    except sqlite3.Error as exc:
        raise AnnaError("Cannot save local download history.") from exc


def verified_copy(book, cancelled=None):
    for receipt in history():
        if (receipt["source"], receipt["identity"]) != (book.source, book.md5 or book.source_id):
            continue
        if book.md5 and receipt["md5"] != book.md5:
            continue
        path = Path(receipt["path"])
        try:
            if path.stat().st_size != receipt["bytes"]:
                continue
            digest = hashlib.md5(usedforsecurity=False)
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    if cancelled and cancelled():
                        raise DownloadCancelledError("Download cancelled.")
                    digest.update(chunk)
            if digest.hexdigest() == receipt["md5"]:
                return {
                    "path": str(path),
                    "bytes": receipt["bytes"],
                    "md5": receipt["md5"],
                    "already_downloaded": True,
                }
        except OSError:
            continue
    return None


def edition_path(book, directory):
    def clean(value):
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", value)
        return (
            " ".join(value.split())
            .strip(" .")
            .encode("utf-8")[:72]
            .decode("utf-8", errors="ignore")
            or "Unknown"
        )

    identity = book.md5[:12] or f"{book.source}-{book.source_id}"
    identity = re.sub(r"[^a-zA-Z0-9_-]", "", identity)[:50] or "edition"
    extension = book.format.lower()
    if not re.fullmatch(r"[a-z0-9]{1,10}", extension):
        extension = "bin"
    return Path(directory) / (
        f"{clean(book.author or 'Unknown author')} — {clean(book.title)} [{identity}].{extension}"
    )


def save_download(book, download, **options):
    """Reuse only a checksum-verified receipt; explicit output always takes precedence."""
    if not options.get("output"):
        try:
            existing = (
                verified_copy(book, options.get("cancelled"))
                if options.get("source") is None
                else None
            )
        except DownloadCancelledError:
            raise
        except (AnnaError, OSError):
            existing = None
        if existing:
            return existing
        options["output"] = edition_path(book, options.get("directory", Path.home() / "Books"))
    result = download(**options)
    try:
        remember(book, result)
    except (AnnaError, OSError) as exc:
        result["history_warning"] = str(exc)
    return result


def open_saved(path, folder=False):
    """Launch only on an explicit user action; never pass filenames through a shell."""
    path = Path(path).resolve()
    if not path.is_file():
        raise AnnaError("This saved book is missing. It may have been moved or deleted.")
    target = str(path.parent if folder else path)
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    try:
        if sys.platform == "win32":
            getattr(os, "startfile")(target)
            return
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AnnaError(
            "Cannot open this file. Check your default reader or file manager."
        ) from exc
