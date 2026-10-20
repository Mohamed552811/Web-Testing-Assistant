"""Top strip: Claude Code sign-in state and the 5-hour / weekly plan usage bars."""

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QToolButton, QWidget

from wta import claude_launcher, icons, usage

GREEN, YELLOW, RED, GREY = "#2ea043", "#d29922", "#da3633", "#6e7681"
# Narrower than these, the strip drops details (shown on hover instead), so it never stops the window
# from getting narrow.
HIDE_RESETS_BELOW = 1150
HIDE_UPDATED_BELOW = 820


def _shrinkable(label: QLabel) -> QLabel:
    """A label that never sets the window's minimum width (it is hidden or clipped when narrow): it keeps its
    text's width while there is room, and an explicit minimum of 1 lets the layout shrink it."""
    label.setMinimumWidth(1)
    return label


def level_color(percent: float) -> str:
    return GREEN if percent < 50 else YELLOW if percent < 80 else RED


def format_reset(resets_at: int, now: float | None = None) -> str:
    when = datetime.fromtimestamp(resets_at)
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    return when.strftime("%H:%M") if when.date() == today else when.strftime("%a %d %b %H:%M")


class _WindowBar(QWidget):
    """A slim gauge with the percentage and reset time beside it; details on hover."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = QLabel(title)
        self.title.setProperty("muted", True)
        self.bar = QProgressBar(minimum=0, maximum=1000, textVisible=False)
        self.bar.setFixedSize(90, 6)
        self.value = QLabel()
        self.value.setMinimumWidth(34)
        self.reset = _shrinkable(QLabel())
        self.reset.setProperty("muted", True)
        self.compact = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.title)
        layout.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.value)
        layout.addWidget(self.reset)
        self.show_reading(None)

    def show_reading(self, reading: usage.WindowUsage | None, now: float | None = None) -> None:
        if reading is None:
            self.bar.setValue(0)
            self._show("–", GREY, "")
            self._hover("No usage reported yet. Use refresh, or it fills in during the first test run.")
            return
        if reading.is_stale(now):
            # The window has reset since this reading; the old number no longer applies.
            self.bar.setValue(0)
            self._show("–", GREY, "needs refresh")
            self._hover(f"{self.title.text()} window reset at {format_reset(reading.resets_at, now)}; "
                        f"the last reading ({reading.percent:.0f}%) is out of date. Click refresh.")
            return
        self.bar.setValue(int(min(reading.percent, 100) * 10))
        resets = format_reset(reading.resets_at, now)
        self._show(f"{reading.percent:.0f}%", level_color(reading.percent), f"resets {resets}")
        self._hover(f"{self.title.text()} window: {reading.percent:.0f}% used, resets {resets}.")

    def _show(self, value: str, color: str, reset: str) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"font-weight: 600; color: {color};" if color != GREY else "")
        self.value.setProperty("muted", color == GREY)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)
        self.reset.setText(reset)
        self._fit_reset()
        self.bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 3px; background: rgba(128, 128, 128, 0.25); }"
            f"QProgressBar::chunk {{ border-radius: 3px; background: {color}; }}"
        )

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self._fit_reset()

    def _fit_reset(self) -> None:
        self.reset.setVisible(bool(self.reset.text()) and not self.compact)

    def _hover(self, text: str) -> None:
        text += "\nPlan usage as reported by Claude Code itself (rate_limit_event)."
        for widget in (self, self.title, self.bar, self.value, self.reset):
            widget.setToolTip(text)


class _UsageRefresher(QObject):
    done = Signal(object, str)  # rate_limit_info or None, error text

    def run(self) -> None:
        def check() -> None:
            self.done.emit(*claude_launcher.read_plan_usage())
        threading.Thread(target=check, daemon=True).start()


class _SignInChecker(QObject):
    done = Signal(object, object)  # SignInStatus, NodeStatus

    def run(self) -> None:
        def check() -> None:
            self.done.emit(claude_launcher.check_sign_in(), claude_launcher.check_node())
        threading.Thread(target=check, daemon=True).start()


class TopBar(QWidget):
    """Shows whether runs are allowed (Claude Code signed in) and the plan usage."""

    sign_in_changed = Signal(bool)  # runs possible: Claude Code signed in and Node.js present
    setup_checked = Signal(object, object)  # SignInStatus, NodeStatus
    close_all_requested = Signal()

    def __init__(self, usage_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.usage_path = usage_path
        self.signed_in = False
        self.node_ok = True  # until the check says otherwise
        self.claude_status: claude_launcher.SignInStatus | None = None
        self.node_status: claude_launcher.NodeStatus | None = None

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {GREY}; font-size: 12pt;")
        self.claude_label = _shrinkable(QLabel("Checking Claude Code sign-in…"))
        self.check_button = QPushButton("Check again")
        self.check_button.hide()
        self.five_hour = _WindowBar("5-hour")
        self.seven_day = _WindowBar("Weekly")
        self.updated = QLabel()
        self.updated.setProperty("muted", True)
        _shrinkable(self.updated)
        self.refresh_button = QToolButton()
        self.refresh_button.setAutoRaise(True)
        icons.set_icon(self.refresh_button, "rotate-cw")
        self.refresh_button.setToolTip(
            "Refresh plan usage now. Runs one tiny Claude Code call (Haiku, no tools), "
            "which uses a very small part of your plan."
        )
        self.refresh_button.clicked.connect(self.refresh_from_claude)
        self._refresher = _UsageRefresher(self)
        self._refresher.done.connect(self._on_refreshed)
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
        layout.addSpacing(16)
        layout.addWidget(self.seven_day)
        layout.addSpacing(12)
        layout.addWidget(self.updated)
        layout.addWidget(self.refresh_button)

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

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        width = event.size().width()
        for bar in (self.five_hour, self.seven_day):
            bar.set_compact(width < HIDE_RESETS_BELOW)
        self.updated.setVisible(width >= HIDE_UPDATED_BELOW)
        super().resizeEvent(event)

    def check_sign_in(self) -> None:
        self.check_button.hide()
        self.claude_label.setText("Checking Claude Code sign-in…")
        self.claude_label.setStyleSheet("")
        self.dot.setStyleSheet(f"color: {GREY}; font-size: 12pt;")
        self._checker.run()

    def _on_sign_in(self, status: claude_launcher.SignInStatus,
                    node: claude_launcher.NodeStatus | None = None) -> None:
        self.signed_in = status.ok
        self.node_ok = node is None or node.ok
        self.claude_status, self.node_status = status, node
        ready = status.ok and self.node_ok
        # Short text in the bar; the full message on hover.
        if not status.ok:
            text = "Runs blocked: Claude Code not ready"
        elif not self.node_ok:
            text = "Runs blocked: Node.js missing"
        else:
            text = "Claude Code signed in"
        self.claude_label.setText(text)
        self.claude_label.setStyleSheet("" if ready else f"color: {RED}; font-weight: bold;")
        self.dot.setStyleSheet(f"color: {GREEN if ready else RED}; font-size: 12pt;")
        tip = status.message + (f"\n{node.message}" if node is not None else "")
        for widget in (self.dot, self.claude_label):
            widget.setToolTip(tip)
        self.check_button.setVisible(not ready)
        self.sign_in_changed.emit(ready)
        if node is not None:
            self.setup_checked.emit(status, node)

    def refresh_usage(self, now: float | None = None) -> None:
        if self.usage_path.exists() and str(self.usage_path) not in self.watcher.files():
            self.watcher.addPath(str(self.usage_path))
        state = usage.load(self.usage_path)
        self.five_hour.show_reading(state.get("five_hour"), now)
        self.seven_day.show_reading(state.get("seven_day"), now)
        observed = max((w.observed_at for w in state.values()), default=None)
        if not self.refresh_button.isEnabled():
            return  # a manual refresh is running; keep "Updating…"
        self.updated.setText(
            f"Updated {datetime.fromtimestamp(observed).strftime('%H:%M')}" if observed else "No usage data yet"
        )
        self.updated.setToolTip("")


    def refresh_from_claude(self) -> None:
        """Ask Claude Code for a fresh usage reading (manual refresh)."""
        self.refresh_button.setEnabled(False)
        self.updated.setText("Updating…")
        self._refresher.run()

    def _on_refreshed(self, info: dict | None, error: str) -> None:
        self.refresh_button.setEnabled(True)
        if info is not None:
            usage.record_event(self.usage_path, info)
        self.refresh_usage()
        if info is None:
            self.updated.setText("Refresh failed")
            self.updated.setToolTip(error)
