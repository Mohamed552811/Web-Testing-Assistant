"""App settings remembered between sessions (data folder, settings.json)."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    # Pause before risky actions and ask the user (uses `claude --resume`). Off by default.
    confirm_risky_actions: bool = False


def load(path: Path) -> Settings:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f.name: f.type for f in fields(Settings)}
    values = {k: v for k, v in data.items() if k in known and isinstance(v, type(getattr(Settings(), k)))}
    return Settings(**values)


def save(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, path)
