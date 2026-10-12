"""Scenario tab: batches of up to five scenarios, their steps, and step review."""

from html import escape

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from wta.db import Database
from wta.db import scenarios as sc
from wta.db.models import MAX_SCENARIOS_PER_BATCH, ReviewDecision, Scenario, Step, StepStatus
from wta.scenario_dialogs import ScenarioDialog, StepDialog
from wta.step_table import COLUMN_WIDTHS, STATUS_LABELS, StepTableModel

DECISION_LABELS = {
    ReviewDecision.APPROVED: "Approved",
    ReviewDecision.OVERRIDDEN: "Overridden",
    ReviewDecision.RERUN_REQUESTED: "Re-run requested",
}


class ScenarioTab(QWidget):
    status_message = Signal(str)
    run_requested = Signal(int)  # batch number

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.session_id: int | None = None
        self.batch: int = 1

        # Batch row
        self.batch_combo = QComboBox()
        self.new_batch_button = QPushButton("New batch…")
        self.run_button = QPushButton("Run batch")
        self.run_button.setToolTip("Run Claude Code on scenarios in this batch that have no results or need a re-run")
        self.run_button.setStyleSheet("font-weight: bold;")
        batch_row = QHBoxLayout()
        batch_row.addWidget(QLabel("Batch"))
        batch_row.addWidget(self.batch_combo)
        batch_row.addWidget(self.new_batch_button)
        batch_row.addStretch(1)
        batch_row.addWidget(self.run_button)

        # Scenarios
        self.scenario_list = QListWidget()
        self.add_scenario_button = QPushButton("Add scenario…")
        self.edit_scenario_button = QPushButton("Edit…")
        self.remove_scenario_button = QPushButton("Remove")
        self.scenario_count = QLabel()
        scenario_buttons = QHBoxLayout()
        for w in (self.add_scenario_button, self.edit_scenario_button, self.remove_scenario_button):
            scenario_buttons.addWidget(w)
        scenario_buttons.addStretch(1)
        scenario_buttons.addWidget(self.scenario_count)
        scenario_box = QGroupBox("Scenarios")
        scenario_layout = QVBoxLayout(scenario_box)
        scenario_layout.addWidget(self.scenario_list)
        scenario_layout.addLayout(scenario_buttons)

        # Steps
        self.step_model = StepTableModel(self)
        self.step_table = QTableView()
        self.step_table.setModel(self.step_model)
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.step_table.setWordWrap(True)
        self.step_table.verticalHeader().hide()
        header = self.step_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate(COLUMN_WIDTHS):
            self.step_table.setColumnWidth(column, width)
        self.add_step_button = QPushButton("Add step…")
        self.edit_step_button = QPushButton("Edit step…")
        self.delete_step_button = QPushButton("Delete step")
        step_buttons = QHBoxLayout()
        for w in (self.add_step_button, self.edit_step_button, self.delete_step_button):
            step_buttons.addWidget(w)
        step_buttons.addStretch(1)
        self.steps_box = QGroupBox("Steps")
        steps_layout = QVBoxLayout(self.steps_box)
        steps_layout.addWidget(self.step_table)
        steps_layout.addLayout(step_buttons)

        # Review
        self.step_details = QTextBrowser()
        self.comment = QPlainTextEdit(placeholderText="Review comment (optional)")
        self.comment.setFixedHeight(60)
        self.approve_button = QPushButton("Approve")
        self.approve_button.setToolTip("Accept Claude's status as final")
        self.override_combo = QComboBox()
        for status, label in STATUS_LABELS.items():
            self.override_combo.addItem(label, status)
        self.override_button = QPushButton("Override")
        self.override_button.setToolTip("Set a different final status; Claude's status is kept")
        self.rerun_button = QPushButton("Request re-run")
        self.rerun_button.setToolTip("Send the step back; it is not final until reviewed again")
        review_buttons = QHBoxLayout()
        review_buttons.addWidget(self.approve_button)
        review_buttons.addSpacing(12)
        review_buttons.addWidget(QLabel("Final status"))
        review_buttons.addWidget(self.override_combo)
        review_buttons.addWidget(self.override_button)
        review_buttons.addSpacing(12)
        review_buttons.addWidget(self.rerun_button)
        review_buttons.addStretch(1)
        self.review_box = QGroupBox("Review")
        review_layout = QVBoxLayout(self.review_box)
        review_layout.addWidget(self.step_details, 1)
        review_layout.addWidget(self.comment)
        review_layout.addLayout(review_buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scenario_box)
        splitter.addWidget(self.steps_box)
        splitter.addWidget(self.review_box)
        splitter.setSizes([130, 330, 340])

        layout = QVBoxLayout(self)
        layout.addLayout(batch_row)
        layout.addWidget(splitter, 1)

        self.batch_combo.currentIndexChanged.connect(self._on_batch_chosen)
        self.new_batch_button.clicked.connect(self.start_new_batch)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.batch))
        self.scenario_list.currentRowChanged.connect(lambda _row: self._load_steps())
        self.scenario_list.itemDoubleClicked.connect(lambda _item: self.edit_scenario())
        self.add_scenario_button.clicked.connect(self.add_scenario)
        self.edit_scenario_button.clicked.connect(self.edit_scenario)
        self.remove_scenario_button.clicked.connect(self.remove_scenario)
        self.step_table.selectionModel().currentRowChanged.connect(lambda *_: self._show_step())
        self.step_table.doubleClicked.connect(lambda _index: self.edit_step())
        self.add_step_button.clicked.connect(self.add_step)
        self.edit_step_button.clicked.connect(self.edit_step)
        self.delete_step_button.clicked.connect(self.delete_step)
        self.approve_button.clicked.connect(self.approve)
        self.override_button.clicked.connect(self.override)
        self.rerun_button.clicked.connect(self.request_rerun)

        self.set_session(None)

    # --- running ---------------------------------------------------------------

    def set_run_state(self, allowed: bool, running: bool, reason: str = "") -> None:
        """Enable "Run batch" only when runs are allowed and none is in progress."""
        self.run_button.setEnabled(allowed and not running)
        self.run_button.setText("Running…" if running else "Run batch")
        self.run_button.setToolTip(reason or "Run Claude Code on scenarios in this batch that have no results or need a re-run")
        # Editing the batch while Claude writes results into it would be confusing.
        self.new_batch_button.setEnabled(not running)
        self.batch_combo.setEnabled(not running)
        if running:
            self.add_scenario_button.setEnabled(False)
            self.remove_scenario_button.setEnabled(False)
        else:
            self._load_scenarios()  # recomputes the scenario buttons (e.g. batch full)

    def refresh(self, scenario_id: int | None = None) -> None:
        """Reload after results changed; keeps the current selection."""
        current = self._current_scenario()
        if scenario_id is None or (current is not None and current.id == scenario_id):
            self._load_steps()

    # --- session and batch ---------------------------------------------------

    def set_session(self, session_id: int | None) -> None:
        self.session_id = session_id
        self.setEnabled(session_id is not None)
        batches = sc.batch_numbers(self.db, session_id) if session_id is not None else []
        self._fill_batches(batches, batches[-1] if batches else 1)

    def _fill_batches(self, batches: list[int], current: int) -> None:
        numbers = sorted(set(batches) | {current})
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        for number in numbers:
            self.batch_combo.addItem(f"Batch {number}", number)
        self.batch_combo.setCurrentIndex(numbers.index(current))
        self.batch_combo.blockSignals(False)
        self.batch = current
        self._load_scenarios()

    def _on_batch_chosen(self, index: int) -> None:
        if index >= 0:
            self.batch = self.batch_combo.itemData(index)
            self._load_scenarios()

    def start_new_batch(self) -> None:
        if self.session_id is None:
            return
        try:
            number = sc.next_batch_number(self.db, self.session_id)
        except sc.BatchNotReviewedError as exc:
            QMessageBox.information(self, "New batch", str(exc))
            return
        batches = sc.batch_numbers(self.db, self.session_id)
        already_shown = self.batch_combo.findData(number) >= 0
        if number == 1 or already_shown:
            # Nothing new to confirm: the first batch, or an empty batch the user already started.
            self._fill_batches(batches, number)
            return
        answer = QMessageBox.question(
            self,
            "Start another batch?",
            f"Running batch {number} will use more of your Claude plan.\n\nStart batch {number}?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._fill_batches(batches, number)
            self.status_message.emit(f"Batch {number} started. Add up to {MAX_SCENARIOS_PER_BATCH} scenarios.")

    # --- scenarios -----------------------------------------------------------

    def _current_scenario(self) -> Scenario | None:
        item = self.scenario_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _load_scenarios(self, select_id: int | None = None) -> None:
        keep = select_id if select_id is not None else getattr(self._current_scenario(), "id", None)
        scenarios = sc.batch_scenarios(self.db, self.session_id, self.batch) if self.session_id is not None else []
        self.scenario_list.blockSignals(True)
        self.scenario_list.clear()
        for scenario in scenarios:
            item = QListWidgetItem(f"{scenario.position}. {scenario.name}")
            item.setData(Qt.ItemDataRole.UserRole, scenario)
            item.setToolTip(f"Goal: {scenario.goal or '—'}\nTest data: {scenario.test_data or '—'}")
            self.scenario_list.addItem(item)
        row = next((i for i, s in enumerate(scenarios) if s.id == keep), 0 if scenarios else -1)
        self.scenario_list.setCurrentRow(row)
        self.scenario_list.blockSignals(False)

        self.scenario_count.setText(f"{len(scenarios)} / {MAX_SCENARIOS_PER_BATCH}")
        self.add_scenario_button.setEnabled(len(scenarios) < MAX_SCENARIOS_PER_BATCH)
        self.edit_scenario_button.setEnabled(bool(scenarios))
        self.remove_scenario_button.setEnabled(bool(scenarios))
        self._load_steps()

    def add_scenario(self) -> None:
        dialog = ScenarioDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            scenario = sc.add_scenario(self.db, self.session_id, self.batch, dialog.data())
        except sc.BatchFullError as exc:
            QMessageBox.warning(self, "Add scenario", str(exc))
            return
        self._load_scenarios(select_id=scenario.id)

    def edit_scenario(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        dialog = ScenarioDialog(self, sc.ScenarioInput(scenario.name, scenario.goal, scenario.test_data))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            sc.update_scenario(self.db, scenario.id, dialog.data())
            self._load_scenarios(select_id=scenario.id)

    def remove_scenario(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        answer = QMessageBox.question(
            self, "Remove scenario", f"Remove “{scenario.name}” and all its steps and reviews?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            sc.delete_scenario(self.db, scenario.id)
            self._load_scenarios()

    # --- steps ---------------------------------------------------------------

    def _current_step(self) -> Step | None:
        return self.step_model.step_at(self.step_table.currentIndex().row())

    def _load_steps(self, select_id: int | None = None) -> None:
        keep = select_id if select_id is not None else getattr(self._current_step(), "id", None)
        scenario = self._current_scenario()
        steps = sc.scenario_steps(self.db, scenario.id) if scenario else []
        self.step_model.set_steps(steps)
        self.steps_box.setTitle(f"Steps — {scenario.name}" if scenario else "Steps")
        self.steps_box.setEnabled(scenario is not None)
        row = self.step_model.row_of(keep) if keep is not None else -1
        if row < 0 and steps:
            row = 0
        if row >= 0:
            self.step_table.selectionModel().setCurrentIndex(
                self.step_model.index(row, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
        self.step_table.resizeRowsToContents()
        self._show_step()

    def add_step(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        dialog = StepDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            step = sc.add_step(self.db, scenario.id, dialog.data())
            self._load_steps(select_id=step.id)

    def edit_step(self) -> None:
        step = self._current_step()
        if step is None:
            return
        current = sc.StepInput(
            screen_name=step.screen_name, element=step.element, action=step.action,
            expected_result=step.expected_result, actual_result=step.actual_result,
            status=step.status, reasoning=step.reasoning,
        )
        dialog = StepDialog(self, current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            sc.update_step(self.db, step.id, dialog.data())
            self._load_steps(select_id=step.id)

    def delete_step(self) -> None:
        step = self._current_step()
        if step is None:
            return
        answer = QMessageBox.question(self, "Delete step", f"Delete step {step.order} and its reviews?")
        if answer == QMessageBox.StandardButton.Yes:
            sc.delete_step(self.db, step.id)
            self._load_steps()

    # --- review --------------------------------------------------------------

    def _show_step(self) -> None:
        step = self._current_step()
        has_step = step is not None
        for w in (self.edit_step_button, self.delete_step_button):
            w.setEnabled(has_step)
        self.review_box.setEnabled(has_step)
        self.review_box.setTitle(f"Review — step {step.order}" if step else "Review")
        if step is None:
            self.step_details.clear()
            return
        self.override_combo.setCurrentIndex(self.override_combo.findData(step.final_status or step.status))
        self.step_details.setHtml(_step_html(step))

    def _review(self, action) -> None:
        step = self._current_step()
        if step is None:
            return
        try:
            action(step)
        except ValueError as exc:
            QMessageBox.information(self, "Review", str(exc))
            return
        self.comment.clear()
        self._load_steps(select_id=step.id)

    def approve(self) -> None:
        self._review(lambda step: sc.approve_step(self.db, step.id, self.comment.toPlainText()))

    def override(self) -> None:
        status = StepStatus(self.override_combo.currentData())
        self._review(lambda step: sc.override_step(self.db, step.id, status, self.comment.toPlainText()))

    def request_rerun(self) -> None:
        self._review(lambda step: sc.request_rerun(self.db, step.id, self.comment.toPlainText()))


def _step_html(step: Step) -> str:
    def row(label: str, value: str) -> str:
        return f"<tr><td style='color:gray;padding-right:8px'>{label}</td><td>{escape(value or '—')}</td></tr>"

    parts = [
        "<table>",
        row("Action / input", step.action),
        row("Expected", step.expected_result),
        row("Actual", step.actual_result),
        row("Claude status", STATUS_LABELS[step.status]),
        row("Claude reasoning", step.reasoning),
        "</table>",
    ]
    if step.reviews:
        parts.append("<p><b>Review history</b></p><ul>")
        for review in reversed(step.reviews):
            final = f" → {STATUS_LABELS[review.final_status]}" if review.final_status else ""
            comment = f": {escape(review.comment)}" if review.comment else ""
            when = review.reviewed_at.strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"<li>{when} — {DECISION_LABELS[review.decision]}{final}{comment}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p><i>Not reviewed yet. This step is not final.</i></p>")
    return "".join(parts)
