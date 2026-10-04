import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/live_smoke.py"


@pytest.mark.parametrize("record_only", [False, True])
def test_live_diagnostic_checks_exact_bytes_and_optional_source(monkeypatch, capsys, record_only):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("content.xhtml", "<html/>")
    payload = data.getvalue()
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    calls = []
    record = {"md5": digest, "title": "Public-domain fixture", "author": "Jane Austen"}

    def run(command, **options):
        calls.append(command)
        if "search" in command:
            result = [record]
        elif "info" in command:
            result = record
        elif "links" in command:
            result = [{"url": "https://fixture.invalid/file"}]
        else:
            Path(command[command.index("-o") + 1]).write_bytes(payload)
            assert command[command.index("--source") + 1] == "2"
            result = {"md5": digest, "bytes": len(payload)}
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    args = [str(SCRIPT), "--md5", digest, "--source", "2"]
    if record_only:
        args.append("--record-only")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(subprocess, "run", run)
    spec = importlib.util.spec_from_file_location("live_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    report = json.loads(capsys.readouterr().out)
    assert report["md5"] == digest
    assert report["bytes"] == len(payload)
    assert report["stages"][-2:] == ["md5", "epub_crc"]
    assert any("search" in call for call in calls) is not record_only
    assert not Path(calls[-1][calls[-1].index("-o") + 1]).exists()
