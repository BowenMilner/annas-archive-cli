"""Opt-in public-domain download check against a real Anna's Archive mirror."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://annas-archive.gl")
    parser.add_argument("--query", default='"Pride and Prejudice" "Gutenberg"')
    parser.add_argument("--md5", default="51d2b22ca12a8b470b51f543298b34c9")
    parser.add_argument(
        "--record-only",
        action="store_true",
        help="Inspect and download the exact record without search ranking.",
    )
    parser.add_argument("--source", type=int, help="One listed source to isolate during diagnosis.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=60,
        help="HTTP inactivity timeout for upstream diagnostics.",
    )
    args = parser.parse_args()
    command = [
        sys.executable,
        "-m",
        "anna",
        "--base-url",
        args.base_url,
        "--timeout",
        str(args.timeout),
    ]

    with tempfile.TemporaryDirectory(prefix="anna-live-") as directory:

        def run(*arguments):
            result = subprocess.run(
                [*command, *arguments, "--json"],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={k: v for k, v in os.environ.items() if not k.startswith("ANNA_")},
                timeout=420,
            )
            if result.returncode:
                raise RuntimeError(result.stdout.strip() or result.stderr.strip())
            return json.loads(result.stdout)

        info = run("info", args.md5)
        links = run("links", args.md5)
        if not info["title"] or not links:
            raise RuntimeError("Record metadata or download links are missing.")
        if not args.record_only:
            books = run("search", args.query, "--lang", "en", "--ext", "epub", "--sort", "smallest")
            book = next((book for book in books if book["md5"] == args.md5), None)
            if book is None or book["title"] != info["title"]:
                raise RuntimeError("The expected public-domain record was not found in search.")
        destination = Path(directory) / "book.epub"
        source = ["--source", str(args.source)] if args.source is not None else []
        result = run("download", args.md5, "-o", str(destination), *source)
        data = destination.read_bytes()
        digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
        if digest != args.md5 or result["md5"] != digest or result["bytes"] != len(data):
            raise RuntimeError("Downloaded bytes do not match the catalog MD5 and CLI result.")
        with zipfile.ZipFile(destination) as archive:
            if archive.read("mimetype") != b"application/epub+zip" or archive.testzip():
                raise RuntimeError("Downloaded file is not a valid EPUB archive.")
        print(
            json.dumps(
                {
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "base_url": args.base_url,
                    "query": args.query,
                    "title": info["title"],
                    "author": info["author"],
                    "md5": digest,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source": args.source,
                    "stages": ([] if args.record_only else ["search"])
                    + ["info", "links", "download", "md5", "epub_crc"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
