"""Live run view: Claude's notes, actions and what it saw, as they happen."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

from wta.db.models import TraceKind
from wta.runner import RunOutcome, ScenarioJob
from wta.stream import TraceEvent

KIND_STYLE = {
    TraceKind.NOTE: ("Note", QColor(88, 166, 255)),
    TraceKind.ACTION: ("Action", QColor(210, 153, 34)),
    TraceKind.RESULT: ("Saw", QColor(139, 148, 158)),
}


class RunPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.header = QLabel("No run yet. Use “Run batch” on the Scenarios tab.")
        self.header.setWordWrap(True)
        self.header.setStyleSheet("font-weight: bold;")
        self.events = QListWidget()
        self.events.setWordWrap(True)
        self.events.setAlternatingRowColors(True)
        self.events.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.summary = QPlainTextEdit(readOnly=True)
        self.summary.setFixedHeight(150)
        self.summary.setPlaceholderText("Run summary appears here when a scenario finishes.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.events, 1)
        layout.addWidget(self.summary)

    def clear(self) -> None:
        self.events.clear()
        self.summary.clear()

    def scenario_started(self, job: ScenarioJob, index: int, total: int) -> None:
        self.header.setText(f"Running scenario {index} of {total}: {job.title}")
        self._add(f"──── Scenario {index}/{total}: {job.title} ────", QColor(255, 255, 255), bold=True)

    def add_event(self, event: TraceEvent) -> None:
        label, color = KIND_STYLE[event.kind]
        text = event.text if len(event.text) < 600 else event.text[:600] + " …"
        item = self._add(f"{datetime.now():%H:%M:%S}  {label}: {text}", color)
        item.setToolTip(event.text[:4000])

    def scenario_finished(self, outcome: RunOutcome, title: str) -> None:
        s = outcome.summary
        lines = [f"■ {title}: " + ("finished" if s and s.ok else f"ended (exit code {outcome.exit_code})")]
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
        self._add(lines[0], QColor(46, 160, 67) if s and s.ok else QColor(218, 54, 51), bold=True)

    def all_finished(self) -> None:
        self.header.setText("Run finished. Review the steps on the Scenarios tab.")

    def _add(self, text: str, color: QColor, bold: bool = False) -> QListWidgetItem:
        at_bottom = self.events.verticalScrollBar().value() >= self.events.verticalScrollBar().maximum() - 4
        item = QListWidgetItem(text)
        item.setForeground(QBrush(color))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.events.addItem(item)
        if at_bottom:
            self.events.scrollToBottom()
        return item
