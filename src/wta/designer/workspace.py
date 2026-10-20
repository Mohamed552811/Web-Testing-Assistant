"""The Test Case Design workspace for one workbook: pick a backlog (sheet, search, list), check what will be
sent, generate, then edit the test cases and answer the open questions (spec 3.4, 3.5, 8.1)."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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
from wta.db import checklists, library
from wta.db import workbooks as wb
from wta.designer import export as exporter
from wta.designer.backlog_panel import BacklogPanel
from wta.designer.export_dialog import WORKBOOK, ExportDialog
from wta.designer import handoff
from wta.designer.handoff_dialog import PROMPT, SCENARIOS, SELECTED, HandOffDialog
from wta.designer.generation import GenerationOutcome, GenerationRun
from wta.designer.history_dialog import HistoryDialog
from wta.designer.questions_panel import QuestionsPanel
from wta.designer.results_panel import ResultsPanel
from wta.designer.search import find

ALL_SHEETS = -1


class DesignerWorkspace(QWidget):
    change_workbook_requested = Signal()
    settings_requested = Signal()
    status_message = Signal(str)
    generation_finished = Signal(str, str, bool)  # heading, message, warning: for a Windows notification
    checklist_created = Signal(str, int)  # mode, run id: the main window can open it

    def __init__(self, db: Database | None = None, work_root: Path | None = None, usage_path: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.work_root, self.usage_path = work_root, usage_path
        self.workbook_id: int | None = None
        self.view: wb.WorkbookView | None = None
        self.run: GenerationRun | None = None
        self.run_backlog_id: int | None = None
        # Set by the main window: why generation cannot start now ("" = it can), remembered UI values, the
        # export folder and the tester name for the export log.
        self.export_dir: Path | None = None
        self.tester_name: Callable[[], str] = lambda: ""
        self.generation_blocker: Callable[[], str] = lambda: ""
        self.remember: Callable[[str, object], None] = lambda _key, _value: None
        self.recall: Callable[[str], object] = lambda _key: None

        self.title = QLabel("Test Case Design")
        self.title.setProperty("heading", True)
        self.settings_button = QPushButton("Settings…")
        icons.set_icon(self.settings_button, "settings")
        self.change_button = QPushButton("Change workbook")
        icons.set_icon(self.change_button, "folder-open")
        self.export_button = QPushButton("Export…")
        icons.set_icon(self.export_button, "download")
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.export_button)
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
        self.export_button.clicked.connect(self.export)
        if db is not None:
            self.backlog_panel.history_requested.connect(self.show_history)
            self.results_panel.handoff_requested.connect(self.hand_off)
            self.results_panel.changed.connect(self._update_generation_state)
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
        self._update_generation_state()
        if self.run is not None:
            self.backlog_panel.set_running(True, "Generating for another backlog…"
                                           if backlog_id != self.run_backlog_id else self.backlog_panel.progress.text())
        self._refresh_counts()

    def _update_generation_state(self) -> None:
        backlog = self.backlog_panel.backlog
        if backlog is None:
            self.backlog_panel.set_generation_state(False, 0, 0)
            return
        runs = generations.list_runs(self.db, backlog.id)
        locked = sum(1 for c in self.results_panel.model.cases if c.locked)
        self.backlog_panel.set_generation_state(content.current_run_id(self.db, backlog.id) is not None, len(runs),
                                                locked)

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
        panel = self.backlog_panel
        locked = ([c.data for c in content.current_cases(self.db, backlog.id) if c.locked]
                  if panel.keep_locked.isVisible() and panel.keep_locked.isChecked() else [])
        request = generations.request_for_backlog(
            self.db, backlog.id, settings, content.texts(self.db, backlog.id, content.DOMAIN_NOTE),
            content.texts(self.db, backlog.id, content.SUPPLEMENTARY), panel.guidance.text().strip(), locked)
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
        # Generation takes minutes: tell the user, also when they are in another window.
        number = next((b.number for s in (self.view.sheets if self.view else []) for b in s.backlogs
                       if b.id == backlog_id), "")
        if outcome.ok:
            self.generation_finished.emit(
                "Test cases ready", f"Backlog {number}: {len(outcome.response.get('test_cases') or [])} test "
                f"case(s), {len(outcome.response.get('open_questions') or [])} open question(s).", False)
        else:
            self.generation_finished.emit("Generation needs your attention", f"Backlog {number}: {outcome.message}",
                                          True)
        if outcome.ok:
            if backlog_id == self.current_backlog_id():
                self.backlog_panel.guidance.clear()
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

    # --- history and export -----------------------------------------------------------------------

    def show_history(self) -> None:
        backlog = self.backlog_panel.backlog
        if backlog is None:
            return
        dialog = HistoryDialog(self.db, backlog.id, f"#{backlog.number} {backlog.title}", self)
        dialog.exec()
        if dialog.restored:
            self._backlog_chosen(self.backlogs.currentItem())
            self.status_message.emit("An earlier generation was restored.")

    def _workbook_backlog_ids(self) -> list[int]:
        return [b.id for s in (self.view.sheets if self.view else []) for b in s.backlogs]

    def export(self) -> None:
        """Spec 8.1 (9): one backlog or every backlog of the workbook that has test cases."""
        if self.db is None or self.view is None:
            return
        backlog = self.backlog_panel.backlog
        has_cases = bool(self.results_panel.model.cases)
        all_entries = content.export_entries(self.db, self._workbook_backlog_ids())
        if not all_entries:
            QMessageBox.information(self, "Export test cases", "No backlog of this workbook has test cases yet.")
            return
        settings = designer_settings.load(self.db)
        tags = list(dict.fromkeys([*settings.tag_vocabulary,
                                   *(t for e in all_entries for c in e.test_cases for t in c.get("tags") or [])]))
        label = f"#{backlog.number} {backlog.title}" if backlog is not None and has_cases else None
        dialog = ExportDialog(label, len(all_entries), tags if settings.preset().includes("tags") else [], self)
        if dialog.exec() != ExportDialog.DialogCode.Accepted:
            return
        entries = all_entries if dialog.scope() == WORKBOOK else content.export_entries(self.db, [backlog.id])
        entries = exporter.filter_by_tag(entries, dialog.tag())
        if not any(e.test_cases for e in entries):
            QMessageBox.information(self, "Export test cases", "No test case matches the chosen tag.")
            return
        entries = [e for e in entries if e.test_cases]
        fmt, per_backlog = dialog.fmt(), dialog.per_backlog.isChecked()
        folder = self.export_dir or Path.home()
        stem = exporter.safe_name(Path(self.view.file_name).stem + (
            f"-backlog-{entries[0].backlog_key}" if dialog.scope() != WORKBOOK else "") +
            (f"-{dialog.tag()}" if dialog.tag() else ""))
        if per_backlog:
            chosen = QFileDialog.getExistingDirectory(self, "Export test cases to folder", str(folder))
            target = Path(chosen) / stem if chosen else None
        else:
            chosen, _filter = QFileDialog.getSaveFileName(self, "Export test cases", str(folder / f"{stem}.{fmt}"),
                                                          f"{exporter.FORMATS[fmt]} (*.{fmt})")
            target = Path(chosen) if chosen else None
        if target is None:
            return
        self.write_export(target, fmt, entries, settings, per_backlog, dialog.tag())

    def write_export(self, target: Path, fmt: str, entries, settings, per_backlog: bool, tag: str) -> list[Path]:
        try:
            written = exporter.export(target, fmt, entries, settings, per_backlog)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export test cases", f"Could not export:\n{exc}")
            return []
        content.log_export(self.db, self.tester_name(), fmt, {
            "workbook_id": self.workbook_id, "backlogs": [f"{e.sheet}|{e.backlog_key}" for e in entries],
            "tag": tag, "per_backlog": per_backlog, "files": [str(p) for p in written]})
        cases = sum(len(e.test_cases) for e in entries)
        self.status_message.emit(f"Exported {cases} test case(s) from {len(entries)} backlog(s) to {target}")
        box = QMessageBox(QMessageBox.Icon.Information, "Export test cases",
                          f"Exported {cases} test case(s) from {len(entries)} backlog(s) to:\n{target}", parent=self)
        folder_button = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target if target.is_dir() else target.parent)))
        return written

    # --- hand-off to Smoke / Regression (spec 8.6) --------------------------------------------------

    def _workbook_cases(self) -> list[tuple[int, content.CaseView]]:
        return [(backlog_id, case) for backlog_id in self._workbook_backlog_ids()
                for case in content.current_cases(self.db, backlog_id)]

    def hand_off(self, kind: str) -> None:
        if self.db is None or self.view is None:
            return
        backlog = self.backlog_panel.backlog
        selected = self.results_panel.selected_cases()
        everything = self._workbook_cases()
        tag_counts: dict[str, int] = {}
        for _backlog_id, case in everything:
            for tag in case.data.get("tags") or []:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if not selected and not tag_counts:
            QMessageBox.information(self, "Hand over test cases", "Select test cases first (the ✓ column).")
            return
        mode = kind if kind in checklists.MODES else checklists.REGRESSION
        open_runs = [(r.id, f"{r.name} ({len(r.items)} test cases, {checklists.STATUS_LABELS[r.status]})")
                     for r in checklists.list_runs(self.db, mode)] if kind in checklists.MODES else []
        dialog = HandOffDialog(kind, len(selected), sorted(tag_counts), checklists.default_name(mode, self.view.file_name),
                               open_runs, tag_counts, self)
        if dialog.exec() != HandOffDialog.DialogCode.Accepted:
            return
        if dialog.scope() == SELECTED:
            cases = [checklists.HandOffCase(c.row_id, backlog.id, c.data) for c in selected]
        else:
            cases = [checklists.HandOffCase(c.row_id, backlog_id, c.data) for backlog_id, c in everything
                     if dialog.tag() in (c.data.get("tags") or [])]
        if kind == PROMPT:
            text = handoff.copy_as_prompt(dialog.mode(), [c.data for c in cases],
                                          designer_settings.load(self.db).with_product_context(
                                              self.view.product_context if self.view else ""))
            QGuiApplication.clipboard().setText(text)
            self.status_message.emit(f"Copied a {dialog.mode()} checklist prompt with {len(cases)} test case(s).")
            return
        if kind == SCENARIOS:
            source = f"{self.view.file_name} ({self.view.version_label})"
            items = library.add_items(self.db, [handoff.scenario_input(c.data, source) for c in cases])
            QMessageBox.information(self, "Add as scenarios",
                                    f"Added {len(items)} scenario(s) to the library. In Regression Testing, use "
                                    "\u201cAdd from library\u201d on the Scenarios tab to put them in a batch "
                                    "for Claude Code to run.")
            return
        source = {"workbook": self.view.file_name, "workbook_id": self.view.id, "version": self.view.version_label,
                  "scope": dialog.scope(), "tag": dialog.tag() if dialog.scope() != SELECTED else "",
                  "backlogs": sorted({c.backlog_id for c in cases})}
        if dialog.existing_run() is not None:
            run_id = dialog.existing_run()
            added = checklists.add_to_run(self.db, run_id, cases)
            message = f"Added {added} test case(s) to the run (test cases already in it were skipped)."
        else:
            run_id = checklists.create_run(self.db, kind, dialog.run_name(), cases, source)
            message = f"Created the {kind} run \u201c{dialog.run_name()}\u201d with {len(cases)} test case(s)."
        self.status_message.emit(message)
        box = QMessageBox(QMessageBox.Icon.Information, "Checklist run", message, parent=self)
        open_button = box.addButton("Open the run", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            self.checklist_created.emit(kind, run_id)
