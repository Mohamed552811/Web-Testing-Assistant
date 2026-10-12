"""Runs tab: reopen past runs and read their traces; write notes about the site."""

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wta.db import Database
from wta.db import runs as run_store
from wta.db import site_notes
from wta.db.models import Run, RunOutcomeKind, TraceKind
from wta.run_panel import KIND_STYLE

OUTCOME_LABELS = {
    RunOutcomeKind.RUNNING: "Running",
    RunOutcomeKind.FINISHED: "Finished",
    RunOutcomeKind.STOPPED: "Stopped",
    RunOutcomeKind.FAILED: "Failed",
}
RUN_COLUMNS = ("Started", "Batch", "Scenario", "Outcome", "Turns", "Duration", "Output tokens", "5-hour", "Site notes")


def local_time(value: datetime | None) -> str:
    """Stored times are naive UTC; show them in local time."""
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _item(text: str, data=None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


class SiteNotesPanel(QGroupBox):
    """Notes about one site (host). They are added to the prompt of the next run on that site."""

    notes_changed = Signal()

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__("Site notes", parent)
        self.db = db
        self.site = ""
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.new_note = QPlainTextEdit(placeholderText="What should the next run on this site do differently?")
        self.new_note.setFixedHeight(56)
        self.add_button = QPushButton("Add note")
        self.delete_button = QPushButton("Delete selected")
        hint = QLabel("Added to the prompt of every following run on this site.")
        hint.setStyleSheet("color: gray;")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(hint)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.new_note)
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add)
        self.delete_button.clicked.connect(self.delete_selected)
        self.set_site("")

    def set_site(self, site: str) -> None:
        self.site = site
        self.setTitle(f"Site notes — {site}" if site else "Site notes")
        self.setEnabled(bool(site))
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for note in site_notes.list_notes(self.db, self.site) if self.site else []:
            item = QListWidgetItem(f"{local_time(note.created_at)[:16]}  {note.note}")
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            self.list.addItem(item)
        self.delete_button.setEnabled(self.list.count() > 0)

    def add(self) -> None:
        try:
            site_notes.add_note(self.db, self.site, self.new_note.toPlainText())
        except ValueError as exc:
            QMessageBox.information(self, "Site notes", str(exc))
            return
        self.new_note.clear()
        self.refresh()
        self.notes_changed.emit()

    def delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        site_notes.delete_note(self.db, item.data(Qt.ItemDataRole.UserRole))
        self.refresh()
        self.notes_changed.emit()


class RunsTab(QWidget):
    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.session_id: int | None = None

        self.runs_table = QTableWidget(0, len(RUN_COLUMNS))
        self.runs_table.setHorizontalHeaderLabels(RUN_COLUMNS)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.verticalHeader().hide()
        self.runs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.refresh_button = QPushButton("Refresh")
        runs_box = QGroupBox("Past runs in this session")
        runs_layout = QVBoxLayout(runs_box)
        runs_layout.addWidget(self.runs_table)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.refresh_button)
        runs_layout.addLayout(row)

        self.summary = QLabel("Select a run to read its trace.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_folder_button = QPushButton("Open run folder")
        self.steps_table = QTableWidget(0, 4)
        self.steps_table.setHorizontalHeaderLabels(["#", "Status", "Action", "Actual"])
        self.steps_table.verticalHeader().hide()
        self.steps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.steps_table.setWordWrap(True)
        self.steps_table.horizontalHeader().setStretchLastSection(True)
        # Left-aligned so a header stays visible even over a very wide column.
        self.steps_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Everything", None)
        for kind, (label, _color) in KIND_STYLE.items():
            self.kind_filter.addItem(label + "s" if label != "Saw" else "What Claude saw", kind)
        self.search = QLineEdit(placeholderText="Search the trace")
        self.search.setClearButtonEnabled(True)
        self.trace = QListWidget()
        self.trace.setWordWrap(True)
        self.trace.setAlternatingRowColors(True)

        trace_box = QWidget()
        trace_layout = QVBoxLayout(trace_box)
        trace_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Show"))
        filters.addWidget(self.kind_filter)
        filters.addWidget(self.search, 1)
        trace_layout.addLayout(filters)
        trace_layout.addWidget(self.trace, 1)
        steps_box = QWidget()
        steps_layout = QVBoxLayout(steps_box)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.addWidget(QLabel("Steps as this run reported them"))
        steps_layout.addWidget(self.steps_table)
        inner = QSplitter(Qt.Orientation.Vertical)
        inner.addWidget(steps_box)
        inner.addWidget(trace_box)
        inner.setSizes([160, 320])
        detail_box = QGroupBox("Run details")
        detail_layout = QVBoxLayout(detail_box)
        head = QHBoxLayout()
        head.addWidget(self.summary, 1)
        head.addWidget(self.open_folder_button, 0, Qt.AlignmentFlag.AlignTop)
        detail_layout.addLayout(head)
        detail_layout.addWidget(inner, 1)

        self.notes_panel = SiteNotesPanel(db)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(runs_box)
        splitter.addWidget(detail_box)
        splitter.addWidget(self.notes_panel)
        splitter.setSizes([170, 480, 170])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.runs_table.currentCellChanged.connect(lambda *_: self._show_run())
        self.refresh_button.clicked.connect(self.refresh)
        self.kind_filter.currentIndexChanged.connect(lambda _i: self._load_trace())
        self.search.textChanged.connect(lambda _t: self._load_trace())
        self.open_folder_button.clicked.connect(self._open_folder)
        self._runs: list[Run] = []
        self.set_session(None, "")

    def set_session(self, session_id: int | None, site: str) -> None:
        self.session_id = session_id
        self.notes_panel.set_site(site)
        self.refresh()

    def refresh(self) -> None:
        keep = self._current_run().id if self._current_run() else None
        self._runs = run_store.list_runs(self.db, self.session_id) if self.session_id is not None else []
        self.runs_table.setRowCount(len(self._runs))
        for row, run in enumerate(self._runs):
            values = (
                local_time(run.started_at),
                str(run.scenario.batch_number),
                run.scenario.name,
                OUTCOME_LABELS[run.outcome],
                "" if run.num_turns is None else str(run.num_turns),
                "" if run.duration_ms is None else f"{run.duration_ms / 1000:.0f} s",
                str(run.output_tokens or ""),
                "" if run.five_hour_seen is None else f"{run.five_hour_seen:.0f}%",
                str(run.site_notes_used or ""),
            )
            for column, value in enumerate(values):
                self.runs_table.setItem(row, column, _item(value, run.id if column == 0 else None))
        self.runs_table.resizeColumnsToContents()
        row = next((i for i, r in enumerate(self._runs) if r.id == keep), 0 if self._runs else -1)
        if row >= 0:
            self.runs_table.selectRow(row)
        self._show_run()

    def _current_run(self) -> Run | None:
        row = self.runs_table.currentRow()
        return self._runs[row] if 0 <= row < len(self._runs) else None

    def _show_run(self) -> None:
        run = self._current_run()
        self.open_folder_button.setEnabled(run is not None)
        self.steps_table.setRowCount(0)
        if run is None:
            self.summary.setText("No runs yet in this session." if self.session_id else "Open a session first.")
            self.trace.clear()
            return
        usage = []
        if run.five_hour_seen is not None:
            before = f"{run.five_hour_before:.0f}% → " if run.five_hour_before is not None else ""
            usage.append(f"5-hour {before}{run.five_hour_seen:.0f}%")
        if run.seven_day_seen is not None:
            before = f"{run.seven_day_before:.0f}% → " if run.seven_day_before is not None else ""
            usage.append(f"weekly {before}{run.seven_day_seen:.0f}%")
        parts = [
            f"<b>{run.scenario.name}</b> — {OUTCOME_LABELS[run.outcome]}",
            f"Started {local_time(run.started_at)}" + (f", ended {local_time(run.ended_at)}" if run.ended_at else ""),
        ]
        if run.site_notes_used:
            parts.append(f"Used {run.site_notes_used} site note(s)")
        if usage:
            parts.append("Plan usage: " + ", ".join(usage))
        if run.summary:
            parts.append(f"Claude: {run.summary}")
        self.summary.setText("<br>".join(p.replace("\n", "<br>") for p in parts))

        steps = run_store.run_steps_from_folder(run.run_dir)
        self.steps_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            for column, key in enumerate(("order", "status", "action", "actual")):
                self.steps_table.setItem(row, column, _item(str(step.get(key, ""))))
        for column in (0, 1):
            self.steps_table.resizeColumnToContents(column)
        self.steps_table.setColumnWidth(2, 300)
        self.steps_table.resizeRowsToContents()
        self._load_trace()

    def _load_trace(self) -> None:
        self.trace.clear()
        run = self._current_run()
        if run is None:
            return
        kind: TraceKind | None = self.kind_filter.currentData()
        for trace in run_store.run_traces(self.db, run.id, kind, self.search.text().strip()):
            label, color = KIND_STYLE[trace.kind]
            text = trace.text if len(trace.text) < 800 else trace.text[:800] + " …"
            item = QListWidgetItem(f"{local_time(trace.timestamp)[11:]}  {label}: {text}")
            item.setForeground(QBrush(color))
            item.setToolTip(trace.text[:4000])
            self.trace.addItem(item)

    def _open_folder(self) -> None:
        run = self._current_run()
        if run is not None and Path(run.run_dir).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(run.run_dir))
