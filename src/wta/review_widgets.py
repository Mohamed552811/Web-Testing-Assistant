"""Pieces of the review panel: screenshot card, editable bug card, status summary and filter."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db import scenarios as sc
from wta.db.models import BUG_PRIORITIES, BUG_SEVERITIES, Bug, Step, StepStatus
from wta.step_table import effective_status

THUMB_WIDTH = 240


class ScreenshotViewer(QDialog):
    """A screenshot at full size."""

    def __init__(self, path: Path, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        label = QLabel()
        label.setPixmap(QPixmap(str(path)))
        scroll = QScrollArea()
        scroll.setWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.resize(1100, 800)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ScreenshotCard(QGroupBox):
    """Thumbnail of the step's screenshot; click to see it full size."""

    def __init__(self, parent=None) -> None:
        super().__init__("Screenshot", parent)
        self.thumbnail = _ClickableLabel()
        self.thumbnail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumbnail.setToolTip("Click to see it full size")
        self.more = QLabel()
        self.more.setProperty("muted", True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.thumbnail)
        layout.addWidget(self.more)
        self.paths: list[Path] = []
        self.thumbnail.clicked.connect(self.open_viewer)
        self.set_paths([])

    def set_paths(self, paths: list[Path], title: str = "Screenshot") -> None:
        self.paths = [p for p in paths if p.is_file()]
        self.title_text = title
        self.setVisible(bool(self.paths))
        if self.paths:
            pixmap = QPixmap(str(self.paths[0]))
            self.thumbnail.setPixmap(pixmap.scaledToWidth(min(THUMB_WIDTH, pixmap.width()),
                                                          Qt.TransformationMode.SmoothTransformation))
            extra = len(self.paths) - 1
            self.more.setText(f"+ {extra} more screenshot(s) in the run folder" if extra else "")

    def open_viewer(self) -> None:
        if self.paths:
            ScreenshotViewer(self.paths[0], self.title_text, self).exec()


class BugCard(QGroupBox):
    """The bug Claude reported for this step, editable. Claude's original is kept on the first edit."""

    saved = Signal()

    def __init__(self, db, parent=None) -> None:
        super().__init__("Bug report", parent)
        self.db = db
        self.bug: Bug | None = None
        self.title = QLineEdit()
        self.severity = QComboBox()
        self.severity.addItems(list(BUG_SEVERITIES))
        self.priority = QComboBox()
        for p in BUG_PRIORITIES:
            self.priority.addItem(str(p), p)
        self.repro = QPlainTextEdit()
        self.repro.setFixedHeight(70)
        self.expected = QPlainTextEdit()
        self.expected.setFixedHeight(42)
        self.actual = QPlainTextEdit()
        self.actual.setFixedHeight(42)
        self.report = QPlainTextEdit()
        self.report.setFixedHeight(60)
        self.edited_note = QLabel()
        self.edited_note.setProperty("muted", True)
        self.edited_note.setWordWrap(True)
        self.save_button = QPushButton("Save bug")
        icons.set_icon(self.save_button, "check")
        self.revert_button = QPushButton("Use Claude's version")
        icons.set_icon(self.revert_button, "rotate-ccw")

        row = QHBoxLayout()
        row.addWidget(self.severity, 2)
        row.addWidget(QLabel("Priority"))
        row.addWidget(self.priority, 1)
        form = QFormLayout()
        form.addRow("Title", self.title)
        form.addRow("Severity", row)
        form.addRow("Repro steps", self.repro)
        form.addRow("Expected", self.expected)
        form.addRow("Actual", self.actual)
        form.addRow("Report", self.report)
        buttons = QHBoxLayout()
        buttons.addWidget(self.edited_note, 1)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.save_button.clicked.connect(self.save)
        self.revert_button.clicked.connect(self.revert)
        self.set_bug(None)

    def set_bug(self, bug: Bug | None) -> None:
        self.bug = bug
        self.setVisible(bug is not None)
        if bug is None:
            return
        self._fill(bug.title, bug.severity, bug.priority, bug.repro_steps, bug.expected, bug.actual, bug.report_text)
        original = sc.claude_original(bug)
        self.revert_button.setVisible(original is not None)
        self.edited_note.setText("Edited by you. Claude's original is kept." if original else "As Claude reported it.")

    def _fill(self, title, severity, priority, repro, expected, actual, report) -> None:
        self.title.setText(title)
        self.title.setCursorPosition(0)  # show the start of a long title
        self.severity.setCurrentText(severity)
        self.priority.setCurrentIndex(max(0, self.priority.findData(priority)))
        self.repro.setPlainText(repro)
        self.expected.setPlainText(expected)
        self.actual.setPlainText(actual)
        self.report.setPlainText(report)

    def edit(self) -> sc.BugEdit:
        return sc.BugEdit(self.title.text(), self.severity.currentText(), self.priority.currentData(),
                          self.repro.toPlainText(), self.expected.toPlainText(), self.actual.toPlainText(),
                          self.report.toPlainText())

    def save(self) -> None:
        if self.bug is None:
            return
        try:
            sc.update_bug(self.db, self.bug.id, self.edit())
        except ValueError as exc:
            QMessageBox.information(self, "Bug report", str(exc))
            return
        self.saved.emit()

    def revert(self) -> None:
        """Put Claude's version back in the fields (saved when you press Save bug)."""
        original = sc.claude_original(self.bug) if self.bug else None
        if original:
            self._fill(original["title"], original["severity"], original["priority"], original["repro_steps"],
                       original["expected"], original["actual"], original["report_text"])


class StatusFilterBar(QWidget):
    """Summary line with clickable filters: All, Pass, Bug/Fail, Blocked, Not run, To review."""

    filter_changed = Signal(str)

    KEYS = ("all", StepStatus.PASS.value, StepStatus.FAIL.value, StepStatus.BLOCKED.value,
            StepStatus.NOT_RUN.value, "review")

    def __init__(self, labels: dict, noun: str = "steps", parent=None) -> None:
        super().__init__(parent)
        self.labels, self.noun = labels, noun
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for key in self.KEYS:
            button = QToolButton(checkable=True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setProperty("chip", True)
            button.clicked.connect(lambda _checked=False, k=key: self.filter_changed.emit(k))
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.buttons["all"].setChecked(True)
        self.set_steps([])

    @property
    def current(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "all")

    def set_steps(self, steps: list[Step]) -> None:
        counts = {key: 0 for key in self.KEYS}
        counts["all"] = len(steps)
        for step in steps:
            counts[effective_status(step).value] += 1
            if step.final_status is None:
                counts["review"] += 1
        names = {"all": f"All {self.noun}", "review": "To review"}
        for key, button in self.buttons.items():
            name = names.get(key) or self.labels[StepStatus(key)]
            button.setText(f"{name} {counts[key]}")
            button.setVisible(key in ("all", "review") or counts[key] > 0)
        self.counts = counts

    def matches(self, step: Step, key: str | None = None) -> bool:
        key = key or self.current
        if key == "all":
            return True
        if key == "review":
            return step.final_status is None
        return effective_status(step).value == key
