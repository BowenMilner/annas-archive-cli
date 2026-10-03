"""Build and archive a native executable with dependency notices."""

import argparse
import hashlib
import importlib.metadata
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from anna import __version__

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    system = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]
    arch = {"AMD64": "x86_64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    if args.target != f"{system}-{arch}":
        parser.error(f"Native build required: host is {system}-{arch}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--python-option",
            "X utf8",
            "--name",
            "anna",
            "--collect-data",
            "certifi",
            "--collect-all",
            "textual",
            "--hidden-import",
            "socksio",
            "--distpath",
            str(ROOT / "build/bin"),
            "--workpath",
            str(ROOT / "build/pyinstaller"),
            "--specpath",
            str(ROOT / "build"),
            str(ROOT / "scripts/entrypoint.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    binary = ROOT / "build/bin" / ("anna.exe" if system == "windows" else "anna")
    subprocess.run([str(binary), "--version"], check=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/smoke_test.py"),
            "--executable",
            str(binary),
            "--standalone",
        ],
        check=True,
    )
    staging = ROOT / "build" / f"anna-v{__version__}-{args.target}"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, staging / binary.name)
    for filename in ["LICENSE", "README.md", "CHANGELOG.md"]:
        shutil.copy2(ROOT / filename, staging / filename)
    # These are the transitive runtime dependencies, plus the bundled bootloader.
    runtime = [
        "beautifulsoup4",
        "click",
        "httpx",
        "httpcore",
        "certifi",
        "idna",
        "anyio",
        "h11",
        "sniffio",
        "soupsieve",
        "typing_extensions",
        "socksio",
        "pyinstaller",
        "colorama",
        "textual",
        "rich",
        "markdown-it-py",
        "mdurl",
        "pygments",
        "platformdirs",
        "linkify-it-py",
        "uc-micro-py",
        "mdit-py-plugins",
    ]
    notices = []
    for name in runtime:
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue  # Conditional dependencies differ by Python and operating system.
        notices.append(f"\n{'=' * 72}\n{name} {dist.version}\n")
        files = [
            p
            for p in dist.files or []
            if any(part in p.name.lower() for part in ("license", "copying", "notice"))
        ]
        for path in files:
            location = Path(dist.locate_file(path))
            if location.is_file():
                notices.append(location.read_text(encoding="utf-8", errors="replace"))
        if not files:
            notices.append(
                str(dist.metadata.get("License", dist.metadata.get("License-Expression", "")))
            )
    python_license = next(
        (
            p
            for p in [
                Path(sys.base_prefix) / "LICENSE.txt",
                Path(sys.base_prefix)
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "LICENSE.txt",
            ]
            if p.exists()
        ),
        None,
    )
    if python_license:
        notices.append("\nPython runtime\n" + python_license.read_text(encoding="utf-8"))
    else:
        raise SystemExit("Python runtime license not found; cannot publish incomplete notices")
    (staging / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    releases = ROOT / "release"
    releases.mkdir(exist_ok=True)
    if system == "windows":
        archive = releases / f"{staging.name}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in staging.iterdir():
                output.write(path, path.name)
    else:
        archive = releases / f"{staging.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for path in staging.iterdir():
                output.add(path, arcname=path.name)
    print(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")


if __name__ == "__main__":
    main()
