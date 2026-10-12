"""Table model for a scenario's steps: Claude's result beside the user's review."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from wta.db.models import ReviewDecision, Step, StepStatus

STATUS_LABELS = {
    StepStatus.PASS: "Pass",
    StepStatus.FAIL: "Fail",
    StepStatus.BLOCKED: "Blocked",
    StepStatus.NOT_RUN: "Not run",
}

STATUS_COLORS = {
    StepStatus.PASS: QColor(46, 160, 67, 70),
    StepStatus.FAIL: QColor(218, 54, 51, 80),
    StepStatus.BLOCKED: QColor(210, 153, 34, 80),
    StepStatus.NOT_RUN: QColor(128, 128, 128, 50),
}

PENDING = "Pending review"
RERUN = "Re-run requested"

COLUMNS = (
    "#",
    "Claude status",
    "Final status",
    "Screen",
    "Field / element",
    "Action / input",
    "Expected",
    "Actual",
    "Claude reasoning",
    "Review comment",
)
COLUMN_WIDTHS = (34, 85, 125, 110, 110, 160, 130, 130, 220, 160)
COL_CLAUDE_STATUS = COLUMNS.index("Claude status")
COL_FINAL_STATUS = COLUMNS.index("Final status")


def final_status_text(step: Step) -> str:
    review = step.latest_review
    if review is None:
        return PENDING
    if review.decision == ReviewDecision.RERUN_REQUESTED:
        return RERUN
    label = STATUS_LABELS[review.final_status]
    return f"{label} (override)" if review.decision == ReviewDecision.OVERRIDDEN else label


class StepTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._steps: list[Step] = []

    def set_steps(self, steps: list[Step]) -> None:
        self.beginResetModel()
        self._steps = list(steps)
        self.endResetModel()

    def step_at(self, row: int) -> Step | None:
        return self._steps[row] if 0 <= row < len(self._steps) else None

    def row_of(self, step_id: int) -> int:
        return next((i for i, s in enumerate(self._steps) if s.id == step_id), -1)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._steps)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def _text(self, step: Step, column: int) -> str:
        review = step.latest_review
        return (
            str(step.order),
            STATUS_LABELS[step.status],
            final_status_text(step),
            step.screen_name,
            step.element,
            step.action,
            step.expected_result,
            step.actual_result,
            step.reasoning,
            review.comment if review else "",
        )[column]

    def data(self, index: QModelIndex | QPersistentModelIndex, role=Qt.ItemDataRole.DisplayRole):
        step = self.step_at(index.row()) if index.isValid() else None
        if step is None:
            return None
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self._text(step, column)
        if role == Qt.ItemDataRole.BackgroundRole:
            if column == COL_CLAUDE_STATUS:
                return QBrush(STATUS_COLORS[step.status])
            if column == COL_FINAL_STATUS and step.final_status is not None:
                return QBrush(STATUS_COLORS[step.final_status])
        if role == Qt.ItemDataRole.FontRole and column == COL_FINAL_STATUS and step.final_status is None:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.ItemDataRole.TextAlignmentRole and column == 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        return None
