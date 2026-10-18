"""Table model for a scenario's steps: Claude's result beside the user's review."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from wta.db.models import ReviewDecision, Step, StepStatus

STATUS_LABELS = {
    StepStatus.PASS: "Pass",
    StepStatus.FAIL: "Fail",
    StepStatus.BLOCKED: "Blocked",
    StepStatus.NOT_RUN: "Not run",
}

# The smoke-test tab calls a failed check a Bug.
SMOKE_LABELS = {status: status.smoke_label for status in StepStatus}

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
COLUMN_WIDTHS = (34, 105, 130, 110, 110, 160, 130, 130, 220, 160)
COL_CLAUDE_STATUS = COLUMNS.index("Claude status")
COL_FINAL_STATUS = COLUMNS.index("Final status")


def final_status_text(step: Step, labels: dict = STATUS_LABELS) -> str:
    review = step.latest_review
    if review is None:
        return PENDING
    if review.decision == ReviewDecision.RERUN_REQUESTED:
        return RERUN
    label = labels[review.final_status]
    return f"{label} (override)" if review.decision == ReviewDecision.OVERRIDDEN else label


class StepTableModel(QAbstractTableModel):
    def __init__(self, parent=None, labels: dict = STATUS_LABELS) -> None:
        super().__init__(parent)
        self.labels = labels
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
            self.labels[step.status],
            final_status_text(step, self.labels),
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


# --- status badges ----------------------------------------------------------------

# Solid badge colours. Fail/Bug is a bright red, deliberately unlike the brick-red accent.
BADGE_COLORS = {
    StepStatus.PASS: "#2E8B57",
    StepStatus.FAIL: "#E5484D",
    StepStatus.BLOCKED: "#C77700",
    StepStatus.NOT_RUN: "#6B7280",
}


def effective_status(step: Step) -> StepStatus:
    """The status that counts: the reviewed one, or Claude's while not reviewed."""
    return step.final_status if step.final_status is not None else step.status


class StatusBadgeDelegate(QStyledItemDelegate):
    """Draws the status columns as coloured pills; "Pending review" and "Re-run requested" as outlines."""

    def __init__(self, model: StepTableModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        step = self.model.step_at(index.row())
        if step is None or index.column() not in (COL_CLAUDE_STATUS, COL_FINAL_STATUS):
            super().paint(painter, option, index)
            return
        # Background and selection as usual, but no text: the badge carries it.
        clean = QStyleOptionViewItem(option)
        self.initStyleOption(clean, index)
        clean.text = ""
        clean.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, clean, painter, widget)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        status = step.status if index.column() == COL_CLAUDE_STATUS else step.final_status
        font = QFont(option.font)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 0.5))
        font.setBold(status is not None)
        metrics = QFontMetrics(font)
        width = min(metrics.horizontalAdvance(text) + 16, option.rect.width() - 8)
        height = metrics.height() + 6
        rect = QRect(option.rect.left() + 4, option.rect.top() + 6, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font)
        if status is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BADGE_COLORS[status]))
            painter.drawRoundedRect(rect, height / 2, height / 2)
            painter.setPen(QColor("#ffffff"))
        else:  # not final yet: outlined
            color = QColor(option.palette.color(QPalette.ColorRole.Highlight) if text == RERUN
                           else option.palette.color(QPalette.ColorRole.PlaceholderText))
            painter.setPen(QPen(color, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), height / 2, height / 2)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 10))
        painter.restore()
