"""Where the app keeps its own files (database, session folders, usage file)."""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Web Testing Assistant"


def data_dir() -> Path:
    """Per-user data folder, created if missing. WTA_DATA_DIR overrides it."""
    override = os.environ.get("WTA_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    # Resolve to the long, final form: Claude Code compares file paths against its working
    # directory, and a Windows 8.3 short name (e.g. MOHAME~1) makes writes look outside it.
    return path.resolve()


def database_path() -> Path:
    return data_dir() / "wta.sqlite3"
