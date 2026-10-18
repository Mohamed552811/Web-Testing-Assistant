"""Live run view: a timeline of Claude's notes, actions and what it saw, with progress and a timer."""

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db.models import TraceKind
from wta.runner import RunOutcome, ScenarioJob
from wta.stream import TraceEvent

KIND_STYLE = {
    TraceKind.NOTE: ("Note", QColor(88, 166, 255)),
    TraceKind.ACTION: ("Action", QColor(210, 153, 34)),
    TraceKind.RESULT: ("Saw", QColor(139, 148, 158)),
}
OK_COLOR, FAIL_COLOR = QColor(46, 160, 67), QColor(218, 54, 51)
COLLAPSED_CHARS = 100  # "what Claude saw" is shown as one line of this length until clicked
FULL_CHARS = 4000

# Icon per Playwright tool (by name prefix); other tools fall back to the kind's icon.
TOOL_ICONS = (
    ("browser_navigate", "globe"), ("browser_click", "mouse-pointer-click"), ("browser_hover", "mouse-pointer-click"),
    ("browser_type", "keyboard"), ("browser_press_key", "keyboard"), ("browser_fill", "keyboard"),
    ("browser_select", "keyboard"), ("browser_take_screenshot", "camera"), ("browser_snapshot", "eye"),
    ("browser_wait", "timer"), ("Write", "file-text"), ("Edit", "file-text"), ("Read", "file-text"),
)
KIND_ICONS = {TraceKind.NOTE: "message-square", TraceKind.ACTION: "mouse-pointer-click", TraceKind.RESULT: "eye"}

FULL_ROLE = Qt.ItemDataRole.UserRole
SHORT_ROLE = Qt.ItemDataRole.UserRole + 1


def icon_for(event: TraceEvent) -> str:
    if event.kind == TraceKind.ACTION:
        return next((name for prefix, name in TOOL_ICONS if event.tool.startswith(prefix)), KIND_ICONS[event.kind])
    return KIND_ICONS[event.kind]


def one_line(text: str, limit: int = COLLAPSED_CHARS) -> str:
    """The text as a single line of at most `limit` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


class RunPanel(QWidget):
    stop_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.header = QLabel("No run yet. Use “Run batch” on the Scenarios tab.")
        self.header.setWordWrap(True)
        self.header.setProperty("heading", True)
        self.header.setStyleSheet("font-weight: bold;")
        self.progress_label = QLabel()
        self.progress_bar = QProgressBar(textVisible=False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setFixedWidth(160)
        self.elapsed_label = QLabel()
        self.elapsed_label.setToolTip("Time since the run started")
        self.elapsed_label.setProperty("muted", True)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop the run now: ends Claude Code and everything it started")
        self.stop_button.setStyleSheet("QPushButton:enabled { background: #da3633; color: white; font-weight: bold; }")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested)

        self.events = QListWidget()
        self.events.setWordWrap(True)
        self.events.setAlternatingRowColors(True)
        self.events.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.events.itemClicked.connect(self.toggle_item)
        # Follow new lines while the view is at the bottom; scrolling up stops following.
        self._follow = True
        bar = self.events.verticalScrollBar()
        bar.rangeChanged.connect(lambda _low, high: bar.setValue(high) if self._follow else None)
        bar.valueChanged.connect(lambda value: setattr(self, "_follow", value >= bar.maximum() - 4))
        hint = QLabel("Click a line to expand or collapse what Claude saw.")
        hint.setProperty("muted", True)
        self.summary = QPlainTextEdit(readOnly=True)
        self.summary.setFixedHeight(150)
        self.summary.setPlaceholderText("Run summary appears here when a scenario finishes.")

        header_row = QHBoxLayout()
        header_row.addWidget(self.header, 1)
        header_row.addWidget(self.stop_button)
        status_row = QHBoxLayout()
        status_row.addWidget(self.progress_label)
        status_row.addWidget(self.progress_bar)
        status_row.addStretch(1)
        status_row.addWidget(self.elapsed_label)
        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addLayout(status_row)
        layout.addWidget(self.events, 1)
        layout.addWidget(hint)
        layout.addWidget(self.summary)

        self.started_at: float | None = None
        self.noun = "step"
        self.run_dir = ""
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._update_elapsed)
        self.set_progress(0, 0)

    def clear(self) -> None:
        self.events.clear()
        self._follow = True
        self.summary.clear()
        self.elapsed_label.clear()

    # --- progress and timer --------------------------------------------------------

    def set_progress(self, done: int, total: int) -> None:
        """'check 6 of 15': the step being worked on (or the last one when all are done)."""
        visible = total > 0
        self.progress_label.setVisible(visible)
        self.progress_bar.setVisible(visible)
        if not visible:
            return
        if done >= total:
            self.progress_label.setText(f"All {total} {self.noun}s done")
        else:
            self.progress_label.setText(f"{self.noun.capitalize()} {done + 1} of {total} · {done} done")
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)

    def _update_elapsed(self, now: float | None = None) -> None:
        if self.started_at is not None:
            elapsed = (now if now is not None else time.monotonic()) - self.started_at
            self.elapsed_label.setText(f"Elapsed {format_elapsed(elapsed)}")

    # --- run events ----------------------------------------------------------------

    def scenario_started(self, job: ScenarioJob, index: int, total: int) -> None:
        self.noun = "check" if job.context.smoke is not None else "step"
        self.run_dir = str(job.run_dir)
        self.header.setText(f"Running {'page' if self.noun == 'check' else 'scenario'} {index} of {total}: {job.title}")
        self.set_progress(0, job.planned_steps)
        item = self._add(f"Scenario {index}/{total}: {job.title}", None, bold=True)
        item.setIcon(icons.themed_icon("play", "accent"))

    def add_event(self, event: TraceEvent) -> None:
        label, color = KIND_STYLE[event.kind]
        full = self.shorten_paths(event.text[:FULL_CHARS])
        stamp = f"{datetime.now():%H:%M:%S}  {label}: "
        if event.kind == TraceKind.RESULT and (len(full) > COLLAPSED_CHARS or "\n" in full.strip()):
            item = self._add(stamp + "▸ " + one_line(full), color)
            item.setData(SHORT_ROLE, stamp + "▸ " + one_line(full))
            item.setData(FULL_ROLE, stamp + "▾ " + full)
            item.setToolTip("Click to see all of it")
        else:
            item = self._add(stamp + (full if len(full) < 600 else full[:600] + " …"), color)
            item.setToolTip(full)
        item.setIcon(icons.themed_icon(icon_for(event), "muted"))

    def shorten_paths(self, text: str) -> str:
        """Files in the run folder by their name only; the folder itself as "the run folder"."""
        if not self.run_dir:
            return text
        for sep in ("\\", "/"):
            text = text.replace(self.run_dir + sep, "")
        return text.replace(self.run_dir, "the run folder")

    def toggle_item(self, item: QListWidgetItem) -> None:
        short, full = item.data(SHORT_ROLE), item.data(FULL_ROLE)
        if short and full:
            item.setText(short if item.text() == full else full)

    def scenario_finished(self, outcome: RunOutcome, title: str) -> None:
        s = outcome.summary
        if outcome.stopped:
            status = "stopped by you"
        else:
            status = "finished" if s and s.ok else f"ended (exit code {outcome.exit_code})"
        lines = [f"■ {title}: {status}"]
        if s:
            lines.append(
                f"  {s.num_turns} turns, {((s.duration_ms or 0) / 1000):.0f} s, "
                f"tokens in {s.input_tokens} / out {s.output_tokens} / cache read {s.cache_read_tokens} "
                f"/ cache write {s.cache_creation_tokens}"
            )
            if s.text:
                lines.append(f"  Claude: {s.text.strip()}")
            lines += [f"  Error: {e}" for e in s.errors]
        for name, label in (("five_hour", "5-hour"), ("seven_day", "Weekly")):
            before, seen = outcome.usage_at_start.get(name), outcome.usage_seen.get(name)
            if seen:
                was = f"{before.percent:.0f}% before → " if before and before.resets_at == seen.resets_at else ""
                lines.append(f"  {label} plan usage: {was}{seen.percent:.0f}% reported during this run")
        lines += [f"  Notice: {n}" for n in outcome.notices]
        lines += [f"  Results problem: {p}" for p in outcome.problems]
        if outcome.exit_code not in (0,) and outcome.stderr_tail.strip():
            lines.append("  stderr: " + outcome.stderr_tail.strip()[-500:])
        self.summary.appendPlainText("\n".join(lines) + "\n")
        ok = bool(s and s.ok)
        item = self._add(lines[0].removeprefix("■ "), OK_COLOR if ok else FAIL_COLOR, bold=True)
        icon_color = OK_COLOR if ok else FAIL_COLOR
        item.setIcon(icons.icon("circle-check" if ok else "circle-x", icon_color.name()))

    def set_running(self, running: bool) -> None:
        self.stop_button.setEnabled(running)
        if running:
            self.started_at = time.monotonic()
            self._update_elapsed()
            self._timer.start()
        else:
            self._timer.stop()
            self._update_elapsed()

    def all_finished(self, stopped: bool = False) -> None:
        self.set_running(False)
        self.header.setText(
            "Run stopped. Steps reached so far are on the Scenarios tab."
            if stopped
            else "Run finished. Review the steps on the Scenarios tab."
        )

    def _add(self, text: str, color: QColor | None, bold: bool = False) -> QListWidgetItem:
        item = QListWidgetItem(text)
        if color is not None:
            item.setForeground(QBrush(color))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.events.addItem(item)
        return item
