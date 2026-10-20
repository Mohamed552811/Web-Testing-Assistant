"""The Test Case Design workspace for one workbook: pick a backlog (sheet, search, list), check what will be
sent, generate, then edit the test cases and answer the open questions (spec 3.4, 3.5, 8.1)."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db import Database
from wta.db import designer_content as content
from wta.db import designer_settings, generations
from wta.db import workbooks as wb
from wta.designer.backlog_panel import BacklogPanel
from wta.designer.generation import GenerationOutcome, GenerationRun
from wta.designer.questions_panel import QuestionsPanel
from wta.designer.results_panel import ResultsPanel
from wta.designer.search import find

ALL_SHEETS = -1


class DesignerWorkspace(QWidget):
    change_workbook_requested = Signal()
    settings_requested = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database | None = None, work_root: Path | None = None, usage_path: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.work_root, self.usage_path = work_root, usage_path
        self.workbook_id: int | None = None
        self.view: wb.WorkbookView | None = None
        self.run: GenerationRun | None = None
        self.run_backlog_id: int | None = None
        # Set by the main window: why generation cannot start now ("" = it can), and remembered UI values.
        self.generation_blocker: Callable[[], str] = lambda: ""
        self.remember: Callable[[str, object], None] = lambda _key, _value: None
        self.recall: Callable[[str], object] = lambda _key: None

        self.title = QLabel("Test Case Design")
        self.title.setProperty("heading", True)
        self.settings_button = QPushButton("Settings…")
        icons.set_icon(self.settings_button, "settings")
        self.change_button = QPushButton("Change workbook")
        icons.set_icon(self.change_button, "folder-open")
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.settings_button)
        header.addWidget(self.change_button)

        # Left: backlog picker
        self.sheet_combo = QComboBox()
        self.sheet_combo.setAccessibleName("Sheet")
        self.search = QLineEdit(placeholderText="backlog 5, 5 or #5")
        self.search.setAccessibleName("Find backlog")
        self.search_button = QPushButton("Find")
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.search_button)
        self.search_message = QLabel()
        self.search_message.setWordWrap(True)
        self.search_message.setStyleSheet("color: #c0392b;")
        self.search_message.hide()
        self.backlogs = QListWidget()
        self.backlogs.setAccessibleName("Backlogs")
        self.backlogs.setWordWrap(True)
        self.backlogs.setSpacing(3)
        picker = QWidget()
        left = QVBoxLayout(picker)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("Sheet"))
        left.addWidget(self.sheet_combo)
        left.addLayout(search_row)
        left.addWidget(self.search_message)
        left.addWidget(self.backlogs, 1)

        # Right: tabs
        self.backlog_panel = BacklogPanel(db, self) if db is not None else QWidget()
        self.results_panel = ResultsPanel(db, self) if db is not None else QWidget()
        self.questions_panel = QuestionsPanel(db, self) if db is not None else QWidget()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.backlog_panel, "Backlog")
        self.tabs.addTab(self.results_panel, "Test cases")
        self.tabs.addTab(self.questions_panel, "Open questions")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(picker)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1200])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)

        self.change_button.clicked.connect(self.change_workbook_requested)
        self.settings_button.clicked.connect(self.settings_requested)
        self.sheet_combo.currentIndexChanged.connect(self._sheet_changed)
        self.search.returnPressed.connect(self.find_backlog)
        self.search_button.clicked.connect(self.find_backlog)
        self.backlogs.currentItemChanged.connect(lambda current, _prev: self._backlog_chosen(current))
        if db is not None:
            self.backlog_panel.generate_requested.connect(self.generate)
            self.backlog_panel.cancel_requested.connect(self.cancel_generation)
            self.backlog_panel.changed.connect(self._refresh_counts)
            self.results_panel.changed.connect(self._refresh_counts)
            self.questions_panel.answered.connect(self.backlog_panel.refresh)
            self.questions_panel.answered.connect(self._refresh_counts)

    # --- workbook and sheets ---------------------------------------------------------------------

    def set_workbook(self, workbook_id: int, name: str) -> None:
        self.workbook_id = workbook_id
        self.title.setText(f"Test Case Design — {name}")
        if self.db is None:
            return
        self.view = wb.load_workbook(self.db, workbook_id)
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for sheet in self.view.sheets:
            self.sheet_combo.addItem(f"{sheet.name} ({len(sheet.backlogs)})", sheet.id)
        if len(self.view.sheets) > 1:
            self.sheet_combo.addItem("All sheets", ALL_SHEETS)
        last = self.recall(f"designer_sheet_{workbook_id}")
        index = self.sheet_combo.findData(int(last)) if last is not None and str(last).lstrip("-").isdigit() else -1
        self.sheet_combo.setCurrentIndex(max(0, index))  # the last sheet used, else the first
        self.sheet_combo.blockSignals(False)
        self.search_message.hide()
        self._fill_backlogs()

    def _scope(self) -> int | None:
        data = self.sheet_combo.currentData()
        return None if data in (None, ALL_SHEETS) else data

    def _sheet_changed(self) -> None:
        if self.workbook_id is not None:
            self.remember(f"designer_sheet_{self.workbook_id}", self.sheet_combo.currentData())
        self._fill_backlogs()

    def _fill_backlogs(self, select_id: int | None = None) -> None:
        if self.view is None:
            return
        current = select_id if select_id is not None else self.current_backlog_id()
        scope = self._scope()
        self.backlogs.blockSignals(True)
        self.backlogs.clear()
        for sheet in self.view.sheets:
            if scope is not None and sheet.id != scope:
                continue
            for b in sheet.backlogs:
                prefix = f"{sheet.name} · " if scope is None else ""
                repeat = f" ({b.occurrence})" if b.occurrence > 1 else ""
                flags = []
                if not b.text_blocks and not b.images:
                    flags.append("no content")
                if b.unfinished:
                    flags.append("unfinished")
                item = QListWidgetItem(f"{prefix}#{b.number}{repeat}  {b.title}\n"
                                       f"{len(b.text_blocks)} text · {len(b.images)} picture(s)"
                                       + (f" · ⚠ {', '.join(flags)}" if flags else ""))
                item.setData(Qt.ItemDataRole.UserRole, (b.id, sheet.id))
                self.backlogs.addItem(item)
        self.backlogs.blockSignals(False)
        row = next((i for i in range(self.backlogs.count())
                    if self.backlogs.item(i).data(Qt.ItemDataRole.UserRole)[0] == current), -1)
        if row >= 0:
            self.backlogs.setCurrentRow(row)
        else:
            self._backlog_chosen(None)

    def current_backlog_id(self) -> int | None:
        item = self.backlogs.currentItem()
        return item.data(Qt.ItemDataRole.UserRole)[0] if item else None

    def _sheet_of(self, backlog_id: int):
        return next((s for s in self.view.sheets for b in s.backlogs if b.id == backlog_id), None)

    # --- finding and choosing a backlog ------------------------------------------------------------

    def find_backlog(self) -> None:
        """Spec 3.4/3.5: exact match in the chosen sheet; not found lists the numbers; duplicates ask which one."""
        if self.view is None:
            return
        result = find(self.view.sheets, self.search.text(), self._scope())
        if not result.matches:
            self.search_message.setText(result.message)
            self.search_message.show()
            return
        self.search_message.hide()
        match = result.unique
        if match is None:
            labels = [m.label for m in result.matches]
            label, ok = QInputDialog.getItem(self, "Choose backlog", f"Backlog {result.key} was found more than "
                                             "once. Which one do you mean?", labels, 0, False)
            if not ok:
                return
            match = result.matches[labels.index(label)]
        self.select_backlog(match.backlog.id)

    def select_backlog(self, backlog_id: int) -> None:
        row = next((i for i in range(self.backlogs.count())
                    if self.backlogs.item(i).data(Qt.ItemDataRole.UserRole)[0] == backlog_id), -1)
        if row < 0:  # on another sheet: show all sheets
            index = self.sheet_combo.findData(ALL_SHEETS)
            sheet = self._sheet_of(backlog_id)
            self.sheet_combo.setCurrentIndex(index if index >= 0 else self.sheet_combo.findData(sheet.id))
            row = next(i for i in range(self.backlogs.count())
                       if self.backlogs.item(i).data(Qt.ItemDataRole.UserRole)[0] == backlog_id)
        self.backlogs.setCurrentRow(row)

    def _backlog_chosen(self, item: QListWidgetItem | None) -> None:
        if self.db is None:
            return
        backlog_id = item.data(Qt.ItemDataRole.UserRole)[0] if item else None
        sheet = self._sheet_of(backlog_id) if backlog_id is not None else None
        self.backlog_panel.show_backlog(backlog_id, self.workbook_id, sheet.name if sheet else "")
        backlog = self.backlog_panel.backlog
        self.results_panel.show_backlog(backlog_id, backlog.number if backlog else "", designer_settings.load(self.db))
        self.questions_panel.show_backlog(backlog_id)
        if self.run is not None:
            self.backlog_panel.set_running(True, "Generating for another backlog…"
                                           if backlog_id != self.run_backlog_id else self.backlog_panel.progress.text())
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        if self.db is None:
            return
        cases = len(self.results_panel.model.cases)
        self.tabs.setTabText(1, f"Test cases ({cases})" if cases else "Test cases")
        open_count = self.questions_panel.open_count()
        self.tabs.setTabText(2, f"Open questions ({open_count})" if open_count else "Open questions")

    # --- generation ------------------------------------------------------------------------------

    def generate(self) -> None:
        backlog = self.backlog_panel.backlog
        if backlog is None or self.run is not None:
            return
        blocker = self.generation_blocker()
        if blocker:
            QMessageBox.warning(self, "Generate test cases", blocker)
            return
        settings = designer_settings.load(self.db)
        request = generations.request_for_backlog(
            self.db, backlog.id, settings, content.texts(self.db, backlog.id, content.DOMAIN_NOTE),
            content.texts(self.db, backlog.id, content.SUPPLEMENTARY))
        self.run_backlog_id = backlog.id
        self.run = GenerationRun(request, self.work_root or Path("."), self.usage_path, parent=self)
        self.run.progress.connect(lambda text: self._progress(text))
        self.run.finished.connect(self._generated)
        self.backlog_panel.set_running(True, "Starting…")
        self.status_message.emit(f"Generating test cases for backlog {backlog.number}…")
        self.run.start()

    def _progress(self, text: str) -> None:
        if self.current_backlog_id() == self.run_backlog_id:
            self.backlog_panel.set_running(True, text)

    def cancel_generation(self) -> None:
        if self.run is not None:
            self.run.cancel()
            self.run = None
            self.backlog_panel.set_running(False, "Cancelled.")
            self.status_message.emit("Generation cancelled.")

    def _generated(self, outcome: GenerationOutcome) -> None:
        backlog_id, self.run = self.run_backlog_id, None
        generations.save_outcome(self.db, backlog_id, outcome)
        self.backlog_panel.set_running(False, "")
        if backlog_id == self.current_backlog_id():
            self._backlog_chosen(self.backlogs.currentItem())
        if outcome.ok:
            count = len(outcome.response.get("test_cases") or [])
            questions = len(outcome.response.get("open_questions") or [])
            self.status_message.emit(f"{count} test case(s) and {questions} open question(s) generated "
                                     f"with {outcome.model_id}.")
            if backlog_id == self.current_backlog_id():
                self.tabs.setCurrentWidget(self.results_panel)
        elif outcome.status == "insufficient_information":
            QMessageBox.information(self, "Generate test cases", outcome.message)
        elif outcome.status == "invalid":
            shown = "\n".join(f"• {e}" for e in outcome.validation_errors[:10])
            QMessageBox.warning(self, "Generate test cases", f"{outcome.message}\n\n{shown}\n\nThe answer was saved "
                                "with the run but not used. Try again, or add notes that clarify the backlog.")
        else:
            QMessageBox.warning(self, "Generate test cases", outcome.message)
