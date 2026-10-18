"""App settings remembered between sessions (data folder, settings.json)."""

import json
import os
import re
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

# Models: "" means Claude Code's own default; otherwise an alias or full model name for `claude --model`.
DEFAULT_MODEL = ""
MODEL_CHOICES = {DEFAULT_MODEL: "Claude Code default", "sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}")

# Pause the batch before the next scenario once the 5-hour window passes this percentage (0 = never).
PAUSE_OFF = 0
PAUSE_RANGE = (50, 100)


def clean_model(value) -> str:
    value = value.strip() if isinstance(value, str) else ""
    return value if value == DEFAULT_MODEL or _MODEL_NAME.fullmatch(value) else DEFAULT_MODEL


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
    # Cost control (decided 2026-10-18): pause at 80% of the 5-hour window; Sonnet for smoke tests,
    # Claude Code's default model for scenarios.
    pause_at_percent: int = 80
    scenario_model: str = DEFAULT_MODEL
    smoke_model: str = "sonnet"

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
            pause_at_percent=(self.pause_at_percent if self.pause_at_percent == PAUSE_OFF
                              or PAUSE_RANGE[0] <= self.pause_at_percent <= PAUSE_RANGE[1]
                              else default.pause_at_percent),
            scenario_model=clean_model(self.scenario_model),
            smoke_model=clean_model(self.smoke_model),
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
