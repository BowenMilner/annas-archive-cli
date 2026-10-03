"""Small, explicit preferences file; never stores cookies or credentials."""

import json
import os
import tempfile
from pathlib import Path

from anna.errors import AnnaError

DEFAULTS = {"language": "en", "format": "epub", "directory": "~/Books"}
KEYS = tuple(DEFAULTS)


def config_path():
    override = os.environ.get("ANNA_CONFIG")
    if override:
        return Path(override).expanduser()
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "anna/config.json"
    )


def load_config():
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise AnnaError(f"Cannot read preferences at {path}; repair or rename this file.") from exc
    if not isinstance(data, dict) or any(
        key not in KEYS or not isinstance(value, str) or not value.strip()
        for key, value in data.items()
    ):
        raise AnnaError(
            f"Invalid preferences at {path}; use language, format and directory strings."
        )
    return {**DEFAULTS, **data}


def save_config(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".anna-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
