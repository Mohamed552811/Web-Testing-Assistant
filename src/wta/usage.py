"""Plan usage from Claude Code's own `rate_limit_event` messages.

Only data Claude Code emits is used. Documented fields of rate_limit_info are
`status`, `resetsAt` (epoch seconds) and `utilization`. Claude Code 2.1 also sends
`rateLimitType` and `unifiedWindows` ({"five_hour": {...}, "seven_day": {...}}); they
are used when present and the documented fields are the fallback.

Utilization is a 0–1 fraction; values above 1 are treated as percentages.
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

WINDOWS = ("five_hour", "seven_day")


@dataclass(frozen=True)
class WindowUsage:
    percent: float  # 0–100
    resets_at: int  # epoch seconds
    observed_at: float  # epoch seconds

    def is_stale(self, now: float | None = None) -> bool:
        """After its reset time a reading no longer describes the current window."""
        return (now if now is not None else time.time()) >= self.resets_at


def _percent(value) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value * 100 if value <= 1 else value


def windows_from_event(info: dict, observed_at: float | None = None) -> dict[str, WindowUsage]:
    observed_at = observed_at if observed_at is not None else time.time()
    found: dict[str, WindowUsage] = {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name in WINDOWS:
            window = unified.get(name)
            if isinstance(window, dict):
                pct, resets = _percent(window.get("utilization")), window.get("resetsAt")
                if pct is not None and isinstance(resets, (int, float)):
                    found[name] = WindowUsage(round(pct, 1), int(resets), observed_at)
    name = info.get("rateLimitType")
    if name in WINDOWS and name not in found:
        pct, resets = _percent(info.get("utilization")), info.get("resetsAt")
        if pct is not None and isinstance(resets, (int, float)):
            found[name] = WindowUsage(round(pct, 1), int(resets), observed_at)
    return found


def merge(current: dict[str, WindowUsage], new: dict[str, WindowUsage]) -> dict[str, WindowUsage]:
    """Combine readings. Same window (same resets_at): keep the higher percentage.
    A later resets_at means a new window, which replaces the old one."""
    merged = dict(current)
    for name, reading in new.items():
        old = merged.get(name)
        if old is None or reading.resets_at > old.resets_at:
            merged[name] = reading
        elif reading.resets_at == old.resets_at:
            higher = reading if reading.percent >= old.percent else old
            merged[name] = WindowUsage(higher.percent, reading.resets_at, max(reading.observed_at, old.observed_at))
    return merged


def load(path: Path) -> dict[str, WindowUsage]:
    try:
        data = json.loads(path.read_text("utf-8"))
        return {name: WindowUsage(**data[name]) for name in WINDOWS if name in data}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def save(path: Path, usage: dict[str, WindowUsage]) -> None:
    """Atomic write so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({k: asdict(v) for k, v in usage.items()}, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_event(path: Path, info: dict, observed_at: float | None = None) -> dict[str, WindowUsage]:
    """Merge one rate_limit_info into the usage file and return the new state."""
    new = windows_from_event(info, observed_at)
    if not new:
        return load(path)
    merged = merge(load(path), new)
    save(path, merged)
    return merged
