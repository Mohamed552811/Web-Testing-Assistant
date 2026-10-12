"""Dialogs for entering a scenario and a step."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from wta.db.models import StepStatus
from wta.db.scenarios import ScenarioInput, StepInput
from wta.step_table import STATUS_LABELS


def _text_box(placeholder: str = "", height: int = 70) -> QPlainTextEdit:
    box = QPlainTextEdit(placeholderText=placeholder)
    box.setFixedHeight(height)
    return box


def _buttons(dialog: QDialog) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


class ScenarioDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, data: ScenarioInput | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit scenario" if data else "New scenario")
        self.setMinimumWidth(520)
        self._result: ScenarioInput | None = None

        self.name = QLineEdit(placeholderText="e.g. Add a product to the cart and check out")
        self.goal = _text_box("What should happen, in plain language", 110)
        self.test_data = _text_box("Values to use, e.g. product name, quantity, coupon code")

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Goal", self.goal)
        form.addRow("Test data", self.test_data)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        if data:
            self.name.setText(data.name)
            self.goal.setPlainText(data.goal)
            self.test_data.setPlainText(data.test_data)

    def accept(self) -> None:
        data = ScenarioInput(self.name.text(), self.goal.toPlainText(), self.test_data.toPlainText())
        try:
            self._result = data.cleaned()
        except ValueError as exc:
            QMessageBox.warning(self, "Check the scenario", str(exc))
            return
        super().accept()

    def data(self) -> ScenarioInput | None:
        return self._result


class StepDialog(QDialog):
    """Claude's side of a step, entered by hand until the runner is built."""

    def __init__(self, parent: QWidget | None = None, data: StepInput | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit step" if data else "New step")
        self.setMinimumWidth(560)
        self._result: StepInput | None = None

        self.screen_name = QLineEdit()
        self.element = QLineEdit()
        self.action = _text_box("What was done, or what was typed", 55)
        self.expected = _text_box(height=55)
        self.actual = _text_box(height=55)
        self.status = QComboBox()
        for status, label in STATUS_LABELS.items():
            self.status.addItem(label, status)
        self.reasoning = _text_box("Why this status was chosen", 80)

        note = QLabel("Claude's result. Entered by hand for now; the runner will fill it in later.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("Screen", self.screen_name)
        form.addRow("Field / element", self.element)
        form.addRow("Action / input", self.action)
        form.addRow("Expected result", self.expected)
        form.addRow("Actual result", self.actual)
        form.addRow("Claude status", self.status)
        form.addRow("Claude reasoning", self.reasoning)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        if data:
            self.screen_name.setText(data.screen_name)
            self.element.setText(data.element)
            self.action.setPlainText(data.action)
            self.expected.setPlainText(data.expected_result)
            self.actual.setPlainText(data.actual_result)
            self.status.setCurrentIndex(self.status.findData(data.status))
            self.reasoning.setPlainText(data.reasoning)

    def accept(self) -> None:
        data = StepInput(
            screen_name=self.screen_name.text(),
            element=self.element.text(),
            action=self.action.toPlainText(),
            expected_result=self.expected.toPlainText(),
            actual_result=self.actual.toPlainText(),
            status=StepStatus(self.status.currentData()),
            reasoning=self.reasoning.toPlainText(),
        )
        try:
            self._result = data.cleaned()
        except ValueError as exc:
            QMessageBox.warning(self, "Check the step", str(exc))
            return
        super().accept()

    def data(self) -> StepInput | None:
        return self._result
