"""Generation history of a backlog (spec 8.1 (6)): every run, including failures; a valid one can be restored."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from wta.db import Database
from wta.db import designer_content as content
from wta.db import generations
from wta.runs_tab import local_time

HEADERS = ("When", "Status", "Test cases", "Questions", "Model", "Prompt", "Guidance", "")


class HistoryDialog(QDialog):
    def __init__(self, db: Database, backlog_id: int, title: str, parent=None) -> None:
        super().__init__(parent)
        self.db, self.backlog_id = db, backlog_id
        self.restored = False
        self.setWindowTitle(f"History — {title}")
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFixedHeight(110)
        self.restore_button = QPushButton("Restore this run")
        self.restore_button.setToolTip("Show this run's test cases again; newer runs stay in the history")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.restore_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        note = QLabel("Every generation is kept. Runs of the same backlog in earlier versions of the workbook are "
                      "listed too.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.details)
        layout.addWidget(buttons)
        self.resize(960, 520)
        self.table.itemSelectionChanged.connect(self._selected)
        self.restore_button.clicked.connect(self.restore)
        self.reload()

    def reload(self) -> None:
        self.runs = generations.list_runs(self.db, self.backlog_id)
        current = content.current_run_id(self.db, self.backlog_id)
        self.table.setRowCount(len(self.runs))
        for row, run in enumerate(self.runs):
            values = (local_time(run.created_at), run.status, str(len(run.test_cases)), str(len(run.open_questions)),
                      run.model_id or "–", run.prompt_version, run.user_guidance,
                      "current" if run.id == current else "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, run.id)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self._selected()

    def selected_run(self):
        rows = self.table.selectionModel().selectedRows()
        return self.runs[rows[0].row()] if rows else None

    def _selected(self) -> None:
        run = self.selected_run()
        current = content.current_run_id(self.db, self.backlog_id)
        self.restore_button.setEnabled(run is not None and run.id != current
                                       and run.status in content.CURRENT_STATUSES)
        if run is None:
            self.details.clear()
            return
        lines = [f"Model resolution: {run.model_resolution or '–'}"]
        if run.message:
            lines.append(run.message)
        lines += [f"• {e}" for e in run.validation_errors]
        lines += [f"Call {i}: {a.get('kind')} · {a.get('subtype') or '–'} · {a.get('output_tokens', 0)} output tokens"
                  for i, a in enumerate(run.attempts, 1)]
        self.details.setPlainText("\n".join(lines))

    def restore(self) -> None:
        run = self.selected_run()
        if run is None:
            return
        generations.restore_run(self.db, run.id)
        self.restored = True
        self.reload()

