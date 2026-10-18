"""App settings remembered between sessions (data folder, settings.json)."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Screenshot timing
SCREENSHOTS_BUG_BLOCKED = "bug_blocked"  # only Blocked / Bug steps (default)
SCREENSHOTS_EVERY_STEP = "every_step"
SCREENSHOTS_ONCE_PER_PAGE = "once_per_smoke_page"  # one per smoke-test page, plus Blocked / Bug steps
SCREENSHOT_TIMINGS = {
    SCREENSHOTS_BUG_BLOCKED: "Only Blocked and Bug steps",
    SCREENSHOTS_EVERY_STEP: "Every step",
    SCREENSHOTS_ONCE_PER_PAGE: "Once per smoke-test page (plus Blocked and Bug steps)",
}

# Export format
EXPORT_FORMATS = {
    "xlsx": "Excel (.xlsx)",
    "pdf": "PDF (.pdf)",
    "docx": "Word (.docx)",
    "md": "Markdown summary + bug reports (folder)",
}


@dataclass
class Settings:
    screenshot_timing: str = SCREENSHOTS_BUG_BLOCKED
    export_format: str = "xlsx"
    # Pause before risky actions and ask the user (uses `claude --resume`). Off by default.
    confirm_risky_actions: bool = False
    # Shown as "Tester" in exported reports.
    tester_name: str = ""
    # "system" (follow Windows), "light" or "dark".
    theme: str = "system"

    def cleaned(self) -> "Settings":
        """Unknown values fall back to the defaults."""
        default = Settings()
        return Settings(
            screenshot_timing=self.screenshot_timing if self.screenshot_timing in SCREENSHOT_TIMINGS
            else default.screenshot_timing,
            export_format=self.export_format if self.export_format in EXPORT_FORMATS else default.export_format,
            confirm_risky_actions=bool(self.confirm_risky_actions),
            tester_name=self.tester_name.strip()[:100],
            theme=self.theme if self.theme in ("system", "light", "dark") else default.theme,
        )


def load(path: Path) -> Settings:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    default = Settings()
    values = {
        f.name: data[f.name]
        for f in fields(Settings)
        if f.name in data and isinstance(data[f.name], type(getattr(default, f.name)))
    }
    return Settings(**values).cleaned()


def save(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings.cleaned()), indent=2), encoding="utf-8")
    os.replace(tmp, path)
