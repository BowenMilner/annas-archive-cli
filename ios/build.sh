#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v xcodegen >/dev/null || { echo 'Install XcodeGen with: brew install xcodegen' >&2; exit 1; }
xcodegen generate
xcodebuild -project Bookfinder.xcodeproj -scheme Bookfinder -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath build/device \
  CODE_SIGNING_ALLOWED=NO build
python3 - <<'PY'
from pathlib import Path
import plistlib
import shutil
import zipfile

root = Path('build')
app = root / 'device/Build/Products/Release-iphoneos/Bookfinder.app'
with (app / 'Info.plist').open('rb') as stream:
    info = plistlib.load(stream)
assert info['CFBundleIdentifier'] == 'dev.bowenmilner.bookfinder'
assert 'iPhoneOS' in info['CFBundleSupportedPlatforms']
assert (app / 'catalogue.js').is_file(), 'Missing catalogue adapter'
assert (app / info['CFBundleExecutable']).is_file(), 'Missing app binary'
payload = root / 'ipa/Payload'
shutil.rmtree(root / 'ipa', ignore_errors=True)
payload.mkdir(parents=True)
shutil.copytree(app, payload / app.name)
destination = root / 'Bookfinder-unsigned.ipa'
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((root / 'ipa').rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(root / 'ipa'))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
print(destination.resolve())
PY
