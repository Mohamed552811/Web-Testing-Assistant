"""Top strip: Claude Code sign-in state and the 5-hour / weekly plan usage bars."""

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from wta import claude_launcher, usage

GREEN, YELLOW, RED, GREY = "#2ea043", "#d29922", "#da3633", "#6e7681"


def level_color(percent: float) -> str:
    return GREEN if percent < 50 else YELLOW if percent < 80 else RED


def format_reset(resets_at: int, now: float | None = None) -> str:
    when = datetime.fromtimestamp(resets_at)
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    return when.strftime("%H:%M") if when.date() == today else when.strftime("%a %d %b %H:%M")


class _WindowBar(QWidget):
    """A compact gauge; the reset time is shown on hover."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = QLabel(title)
        self.title.setProperty("muted", True)
        self.bar = QProgressBar(minimum=0, maximum=1000, textVisible=True)
        self.bar.setFixedWidth(110)
        self.bar.setFixedHeight(14)
        self.reset = QLabel()  # not shown; its text is the hover text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        self.show_reading(None)

    def show_reading(self, reading: usage.WindowUsage | None, now: float | None = None) -> None:
        if reading is None:
            self.bar.setValue(0)
            self.bar.setFormat("no data")
            self._color(GREY)
            self.reset.setText("")
            self._hover("No usage reported yet. It fills in during the first test run.")
            return
        self.bar.setValue(int(min(reading.percent, 100) * 10))
        if reading.is_stale(now):
            self.bar.setFormat(f"{reading.percent:.0f}% (stale)")
            self._color(GREY)
            self.reset.setText(f"reset {format_reset(reading.resets_at, now)} passed")
        else:
            self.bar.setFormat(f"{reading.percent:.0f}%")
            self._color(level_color(reading.percent))
            self.reset.setText(f"resets {format_reset(reading.resets_at, now)}")
        self._hover(f"{self.title.text()} window: {reading.percent:.0f}% used, {self.reset.text()}.")

    def _hover(self, text: str) -> None:
        text += "\nPlan usage reported by Claude Code during test runs (rate_limit_event)."
        for widget in (self, self.title, self.bar):
            widget.setToolTip(text)

    def _color(self, color: str) -> None:
        self.bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {GREY}; border-radius: 7px; text-align: center; font-size: 8pt; }}"
            "QProgressBar::chunk { border-radius: 6px; }"
            f"QProgressBar::chunk {{ background: {color}; }}"
        )


class _SignInChecker(QObject):
    done = Signal(object)  # SignInStatus

    def run(self) -> None:
        threading.Thread(target=lambda: self.done.emit(claude_launcher.check_sign_in()), daemon=True).start()


class TopBar(QWidget):
    """Shows whether runs are allowed (Claude Code signed in) and the plan usage."""

    sign_in_changed = Signal(bool)
    close_all_requested = Signal()

    def __init__(self, usage_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.usage_path = usage_path
        self.signed_in = False

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {GREY}; font-size: 12pt;")
        self.claude_label = QLabel("Checking Claude Code sign-in…")
        self.check_button = QPushButton("Check again")
        self.check_button.hide()
        self.five_hour = _WindowBar("5-hour")
        self.seven_day = _WindowBar("Weekly")
        self.updated = QLabel()
        self.updated.setProperty("muted", True)
        self.close_all_button = QPushButton("Close all browsers")
        self.close_all_button.setToolTip(
            "Stop any running test and close every process the app or Playwright started. "
            "Your own browsers are never touched."
        )
        self.close_all_button.clicked.connect(self.close_all_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.addWidget(self.dot)
        layout.addWidget(self.claude_label)
        layout.addWidget(self.check_button)
        layout.addSpacing(12)
        layout.addWidget(self.close_all_button)
        layout.addStretch(1)
        layout.addWidget(self.five_hour)
        layout.addSpacing(12)
        layout.addWidget(self.seven_day)
        layout.addSpacing(10)
        layout.addWidget(self.updated)

        self._checker = _SignInChecker(self)
        self._checker.done.connect(self._on_sign_in)
        self.check_button.clicked.connect(self.check_sign_in)

        # Watch the folder too: the file may not exist yet, and atomic replaces can drop file watches.
        self.watcher = QFileSystemWatcher(self)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.watcher.addPath(str(usage_path.parent))
        self.watcher.directoryChanged.connect(lambda _p: self.refresh_usage())
        self.watcher.fileChanged.connect(lambda _p: self.refresh_usage())
        self._tick = QTimer(self, interval=30_000)  # re-evaluate staleness and "updated" text
        self._tick.timeout.connect(self.refresh_usage)
        self._tick.start()
        self.refresh_usage()

    def check_sign_in(self) -> None:
        self.check_button.hide()
        self.claude_label.setText("Checking Claude Code sign-in…")
        self.claude_label.setStyleSheet("")
        self.dot.setStyleSheet(f"color: {GREY}; font-size: 12pt;")
        self._checker.run()

    def _on_sign_in(self, status: claude_launcher.SignInStatus) -> None:
        self.signed_in = status.ok
        # Short text in the bar; the full message on hover.
        self.claude_label.setText("Claude Code signed in" if status.ok else "Runs blocked: Claude Code not ready")
        self.claude_label.setStyleSheet("" if status.ok else f"color: {RED}; font-weight: bold;")
        self.dot.setStyleSheet(f"color: {GREEN if status.ok else RED}; font-size: 12pt;")
        for widget in (self.dot, self.claude_label):
            widget.setToolTip(status.message)
        self.check_button.setVisible(not status.ok)
        self.sign_in_changed.emit(status.ok)

    def refresh_usage(self, now: float | None = None) -> None:
        if self.usage_path.exists() and str(self.usage_path) not in self.watcher.files():
            self.watcher.addPath(str(self.usage_path))
        state = usage.load(self.usage_path)
        self.five_hour.show_reading(state.get("five_hour"), now)
        self.seven_day.show_reading(state.get("seven_day"), now)
        observed = max((w.observed_at for w in state.values()), default=None)
        self.updated.setText(
            f"Updated {datetime.fromtimestamp(observed).strftime('%H:%M')}" if observed else "No usage data yet"
        )

