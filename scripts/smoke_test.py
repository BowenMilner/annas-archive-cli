"""Exercise the installed CLI outside its checkout against a local HTTP server."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from anna import __version__

ROOT = Path(__file__).resolve().parent.parent
BOOK = b"%PDF-1.7\npublic-domain packaging fixture\n%%EOF"
MD5 = hashlib.md5(BOOK).hexdigest()
FIXTURES = ROOT / "tests/fixtures"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        status, content_type = 200, "text/html; charset=utf-8"
        if parsed.path == "/search":
            if parse_qs(parsed.query).get("q") == ["challenge"]:
                status, body = 403, b"<title>DDoS-Guard</title>"
            else:
                body = (FIXTURES / "search.html").read_bytes()
        elif parsed.path.startswith("/md5/"):
            body = (FIXTURES / "info.html").read_text(encoding="utf-8")
            body = body.replace("1" * 32, MD5).encode()
        elif parsed.path.startswith("/slow_download/"):
            self.send_response(302)
            self.send_header("Location", "/file")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif parsed.path == "/file":
            body, content_type = BOOK, "application/pdf"
        else:
            status, body = 404, b"Not found"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--standalone", action="store_true")
    args = parser.parse_args()
    executable = str(Path(args.executable).resolve())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANNA_")}
    env.pop("PYTHONPATH", None)
    env.update(PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost")
    if args.standalone:
        env.pop("PYTHONUTF8", None)
        env.pop("PYTHONIOENCODING", None)
    try:
        with tempfile.TemporaryDirectory() as directory:
            env["ANNA_CONFIG"] = str(Path(directory) / "settings" / "config.json")

            def run(*commands, success=True):
                result = subprocess.run(
                    [executable, "--base-url", f"http://127.0.0.1:{server.server_port}", *commands],
                    cwd=directory,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                )
                assert result.returncode == (0 if success else 1), (result.stdout, result.stderr)
                return result

            assert __version__ in run("--version").stdout
            assert "search" in run("--help").stdout
            records = json.loads(run("search", "Austen", "--ext", "epub", "--json").stdout)
            assert len(records) == 2 and records[1]["language"] == "zh"
            assert json.loads(run("info", MD5, "--json").stdout)["author"] == "Jane Austen"
            assert len(json.loads(run("links", MD5, "--json").stdout)) == 3
            result = run("download", MD5, "--source", "2", "-o", "book.pdf", "--json")
            assert not result.stderr, result.stderr
            assert json.loads(result.stdout)["md5"] == MD5
            assert Path(directory, "book.pdf").read_bytes() == BOOK
            receipts = json.loads(run("history", "--json").stdout)
            assert len(receipts) == 1 and receipts[0]["identity"] == MD5
            reused = json.loads(run("download", MD5, "--json").stdout)
            assert (
                reused["already_downloaded"] and reused["path"] == json.loads(result.stdout)["path"]
            )
            error = json.loads(
                run("download", MD5, "-o", "book.pdf", "--json", success=False).stdout
            )
            assert error["error"]["code"] == "file_exists"
            error = json.loads(run("search", "challenge", "--json", success=False).stdout)
            assert error["error"]["code"] == "browser_verification_required"
            error = json.loads(
                run(
                    "download",
                    f"http://127.0.0.1:{server.server_port}/file",
                    "--md5",
                    "0" * 32,
                    "-o",
                    "bad.pdf",
                    "--json",
                    success=False,
                ).stdout
            )
            assert error["error"]["code"] == "integrity_error"
            assert not Path(directory, "bad.pdf").exists()
            assert not list(Path(directory).glob("*.part"))
        print("Installed artifact passed search/info/links/download/error smoke tests.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
