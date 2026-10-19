"""Test Case Design setup step (like Cypress's browser choice): pick an uploaded workbook or upload a new one,
see its analysis summary, then enter the workspace."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from wta import icons

WORKBOOK_FILTER = "Excel workbooks (*.xlsx *.xlsm)"


@dataclass(frozen=True)
class WorkbookEntry:
    """One uploaded workbook as listed on the setup page."""

    id: int
    name: str
    detail: str = ""  # for example "Uploaded 2026-10-19 · 2 sheets · 11 backlogs"


class DesignerSetupPage(QWidget):
    upload_requested = Signal(Path)
    workbook_selected = Signal(int)  # show its analysis summary
    workspace_requested = Signal(int)  # enter the workspace with this workbook

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        title = QLabel("Test Case Design")
        title.setProperty("heading", True)
        intro = QLabel("Choose a workbook you uploaded before, or upload a new Excel file of product backlogs.")
        intro.setProperty("muted", True)
        intro.setWordWrap(True)

        self.workbooks = QListWidget()
        self.workbooks.setAccessibleName("Uploaded workbooks")
        self.empty_label = QLabel("No workbooks uploaded yet.")
        self.empty_label.setProperty("muted", True)
        self.upload_button = QPushButton("Upload workbook…")
        icons.set_icon(self.upload_button, "file-plus")
        self.summary = QTextBrowser()
        self.summary.setAccessibleName("Analysis summary")
        self.summary.setPlaceholderText("The analysis summary of the selected workbook appears here.")
        self.enter_button = QPushButton("Open workspace")
        self.enter_button.setProperty("primary", True)
        icons.set_icon(self.enter_button, "play", "on-accent")
        self.enter_button.setEnabled(False)

        left = QVBoxLayout()
        left.addWidget(QLabel("Workbooks"))
        left.addWidget(self.workbooks, 1)
        left.addWidget(self.empty_label)
        left.addWidget(self.upload_button, 0, Qt.AlignmentFlag.AlignLeft)
        right = QVBoxLayout()
        right.addWidget(QLabel("Analysis summary"))
        right.addWidget(self.summary, 1)
        right.addWidget(self.enter_button, 0, Qt.AlignmentFlag.AlignRight)
        columns = QHBoxLayout()
        columns.addLayout(left, 2)
        columns.addSpacing(16)
        columns.addLayout(right, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addSpacing(8)
        layout.addLayout(columns, 1)

        self.upload_button.clicked.connect(self.choose_file)
        self.workbooks.currentItemChanged.connect(self._on_current_changed)
        self.workbooks.itemActivated.connect(lambda _item: self.enter_button.click())
        self.enter_button.clicked.connect(self._enter)
        self.set_workbooks([])

    def choose_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Upload workbook", "", WORKBOOK_FILTER)
        if chosen:
            self.upload_requested.emit(Path(chosen))

    def set_workbooks(self, entries: list[WorkbookEntry], select_id: int | None = None) -> None:
        self.workbooks.clear()
        for entry in entries:
            item = QListWidgetItem(f"{entry.name}\n{entry.detail}" if entry.detail else entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.workbooks.addItem(item)
            if entry.id == select_id:
                self.workbooks.setCurrentItem(item)
        self.workbooks.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        if self.workbooks.currentItem() is None:
            self.summary.clear()
            self.enter_button.setEnabled(False)

    def set_summary(self, html: str) -> None:
        self.summary.setHtml(html)

    def selected_id(self) -> int | None:
        item = self.workbooks.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _on_current_changed(self, current, _previous) -> None:
        self.enter_button.setEnabled(current is not None)
        if current is not None:
            self.workbook_selected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _enter(self) -> None:
        if self.selected_id() is not None:
            self.workspace_requested.emit(self.selected_id())


class DesignerWorkspace(QWidget):
    """The designer's workspace for one workbook. Backlog picker, generation and results arrive in M1–M5."""

    change_workbook_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.workbook_id: int | None = None
        self.title = QLabel("Test Case Design")
        self.title.setProperty("heading", True)
        self.change_button = QPushButton("Change workbook")
        icons.set_icon(self.change_button, "folder-open")
        self.change_button.clicked.connect(self.change_workbook_requested)
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.change_button)
        self.body = QLabel("Backlog selection and test case generation will appear here.")
        self.body.setProperty("muted", True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.addLayout(header)
        layout.addWidget(self.body, 1)

    def set_workbook(self, workbook_id: int, name: str) -> None:
        self.workbook_id = workbook_id
        self.title.setText(f"Test Case Design — {name}")
