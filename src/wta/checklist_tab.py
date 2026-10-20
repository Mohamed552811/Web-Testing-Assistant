"""Checklists tab of the Smoke and Regression modes: manual checklist runs made in Test Case Design (spec 8.6).

The tester picks a run, goes through its items (ID, title, preconditions, numbered steps, expected result) and
records Pass / Fail / Blocked / Skipped, notes and, for failures, a screenshot. Progress shows counts per result.
"""

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db import Database, checklists

RESULT_ICONS = {"pass": "circle-check", "fail": "circle-x", "blocked": "square", "skipped": "arrow-right"}
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"


def _step(step) -> str:
    if isinstance(step, dict):
        return escape(step.get("action", "")) + (f"<br><i>Expected:</i> {escape(step['expected'])}"
                                                 if step.get("expected") else "")
    return escape(str(step))


def case_html(snapshot: dict) -> str:
    """What the tester follows: ID, title, preconditions, numbered steps and the expected result."""
    parts = [f"<h3>{escape(snapshot.get('id', ''))} &nbsp; {escape(snapshot.get('title', ''))}</h3>"]
    meta = [snapshot.get(k) for k in ("type", "priority") if snapshot.get(k)]
    if meta:
        parts.append(f"<p style='color:gray'>{escape(' · '.join(meta))}</p>")
    preconditions = snapshot.get("preconditions") or []
    if preconditions:
        parts.append("<p><b>Preconditions</b></p><ul>" + "".join(f"<li>{escape(p)}</li>" for p in preconditions)
                     + "</ul>")
    parts.append("<p><b>Steps</b></p><ol>" + "".join(f"<li>{_step(s)}</li>" for s in snapshot.get("steps") or [])
                 + "</ol>")
    parts.append(f"<p><b>Expected result</b><br>{escape(snapshot.get('expected_result', ''))}</p>")
    return "".join(parts)


class ChecklistTab(QWidget):
    status_message = Signal(str)

    def __init__(self, db: Database, attachments_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.db, self.attachments_dir = db, Path(attachments_dir)
        self.mode = checklists.REGRESSION
        self.run: checklists.RunView | None = None
        self.export_dir: Path | None = None

        self.run_combo = QComboBox()
        self.run_combo.setAccessibleName("Checklist run")
        self.show_completed = QCheckBox("Show completed")
        self.refresh_button = QPushButton("Refresh from designer")
        self.refresh_button.setToolTip("Draft runs only: take the current version of each test case again")
        self.complete_button = QPushButton("Complete run")
        self.export_button = QPushButton("Export…")
        self.delete_button = QPushButton("Delete run")
        for button, icon in ((self.refresh_button, "rotate-cw"), (self.complete_button, "check"),
                             (self.export_button, "download"), (self.delete_button, "trash-2")):
            icons.set_icon(button, icon)
        top = QHBoxLayout()
        top.addWidget(QLabel("Run"))
        top.addWidget(self.run_combo, 1)
        top.addWidget(self.show_completed)
        actions = QHBoxLayout()
        for button in (self.refresh_button, self.complete_button, self.export_button, self.delete_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.progress = QLabel()
        self.progress.setProperty("muted", True)
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        self.empty.setProperty("muted", True)

        self.items = QListWidget()
        self.items.setWordWrap(True)
        self.items.setSpacing(2)
        self.items.setAccessibleName("Checklist items")
        self.case_view = QTextBrowser()
        self.result_buttons: dict[str, QPushButton] = {}
        self.result_group = QButtonGroup(self)
        self.result_group.setExclusive(False)
        results_row = QHBoxLayout()
        for key, label in checklists.RESULTS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            icons.set_icon(button, RESULT_ICONS[key])
            self.result_buttons[key] = button
            self.result_group.addButton(button)
            results_row.addWidget(button)
        results_row.addStretch(1)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes (what happened, data used, bug number …)")
        self.notes.setFixedHeight(80)
        self.save_notes_button = QPushButton("Save notes")
        self.attach_button = QPushButton("Attach screenshot…")
        self.open_attachment_button = QPushButton("Open screenshot")
        icons.set_icon(self.attach_button, "camera")
        icons.set_icon(self.open_attachment_button, "eye")
        self.attachment_label = QLabel()
        self.attachment_label.setProperty("muted", True)
        notes_row = QHBoxLayout()
        for widget in (self.save_notes_button, self.attach_button, self.open_attachment_button):
            notes_row.addWidget(widget)
        notes_row.addWidget(self.attachment_label, 1)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.case_view, 1)
        detail_layout.addLayout(results_row)
        detail_layout.addWidget(self.notes)
        detail_layout.addLayout(notes_row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.items)
        self.splitter.addWidget(detail)
        self.splitter.setSizes([260, 500])

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(actions)
        layout.addWidget(self.progress)
        layout.addWidget(self.empty)
        layout.addWidget(self.splitter, 1)

        self.run_combo.currentIndexChanged.connect(lambda _i: self._load_run())
        self.show_completed.toggled.connect(lambda _on: self.refresh())
        self.items.currentRowChanged.connect(lambda _row: self._show_item())
        for key, button in self.result_buttons.items():
            button.clicked.connect(lambda checked, k=key: self.set_result(k if checked else None))
        self.save_notes_button.clicked.connect(self.save_notes)
        self.attach_button.clicked.connect(self.attach_screenshot)
        self.open_attachment_button.clicked.connect(self.open_attachment)
        self.refresh_button.clicked.connect(self.refresh_from_designer)
        self.complete_button.clicked.connect(self.toggle_completed)
        self.export_button.clicked.connect(self.export)
        self.delete_button.clicked.connect(self.delete_run)
        self.refresh()

    # --- runs ----------------------------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode in checklists.MODES and mode != self.mode:
            self.mode = mode
            self.refresh()

    def refresh(self, select_run_id: int | None = None) -> None:
        chosen = select_run_id if select_run_id is not None else self.run_combo.currentData()
        runs = checklists.list_runs(self.db, self.mode, self.show_completed.isChecked())
        if select_run_id is not None and all(r.id != select_run_id for r in runs):
            runs = checklists.list_runs(self.db, self.mode, include_completed=True)
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        for run in runs:
            self.run_combo.addItem(f"{run.name} · {checklists.STATUS_LABELS[run.status]} · {len(run.items)} test cases",
                                   run.id)
        index = self.run_combo.findData(chosen) if chosen is not None else 0
        self.run_combo.setCurrentIndex(max(0, index))
        self.run_combo.blockSignals(False)
        what = "Smoke" if self.mode == checklists.SMOKE else "Regression"
        self.empty.setText(f"No {what.lower()} checklist runs yet. In Test Case Design, select test cases and "
                           f"click “Create {what} run…”.")
        self.empty.setVisible(not runs)
        self.splitter.setVisible(bool(runs))
        self._load_run()

    def _load_run(self, keep_row: bool = False) -> None:
        run_id = self.run_combo.currentData()
        row = self.items.currentRow()
        self.run = checklists.load_run(self.db, run_id) if run_id is not None else None
        self.items.blockSignals(True)
        self.items.clear()
        if self.run is not None:
            for item in self.run.items:
                snapshot = item.snapshot
                entry = QListWidgetItem(f"{item.position}. {snapshot.get('id', '')}  {snapshot.get('title', '')}")
                if item.result:
                    entry.setIcon(icons.themed_icon(RESULT_ICONS[item.result]))
                    entry.setToolTip(checklists.RESULTS[item.result])
                entry.setData(Qt.ItemDataRole.UserRole, item.id)
                self.items.addItem(entry)
        self.items.blockSignals(False)
        if self.items.count():
            self.items.setCurrentRow(row if keep_row and 0 <= row < self.items.count() else 0)
        self._show_item()
        self._update_controls()

    def _update_controls(self) -> None:
        run = self.run
        has_run = run is not None
        completed = has_run and run.status == checklists.COMPLETED
        self.progress.setText(run.progress_text if has_run else "")
        self.refresh_button.setEnabled(has_run and run.status == checklists.DRAFT)
        self.complete_button.setEnabled(has_run)
        self.complete_button.setText("Reopen run" if completed else "Complete run")
        self.export_button.setEnabled(has_run)
        self.delete_button.setEnabled(has_run)
        item = self.current_item()
        for button in self.result_buttons.values():
            button.setEnabled(item is not None and not completed)
        for widget in (self.notes, self.save_notes_button, self.attach_button):
            widget.setEnabled(item is not None and not completed)
        self.open_attachment_button.setEnabled(item is not None and bool(item.attachment))

    def current_item(self) -> checklists.ItemView | None:
        row = self.items.currentRow()
        return self.run.items[row] if self.run is not None and 0 <= row < len(self.run.items) else None

    def _show_item(self) -> None:
        item = self.current_item()
        self.case_view.setHtml(case_html(item.snapshot) if item else "")
        for key, button in self.result_buttons.items():
            button.setChecked(item is not None and item.result == key)
        self.notes.setPlainText(item.notes if item else "")
        self.attachment_label.setText(Path(item.attachment).name if item and item.attachment else "")
        self._update_controls()

    # --- recording -----------------------------------------------------------------------------------

    def set_result(self, result: str | None) -> None:
        item = self.current_item()
        if item is None:
            return
        checklists.record(self.db, item.id, result, self.notes.toPlainText())
        row = self.items.currentRow()
        self._reload_runs_keep(row)
        if result == "fail" and not item.attachment:
            self.status_message.emit("Marked as Fail. You can attach a screenshot of the failure.")
        elif result and row + 1 < self.items.count():
            self.items.setCurrentRow(row + 1)  # on to the next item

    def save_notes(self) -> None:
        item = self.current_item()
        if item is not None:
            checklists.record(self.db, item.id, item.result, self.notes.toPlainText())
            self._reload_runs_keep(self.items.currentRow())

    def _reload_runs_keep(self, row: int) -> None:
        run_id = self.run_combo.currentData()
        self.refresh(run_id)
        if 0 <= row < self.items.count():
            self.items.setCurrentRow(row)

    def attach_screenshot(self) -> None:
        item = self.current_item()
        if item is None:
            return
        chosen, _ = QFileDialog.getOpenFileName(self, "Attach screenshot", "", IMAGE_FILTER)
        if chosen:
            checklists.attach(self.db, item.id, Path(chosen), self.attachments_dir)
            self._reload_runs_keep(self.items.currentRow())

    def open_attachment(self) -> None:
        item = self.current_item()
        if item is not None and item.attachment:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.attachments_dir / item.attachment)))

    # --- run actions ---------------------------------------------------------------------------------

    def refresh_from_designer(self) -> None:
        if self.run is None:
            return
        try:
            changed = checklists.refresh_from_designer(self.db, self.run.id)
        except ValueError as exc:
            QMessageBox.information(self, "Refresh from designer", str(exc))
            return
        self._reload_runs_keep(self.items.currentRow())
        self.status_message.emit(f"{changed} test case(s) updated from Test Case Design.")

    def toggle_completed(self) -> None:
        if self.run is None:
            return
        completing = self.run.status != checklists.COMPLETED
        if completing and self.run.counts()["not_run"]:
            answer = QMessageBox.question(self, "Complete run", f"{self.run.counts()['not_run']} test case(s) have no "
                                          "result yet. Complete the run anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        checklists.set_completed(self.db, self.run.id, completing)
        if completing:
            self.show_completed.setChecked(True)
        self.refresh(self.run.id)

    def export(self) -> None:
        if self.run is None:
            return
        folder = self.export_dir or Path.home()
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.run.name).strip() or "checklist"
        chosen, chosen_filter = QFileDialog.getSaveFileName(self, "Export checklist run", str(folder / f"{safe}.xlsx"),
                                                            "Excel (*.xlsx);;CSV (*.csv)")
        if not chosen:
            return
        path = Path(chosen)
        fmt = "csv" if path.suffix.lower() == ".csv" or "csv" in (chosen_filter or "").lower() else "xlsx"
        try:
            checklists.export_run(self.run, path, fmt)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export checklist run", f"Could not export:\n{exc}")
            return
        self.status_message.emit(f"Checklist run exported to {path}")

    def delete_run(self) -> None:
        if self.run is None:
            return
        answer = QMessageBox.question(self, "Delete run", f"Delete the run “{self.run.name}” and its results?")
        if answer == QMessageBox.StandardButton.Yes:
            checklists.delete_run(self.db, self.run.id, self.attachments_dir)
            self.refresh()
