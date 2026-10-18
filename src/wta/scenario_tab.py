"""Scenario tab and Smoke test tab: batches of up to five scenarios (or pages), their steps, and review.

The smoke tab is the same widget with kind=smoke: pages instead of scenarios, checks instead of
steps, and a failed check is called a Bug.
"""

from html import escape

from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
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
    QScrollArea,
    QSplitter,
    QTableView,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from wta.db import Database
from wta.db import scenarios as sc
from wta.db import site_notes
from wta.db.models import MAX_SCENARIOS_PER_BATCH, ReviewDecision, Scenario, ScenarioKind, Step, StepStatus
from wta.review_widgets import BugCard, ScreenshotCard, StatusFilterBar
from wta.scenario_dialogs import ScenarioDialog, SmokePageDialog, StepDialog
from wta.step_table import (
    BADGE_COLORS,
    COLUMN_WIDTHS,
    SMOKE_LABELS,
    STATUS_LABELS,
    StatusBadgeDelegate,
    StepTableModel,
)

DECISION_LABELS = {
    ReviewDecision.APPROVED: "Approved",
    ReviewDecision.OVERRIDDEN: "Overridden",
    ReviewDecision.RERUN_REQUESTED: "Re-run requested",
}


class ScenarioTab(QWidget):
    status_message = Signal(str)
    run_requested = Signal(int)  # batch number
    site_note_saved = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None, kind: ScenarioKind = ScenarioKind.SCENARIO) -> None:
        super().__init__(parent)
        self.db = db
        self.kind = kind
        self.smoke = kind == ScenarioKind.SMOKE
        self.labels = SMOKE_LABELS if self.smoke else STATUS_LABELS
        self.item_name = "page" if self.smoke else "scenario"
        self.session_id: int | None = None
        self.site = ""
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
        self.add_scenario_button = QPushButton(f"Add {self.item_name}…")
        self.edit_scenario_button = QPushButton("Edit…")
        self.remove_scenario_button = QPushButton("Remove")
        self.scenario_count = QLabel()
        scenario_buttons = QHBoxLayout()
        for w in (self.add_scenario_button, self.edit_scenario_button, self.remove_scenario_button):
            scenario_buttons.addWidget(w)
        scenario_buttons.addStretch(1)
        scenario_buttons.addWidget(self.scenario_count)
        scenario_box = QGroupBox("Pages to smoke-test" if self.smoke else "Scenarios")
        scenario_layout = QVBoxLayout(scenario_box)
        scenario_layout.addWidget(self.scenario_list)
        scenario_layout.addLayout(scenario_buttons)

        # Steps
        self.step_model = StepTableModel(self, self.labels)
        self.step_table = QTableView()
        self.step_table.setModel(self.step_model)
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.step_table.setWordWrap(True)
        self.step_table.verticalHeader().hide()
        self.step_table.setItemDelegate(StatusBadgeDelegate(self.step_model, self.step_table))
        self.step_table.installEventFilter(self)
        self.filter_bar = StatusFilterBar(self.labels, "checks" if self.smoke else "steps")
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
        steps_layout.addWidget(self.filter_bar)
        steps_layout.addWidget(self.step_table)
        steps_layout.addLayout(step_buttons)

        # Review
        self.step_details = QTextBrowser()
        self.screenshot_card = ScreenshotCard()
        self.bug_card = BugCard(db)
        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.addWidget(self.screenshot_card)
        cards_layout.addWidget(self.bug_card)
        cards_layout.addStretch(1)
        self.cards_area = QScrollArea()
        self.cards_area.setWidget(cards)
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QScrollArea.Shape.NoFrame)
        details_split = QSplitter(Qt.Orientation.Horizontal)
        details_split.addWidget(self.step_details)
        details_split.addWidget(self.cards_area)
        details_split.setSizes([420, 340])
        self.comment = QPlainTextEdit(placeholderText="Review comment (optional)")
        self.comment.setFixedHeight(60)
        self.site_note_button = QPushButton("Save as site note")
        self.site_note_button.setToolTip("Keep this comment as a note for the next runs on this site")
        comment_row = QHBoxLayout()
        comment_row.addWidget(self.comment, 1)
        comment_row.addWidget(self.site_note_button, 0, Qt.AlignmentFlag.AlignTop)
        self.approve_button = QPushButton("Approve")
        self.approve_button.setToolTip("Accept Claude's status as final")
        self.override_combo = QComboBox()
        for status, label in self.labels.items():
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
        shortcut_hint = QLabel("Keys: A approve · O override · R re-run · ↑ ↓ move")
        shortcut_hint.setProperty("muted", True)
        review_buttons.addWidget(shortcut_hint)
        self.approve_button.setToolTip("Accept Claude's status as final (A)")
        self.override_button.setToolTip("Set a different final status; Claude's status is kept (O)")
        self.rerun_button.setToolTip("Send the step back; it is not final until reviewed again (R)")
        self.review_box = QGroupBox("Review")
        review_layout = QVBoxLayout(self.review_box)
        review_layout.addWidget(details_split, 1)
        review_layout.addLayout(comment_row)
        review_layout.addLayout(review_buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scenario_box)
        splitter.addWidget(self.steps_box)
        splitter.addWidget(self.review_box)
        splitter.setSizes([110, 280, 480])

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
        self.site_note_button.clicked.connect(self.save_site_note)
        self.filter_bar.filter_changed.connect(lambda _key: self._apply_filter())
        self.bug_card.saved.connect(self._bug_saved)

        self.set_session(None)

    # --- running ---------------------------------------------------------------

    def set_run_state(self, allowed: bool, running: bool, reason: str = "") -> None:
        """Enable "Run batch" only when runs are allowed and none is in progress."""
        self.run_button.setEnabled(allowed and not running)
        self.run_button.setText("Running…" if running else "Run batch")
        self.run_button.setToolTip(
            reason or f"Run Claude Code on {self.item_name}s in this batch that have no results or need a re-run"
        )
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

    def set_session(self, session_id: int | None, site: str = "") -> None:
        self.session_id = session_id
        self.site = site
        self.setEnabled(session_id is not None)
        batches = sc.batch_numbers(self.db, session_id, self.kind) if session_id is not None else []
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
            number = sc.next_batch_number(self.db, self.session_id, self.kind)
        except sc.BatchNotReviewedError as exc:
            QMessageBox.information(self, "New batch", str(exc))
            return
        batches = sc.batch_numbers(self.db, self.session_id, self.kind)
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
            self.status_message.emit(f"Batch {number} started. Add up to {MAX_SCENARIOS_PER_BATCH} {self.item_name}s.")

    # --- scenarios -----------------------------------------------------------

    def _current_scenario(self) -> Scenario | None:
        item = self.scenario_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _load_scenarios(self, select_id: int | None = None) -> None:
        keep = select_id if select_id is not None else getattr(self._current_scenario(), "id", None)
        scenarios = (
            sc.batch_scenarios(self.db, self.session_id, self.batch, self.kind) if self.session_id is not None else []
        )
        self.scenario_list.blockSignals(True)
        self.scenario_list.clear()
        for scenario in scenarios:
            page = scenario.smoke_page
            if page is not None:
                item = QListWidgetItem(f"{scenario.position}. {page.page_name} — {page.url}")
                item.setToolTip(
                    f"How to reach it: {page.how_to_reach or '—'}\nLogin needed: {'yes' if page.login_needed else 'no'}"
                )
            else:
                item = QListWidgetItem(f"{scenario.position}. {scenario.name}")
                item.setToolTip(
                    f"Steps:\n{scenario.test_steps or '—'}\n\nExpected result: {scenario.expected_result or '—'}"
                    f"\nTest data: {scenario.test_data or '—'}"
                )
            item.setData(Qt.ItemDataRole.UserRole, scenario)
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
        dialog = SmokePageDialog(self) if self.smoke else ScenarioDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if self.smoke:
                scenario = sc.add_smoke_page(self.db, self.session_id, self.batch, dialog.data())
            else:
                scenario = sc.add_scenario(self.db, self.session_id, self.batch, dialog.data())
        except sc.BatchFullError as exc:
            QMessageBox.warning(self, f"Add {self.item_name}", str(exc))
            return
        self._load_scenarios(select_id=scenario.id)

    def edit_scenario(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        if scenario.smoke_page is not None:
            dialog = SmokePageDialog(self, sc.SmokePageInput.of(scenario.smoke_page))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                sc.update_smoke_page(self.db, scenario.id, dialog.data())
                self._load_scenarios(select_id=scenario.id)
            return
        dialog = ScenarioDialog(self, sc.ScenarioInput.of(scenario))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            sc.update_scenario(self.db, scenario.id, dialog.data())
            self._load_scenarios(select_id=scenario.id)

    def remove_scenario(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        answer = QMessageBox.question(
            self, f"Remove {self.item_name}", f"Remove “{scenario.name}” and all its steps and reviews?"
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
        self.filter_bar.set_steps(steps)
        title = "Checks" if self.smoke else "Steps"
        self.steps_box.setTitle(f"{title} — {scenario.name}" if scenario else title)
        self.steps_box.setEnabled(scenario is not None)
        row = self.step_model.row_of(keep) if keep is not None else -1
        if row < 0 and steps:
            row = 0
        if row >= 0:
            self._select_row(row)
        self.step_table.resizeRowsToContents()
        self._apply_filter()

    def _select_row(self, row: int) -> None:
        self.step_table.selectionModel().setCurrentIndex(
            self.step_model.index(row, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )

    def _visible_rows(self) -> list[int]:
        return [r for r in range(self.step_model.rowCount()) if not self.step_table.isRowHidden(r)]

    def _apply_filter(self) -> None:
        """Hide the steps the summary filter leaves out; keep a visible step selected."""
        for row in range(self.step_model.rowCount()):
            self.step_table.setRowHidden(row, not self.filter_bar.matches(self.step_model.step_at(row)))
        visible = self._visible_rows()
        if self.step_table.currentIndex().row() not in visible:
            if visible:
                self._select_row(visible[0])
            else:
                self.step_table.selectionModel().clear()
        self._show_step()

    def move_step(self, delta: int) -> None:
        """Select the next (+1) or previous (-1) visible step."""
        visible = self._visible_rows()
        if not visible:
            return
        current = self.step_table.currentIndex().row()
        index = visible.index(current) + delta if current in visible else 0
        self._select_row(visible[max(0, min(len(visible) - 1, index))])

    def add_step(self) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            return
        dialog = StepDialog(self, labels=self.labels)
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
        dialog = StepDialog(self, current, labels=self.labels)
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
        self.site_note_button.setEnabled(has_step and bool(self.site))
        if step is None:
            self.step_details.clear()
            self.screenshot_card.set_paths([])
            self.bug_card.set_bug(None)
            self.cards_area.hide()
            return
        self.override_combo.setCurrentIndex(self.override_combo.findData(step.final_status or step.status))
        self.step_details.setHtml(_step_html(step, self.labels))
        noun = "Check" if self.smoke else "Step"
        self.screenshot_card.set_paths([Path(s.file_path) for s in step.screenshots], f"{noun} {step.order} screenshot")
        self.bug_card.set_bug(step.bugs[0] if step.bugs else None)
        self.cards_area.setVisible(not self.screenshot_card.isHidden() or not self.bug_card.isHidden())

    def _bug_saved(self) -> None:
        step = self._current_step()
        self.status_message.emit("Bug report saved. Claude's original is kept.")
        self._load_steps(select_id=step.id if step else None)

    def save_site_note(self) -> None:
        if not self.site:
            return
        try:
            site_notes.add_note(self.db, self.site, self.comment.toPlainText())
        except ValueError as exc:
            QMessageBox.information(self, "Save as site note", f"{exc} Write the note in the comment box first.")
            return
        self.status_message.emit(f"Saved as a site note for {self.site}. It is added to the next runs on this site.")
        self.site_note_saved.emit()

    # --- keyboard ------------------------------------------------------------

    def handle_review_key(self, event: QKeyEvent, arrows: bool = True) -> bool:
        """A approve, O override, R re-run, Up/Down move between steps. True when handled."""
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return False
        key = event.key()
        buttons = {Qt.Key.Key_A: self.approve_button, Qt.Key.Key_O: self.override_button,
                   Qt.Key.Key_R: self.rerun_button}
        if key in buttons:
            if self.review_box.isEnabled() and buttons[key].isEnabled():
                buttons[key].click()
            return True
        if arrows and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_step(-1 if key == Qt.Key.Key_Up else 1)
            return True
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt name)
        # The table would use letters for type-ahead search; take A/O/R first. Its arrows stay native.
        if watched is self.step_table and event.type() == QEvent.Type.KeyPress:
            if self.handle_review_key(event, arrows=False):
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt name)
        # Reaches here only when the focused widget did not use the key, so never while typing.
        if not self.handle_review_key(event):
            super().keyPressEvent(event)

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


def _badge(status: StepStatus, labels: dict) -> str:
    return (f"<span style='background:{BADGE_COLORS[status]};color:#ffffff;font-weight:bold'>"
            f"&nbsp;{escape(labels[status])}&nbsp;</span>")


def _step_html(step: Step, labels: dict = STATUS_LABELS) -> str:
    def row(label: str, value: str) -> str:
        return (f"<tr><td style='color:gray;padding:2px 10px 2px 0' valign='top'>{label}</td>"
                f"<td style='padding:2px 0'>{escape(value or '—')}</td></tr>")

    final = step.final_status
    state = _badge(final, labels) + "&nbsp;final" if final else "<i>pending review</i>"
    location = " · ".join(escape(v) for v in (step.screen_name, step.element) if v)
    parts = [
        f"<p style='font-size:large'><b>Step {step.order}</b>&nbsp;&nbsp;{state}</p>",
        f"<p style='color:gray'>{location}</p>" if location else "",
        "<p><b>What happened</b></p><table>",
        row("Action / input", step.action),
        row("Expected", step.expected_result),
        row("Actual", step.actual_result),
        "</table>",
        f"<p><b>Claude</b>&nbsp;&nbsp;{_badge(step.status, labels)}</p>",
        f"<p>{escape(step.reasoning or '—')}</p>",
    ]
    if step.reviews:
        parts.append("<p><b>Review history</b></p><ul>")
        for review in reversed(step.reviews):
            final = f" → {labels[review.final_status]}" if review.final_status else ""
            comment = f": {escape(review.comment)}" if review.comment else ""
            when = review.reviewed_at.strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"<li>{when} — {DECISION_LABELS[review.decision]}{final}{comment}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p><i>Not reviewed yet. This step is not final.</i></p>")
    return "".join(parts)
