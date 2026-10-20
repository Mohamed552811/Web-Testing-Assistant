"""The generated test cases of a backlog: a table edited in place (spec 5.5, 8.1 (7)).

Every field can be edited; lists (preconditions, steps) are edited one item per line, and per-step style steps as
"action => expected". Edits are saved at once and mark the case as edited; IDs change only on Renumber.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db import Database
from wta.db import designer_content as content
from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings
from wta.designer.validation import case_warnings

STEP_SEPARATOR = " => "
# (field, header); optional fields are left out when the preset turns them off.
COLUMNS = (("_select", "✓"), ("id", "ID"), ("title", "Title"), ("type", "Type"), ("priority", "Priority"), ("tags", "Tags"),
           ("preconditions", "Preconditions"), ("steps", "Steps"), ("expected_result", "Expected result"),
           ("basis", "Basis"), ("source_reference", "Source"), ("_locked", "Locked"), ("_warnings", "Warnings"))
MULTILINE = {"title", "preconditions", "steps", "expected_result", "source_reference"}


def steps_to_text(steps: list, numbered: bool) -> str:
    lines = []
    for n, step in enumerate(steps, 1):
        text = (f"{step.get('action', '')}{STEP_SEPARATOR}{step.get('expected', '')}" if isinstance(step, dict)
                else str(step))
        lines.append(f"{n}. {text}" if numbered else text)
    return "\n".join(lines)


def text_to_steps(text: str, per_step: bool) -> list:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not per_step:
        return lines
    steps = []
    for line in lines:
        action, _sep, expected = line.partition(STEP_SEPARATOR.strip())
        steps.append({"action": action.strip(), "expected": expected.strip()})
    return steps


class CasesModel(QAbstractTableModel):
    edited = Signal()
    selection_changed = Signal()

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.cases: list[content.CaseView] = []
        self.warnings: list[list[str]] = []
        self.settings = DesignerSettings()
        self.backlog_key = ""
        self.columns = list(COLUMNS)
        self.selected: set[int] = set()  # row ids chosen for a hand-off (spec 8.6); not stored

    def load(self, cases: list[content.CaseView], settings: DesignerSettings, backlog_key: str) -> None:
        self.beginResetModel()
        self.cases, self.settings, self.backlog_key = cases, settings, backlog_key
        preset = settings.preset()
        self.columns = [c for c in COLUMNS if c[0].startswith("_") or preset.includes(c[0])]
        self.warnings = case_warnings([c.data for c in cases], settings, backlog_key) if backlog_key else []
        self.selected &= {c.row_id for c in cases}
        self.endResetModel()

    def field(self, column: int) -> str:
        return self.columns[column][0]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt name)
        return 0 if parent.isValid() else len(self.cases)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][1]
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        name = self.field(index.column())
        if name in ("_locked", "_select"):
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if name == "_warnings":
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def _display(self, case: content.CaseView, name: str, editing: bool) -> str:
        value = case.data.get(name)
        if name == "steps":
            return steps_to_text(value or [], numbered=not editing)
        if name == "preconditions":
            return "\n".join(value or []) if editing else "\n".join(f"• {p}" for p in value or [])
        if name == "tags":
            return ", ".join(value or [])
        return "" if value is None else str(value)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        case, name = self.cases[index.row()], self.field(index.column())
        warnings = self.warnings[index.row()] if index.row() < len(self.warnings) else []
        if name == "_select":
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if case.row_id in self.selected else Qt.CheckState.Unchecked
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Select for Create Smoke run, Create Regression run, Add as scenarios or Copy as prompt"
            return None
        if name == "_locked":
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if case.locked else Qt.CheckState.Unchecked
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Locked test cases are kept unchanged when you regenerate."
            return None
        if name == "_warnings":
            if role == Qt.ItemDataRole.DisplayRole:
                return "\n".join(f"⚠ {w}" for w in warnings)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#b26a00")
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            text = self._display(case, name, editing=False)
            return text + ("  ✎" if name == "id" and case.edited else "")
        if role == Qt.ItemDataRole.EditRole:
            return self._display(case, name, editing=True)
        if role == Qt.ItemDataRole.ToolTipRole and name == "id" and case.edited:
            return "Edited by you"
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False
        case, name = self.cases[index.row()], self.field(index.column())
        if name == "_select" and role == Qt.ItemDataRole.CheckStateRole:
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self.selected.add(case.row_id)
            else:
                self.selected.discard(case.row_id)
            self.dataChanged.emit(index, index, [role])
            self.selection_changed.emit()
            return True
        if name == "_locked" and role == Qt.ItemDataRole.CheckStateRole:
            case.locked = Qt.CheckState(value) == Qt.CheckState.Checked
            content.set_locked(self.db, case.row_id, case.locked)
            QTimer.singleShot(0, self.edited.emit)  # reload after the edit has finished
            return True
        if role != Qt.ItemDataRole.EditRole or name.startswith("_"):
            return False
        data = dict(case.data)
        text = str(value)
        if name == "steps":
            data[name] = text_to_steps(text, self.settings.preset().steps_style == PER_STEP_EXPECTED)
        elif name == "preconditions":
            data[name] = [line.strip() for line in text.splitlines() if line.strip()]
        elif name == "tags":
            data[name] = list(dict.fromkeys(t.strip().lower() for t in text.split(",") if t.strip()))
        else:
            data[name] = " ".join(text.split()) if name in ("title", "id") else text.strip()
        if data == case.data:
            return False
        content.update_case(self.db, case.row_id, data)
        case.data, case.edited = data, True  # later edits build on this one before the reload
        QTimer.singleShot(0, self.edited.emit)  # not during the editor's commit
        return True


class CaseDelegate(QStyledItemDelegate):
    """Combos for the enum fields, a multi-line editor for text and lists."""

    def __init__(self, model: CasesModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model

    def _choices(self, name: str) -> list[str] | None:
        preset = self.model.settings.preset()
        return {"type": ["happy_path", "corner_case"], "priority": list(preset.priority_labels),
                "basis": ["specified", "inferred"]}.get(name)

    def createEditor(self, parent, option, index):  # noqa: N802
        name = self.model.field(index.column())
        choices = self._choices(name)
        if choices:
            combo = QComboBox(parent)
            combo.addItems(choices)
            return combo
        if name in MULTILINE:
            editor = QPlainTextEdit(parent)
            editor.setTabChangesFocus(True)
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):  # noqa: N802
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            editor.setCurrentText(value)
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText(value)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # noqa: N802
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        elif isinstance(editor, QPlainTextEdit):
            model.setData(index, editor.toPlainText())
        else:
            super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        rect = option.rect
        if isinstance(editor, QPlainTextEdit):
            rect.setHeight(max(rect.height(), 140))
            rect.setWidth(max(rect.width(), 320))
        editor.setGeometry(rect)


class ResultsPanel(QWidget):
    changed = Signal()
    handoff_requested = Signal(str)  # smoke | regression | scenarios | prompt

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.backlog_id: int | None = None
        self.backlog_key = ""
        self.settings = DesignerSettings()
        self.model = CasesModel(db, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(CaseDelegate(self.model, self.table))
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setAccessibleName("Test cases")
        self.summary = QLabel("No test cases yet. Generate them on the Backlog tab.")
        self.summary.setProperty("muted", True)
        self.tag_filter = QComboBox()
        self.tag_filter.setAccessibleName("Filter by tag")
        self.tag_filter.setMinimumWidth(170)

        self.add_button = QPushButton("Add")
        self.duplicate_button = QPushButton("Duplicate")
        self.delete_button = QPushButton("Delete")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        self.renumber_button = QPushButton("Renumber IDs")
        for button, icon in ((self.add_button, "plus"), (self.duplicate_button, "copy"),
                             (self.delete_button, "trash-2"), (self.up_button, "arrow-up"),
                             (self.down_button, "arrow-down"), (self.renumber_button, "rotate-cw")):
            icons.set_icon(button, icon)
        self.up_button.setToolTip("Move the selected test case up")
        self.down_button.setToolTip("Move the selected test case down")
        tools = QHBoxLayout()
        for button in (self.add_button, self.duplicate_button, self.delete_button, self.up_button, self.down_button,
                       self.renumber_button):
            tools.addWidget(button)
        tools.addSpacing(12)
        self.tag_label = QLabel("Show")
        tools.addWidget(self.tag_label)
        tools.addWidget(self.tag_filter)
        tools.addStretch(1)
        hint = QLabel("Double-click a cell to edit. Lists: one item per line.")
        hint.setProperty("muted", True)
        tools.addWidget(hint)

        self.select_shown_button = QPushButton("Select shown")
        self.select_shown_button.setToolTip("Select every test case shown (use Show to pick a tag first)")
        self.clear_selection_button = QPushButton("Clear selection")
        self.selection_label = QLabel()
        self.selection_label.setProperty("muted", True)
        self.smoke_button = QPushButton("Create Smoke run…")
        self.regression_button = QPushButton("Create Regression run…")
        self.scenarios_button = QPushButton("Add as scenarios…")
        self.prompt_button = QPushButton("Copy as prompt…")
        for button, icon in ((self.smoke_button, "circle-check"), (self.regression_button, "rotate-ccw"),
                             (self.scenarios_button, "bot"), (self.prompt_button, "copy")):
            icons.set_icon(button, icon)
        handoff = QHBoxLayout()
        for widget in (self.select_shown_button, self.clear_selection_button, self.selection_label):
            handoff.addWidget(widget)
        handoff.addStretch(1)
        for button in (self.smoke_button, self.regression_button, self.scenarios_button, self.prompt_button):
            handoff.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.summary)
        layout.addLayout(tools)
        layout.addWidget(self.table, 1)
        layout.addLayout(handoff)

        self.model.edited.connect(self.reload)
        self.add_button.clicked.connect(self.add_case)
        self.duplicate_button.clicked.connect(lambda: self._with_selected(content.duplicate_case))
        self.delete_button.clicked.connect(self.delete_case)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.renumber_button.clicked.connect(self.renumber)
        self.tag_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self.model.selection_changed.connect(self._update_buttons)
        self.select_shown_button.clicked.connect(self.select_shown)
        self.clear_selection_button.clicked.connect(self.clear_selection)
        for button, kind in ((self.smoke_button, "smoke"), (self.regression_button, "regression"),
                             (self.scenarios_button, "scenarios"), (self.prompt_button, "prompt")):
            button.clicked.connect(lambda _checked=False, k=kind: self.handoff_requested.emit(k))
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self._update_buttons()

    def show_backlog(self, backlog_id: int | None, backlog_key: str, settings: DesignerSettings) -> None:
        self.backlog_id, self.backlog_key, self.settings = backlog_id, backlog_key, settings
        self.reload()

    def reload(self, select_row_id: int | None = None) -> None:
        cases = content.current_cases(self.db, self.backlog_id) if self.backlog_id is not None else []
        selected = select_row_id if select_row_id is not None else self.selected_row_id()
        self.model.load(cases, self.settings, self.backlog_key)
        self.table.resizeColumnsToContents()
        for name, width in (("title", 260), ("preconditions", 240), ("steps", 420), ("expected_result", 260),
                            ("_warnings", 220), ("source_reference", 160)):
            column = next((i for i, c in enumerate(self.model.columns) if c[0] == name), None)
            if column is not None:
                self.table.setColumnWidth(column, width)
        self.table.resizeRowsToContents()
        if selected is not None:
            row = next((i for i, c in enumerate(cases) if c.row_id == selected), None)
            if row is not None:
                self.table.selectRow(row)
        self._fill_tags()
        self._apply_filter()
        happy = sum(1 for c in cases if c.data.get("type") == "happy_path")
        warned = sum(1 for w in self.model.warnings if w)
        self.summary.setText(
            f"{len(cases)} test case(s): {happy} happy path(s), {len(cases) - happy} corner case(s)"
            + (f" · {warned} with warnings" if warned else "") if cases
            else "No test cases yet. Generate them on the Backlog tab.")
        self._update_buttons()
        self.changed.emit()

    def _fill_tags(self) -> None:
        chosen = self.tag_filter.currentData()
        tags = list(dict.fromkeys([*self.settings.tag_vocabulary,
                                   *(t for c in self.model.cases for t in c.data.get("tags") or [])]))
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("All test cases", "")
        for tag in tags:
            count = sum(1 for c in self.model.cases if tag in (c.data.get("tags") or []))
            self.tag_filter.addItem(f"Tag “{tag}” ({count})", tag)
        index = self.tag_filter.findData(chosen) if chosen else 0
        self.tag_filter.setCurrentIndex(max(0, index))
        self.tag_filter.setVisible(self.settings.preset().includes("tags"))
        self.tag_label.setVisible(self.tag_filter.isVisibleTo(self))
        self.tag_filter.blockSignals(False)

    def _apply_filter(self) -> None:
        """Spec 8.1 (8): filter by tag (rows are hidden, nothing is changed)."""
        tag = self.tag_filter.currentData() or ""
        for row, case in enumerate(self.model.cases):
            self.table.setRowHidden(row, bool(tag) and tag not in (case.data.get("tags") or []))

    def visible_cases(self) -> list:
        return [c for row, c in enumerate(self.model.cases) if not self.table.isRowHidden(row)]

    def selected_row_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.model.cases[rows[0].row()].row_id if rows and rows[0].row() < len(self.model.cases) else None

    def select_shown(self) -> None:
        self.model.selected |= {c.row_id for c in self.visible_cases()}
        self._refresh_selection()

    def clear_selection(self) -> None:
        self.model.selected.clear()
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        if self.model.cases:
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.model.cases) - 1, 0))
        self._update_buttons()

    def selected_cases(self) -> list:
        return [c for c in self.model.cases if c.row_id in self.model.selected]

    def _update_buttons(self) -> None:
        count = len(self.model.selected)
        self.selection_label.setText(f"{count} selected" if count else "")
        self.clear_selection_button.setEnabled(bool(count))
        self.select_shown_button.setEnabled(bool(self.model.cases))
        has_cases = bool(self.model.cases)
        selected = self.selected_row_id() is not None
        self.add_button.setEnabled(has_cases)
        self.renumber_button.setEnabled(has_cases)
        for button in (self.duplicate_button, self.delete_button, self.up_button, self.down_button):
            button.setEnabled(selected)

    def _with_selected(self, action) -> None:
        row_id = self.selected_row_id()
        if row_id is not None:
            new_id = action(self.db, row_id)
            self.reload(new_id if isinstance(new_id, int) else row_id)

    def add_case(self) -> None:
        if self.backlog_id is None:
            return
        data = content.blank_case(self.settings, self.backlog_key, len(self.model.cases) + 1)
        new_id = content.add_case(self.db, self.backlog_id, data, self.selected_row_id())
        self.reload(new_id)

    def delete_case(self) -> None:
        row_id = self.selected_row_id()
        if row_id is None:
            return
        case = next(c for c in self.model.cases if c.row_id == row_id)
        answer = QMessageBox.question(self, "Delete test case", f"Delete {case.data.get('id')}?")
        if answer == QMessageBox.StandardButton.Yes:
            content.delete_case(self.db, row_id)
            self.reload()

    def _move(self, offset: int) -> None:
        row_id = self.selected_row_id()
        if row_id is not None:
            content.move_case(self.db, row_id, offset)
            self.reload(row_id)

    def renumber(self) -> None:
        if self.backlog_id is not None:
            content.renumber(self.db, self.backlog_id, self.settings, self.backlog_key)
            self.reload()
