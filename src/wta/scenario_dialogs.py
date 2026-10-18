"""Dialogs for entering a scenario and a step."""

from PySide6.QtWidgets import (
    QCheckBox,
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
from wta.db.scenarios import ScenarioInput, SmokePageInput, StepInput
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
        self.steps = _text_box(
            "One step per line, in order. Numbers are added for you, e.g.\n"
            "Open the Orders page\nClick + Order\nSelect the price list 'strawberry export pl 2'", 130)
        self.expected_result = _text_box(
            "What must be true after the last step, e.g. the exchange rate shows 1 JOD = 4.30 AED", 70)
        self.test_data = _text_box("Values to use, e.g. product name, quantity, coupon code")
        hint = QLabel("Claude follows the steps in order and judges Pass or Fail against the expected result.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Steps", self.steps)
        form.addRow("Expected result", self.expected_result)
        form.addRow("Test data", self.test_data)
        form.addRow("", hint)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        if data:
            self.name.setText(data.name)
            self.steps.setPlainText(data.steps)
            self.expected_result.setPlainText(data.expected_result)
            self.test_data.setPlainText(data.test_data)

    def accept(self) -> None:
        data = ScenarioInput(self.name.text(), self.steps.toPlainText(), self.expected_result.toPlainText(),
                             self.test_data.toPlainText())
        try:
            self._result = data.cleaned()
        except ValueError as exc:
            QMessageBox.warning(self, "Check the scenario", str(exc))
            return
        super().accept()

    def data(self) -> ScenarioInput | None:
        return self._result


class SmokePageDialog(QDialog):
    """Describe one page for a smoke test. Claude writes the checklist from this."""

    def __init__(self, parent: QWidget | None = None, data: SmokePageInput | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit smoke-test page" if data else "New smoke-test page")
        self.setMinimumWidth(560)
        self._result: SmokePageInput | None = None

        self.page_name = QLineEdit(placeholderText="e.g. Login page")
        self.url = QLineEdit(placeholderText="https://app.example.test/login")
        self.how_to_reach = _text_box("e.g. Open the URL directly, or: Home > Settings > Billing", 55)
        self.login_needed = QCheckBox("Sign in with the session's test account first")
        self.page_information = _text_box("What the page is for and anything Claude should know", 90)
        self.expected_elements = _text_box("Optional: fields, buttons, links or texts that must be there", 70)

        form = QFormLayout()
        form.addRow("Page name", self.page_name)
        form.addRow("URL", self.url)
        form.addRow("How to reach it", self.how_to_reach)
        form.addRow("Login needed", self.login_needed)
        form.addRow("Page information", self.page_information)
        form.addRow("Expected elements", self.expected_elements)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        if data:
            self.page_name.setText(data.page_name)
            self.url.setText(data.url)
            self.how_to_reach.setPlainText(data.how_to_reach)
            self.login_needed.setChecked(data.login_needed)
            self.page_information.setPlainText(data.page_information)
            self.expected_elements.setPlainText(data.expected_elements)

    def accept(self) -> None:
        data = SmokePageInput(
            page_name=self.page_name.text(),
            url=self.url.text(),
            how_to_reach=self.how_to_reach.toPlainText(),
            login_needed=self.login_needed.isChecked(),
            page_information=self.page_information.toPlainText(),
            expected_elements=self.expected_elements.toPlainText(),
        )
        try:
            self._result = data.cleaned()
        except ValueError as exc:
            QMessageBox.warning(self, "Check the page", str(exc))
            return
        super().accept()

    def data(self) -> SmokePageInput | None:
        return self._result


class StepDialog(QDialog):
    """Claude's side of a step, entered by hand until the runner is built."""

    def __init__(
        self, parent: QWidget | None = None, data: StepInput | None = None, labels: dict = STATUS_LABELS
    ) -> None:
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
        for status, label in labels.items():
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
