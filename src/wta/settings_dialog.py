"""Settings screen. Values are saved to settings.json and remembered between sessions."""

from PySide6.QtWidgets import (
    QCheckBox,
    QLineEdit,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wta.settings import EXPORT_FORMATS, MODEL_CHOICES, PAUSE_OFF, PAUSE_RANGE, SCREENSHOT_TIMINGS, Settings
from wta.theme import THEMES


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        self.screenshot_timing = QComboBox()
        for key, label in SCREENSHOT_TIMINGS.items():
            self.screenshot_timing.addItem(label, key)
        self.export_format = QComboBox()
        for key, label in EXPORT_FORMATS.items():
            self.export_format.addItem(label, key)
        self.confirm_risky = QCheckBox("Pause before risky actions and ask me first")
        self.confirm_risky.setToolTip(
            "Deleting, sending, paying or other irreversible actions. Claude stops and the app asks you."
        )

        self.pause_at = QSpinBox()
        self.pause_at.setRange(PAUSE_RANGE[0] - 1, PAUSE_RANGE[1])
        self.pause_at.setSpecialValueText("Never")  # shown at the lowest value, saved as "off"
        self.pause_at.setSuffix(" % of the 5-hour window")
        self.pause_at.setToolTip("Before each scenario, the batch pauses and asks you once your 5-hour plan usage "
                                 "has reached this level. A scenario that is already running is not interrupted.")
        self.smoke_accessibility = QCheckBox("Include accessibility checks")
        self.smoke_accessibility.setToolTip("Claude checks labels, button and link names and image alt text; "
                                            "the app measures text contrast on the page after the run.")
        self.scenario_model = self._model_combo()
        self.smoke_model = self._model_combo()

        self.tester_name = QLineEdit(placeholderText="e.g. Staff QA")
        self.theme = QComboBox()
        for key, label in THEMES.items():
            self.theme.addItem(label, key)
        form = QFormLayout()
        form.addRow("Theme", self.theme)
        form.addRow("Tester name", self.tester_name)
        form.addRow("Screenshot timing", self.screenshot_timing)
        form.addRow("Export format", self.export_format)
        form.addRow("Risky actions", self.confirm_risky)
        form.addRow("Pause batch at", self.pause_at)
        form.addRow("Model for scenarios", self.scenario_model)
        form.addRow("Model for smoke tests", self.smoke_model)
        form.addRow("Smoke tests", self.smoke_accessibility)
        note = QLabel("Screenshot timing, risky-action confirmation and models apply from the next run. "
                      "You can also type a full model name.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.screenshot_timing.setCurrentIndex(self.screenshot_timing.findData(settings.screenshot_timing))
        self.export_format.setCurrentIndex(self.export_format.findData(settings.export_format))
        self.confirm_risky.setChecked(settings.confirm_risky_actions)
        self.tester_name.setText(settings.tester_name)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        self.pause_at.setValue(self.pause_at.minimum() if settings.pause_at_percent == PAUSE_OFF
                               else settings.pause_at_percent)
        self._set_model(self.scenario_model, settings.scenario_model)
        self._set_model(self.smoke_model, settings.smoke_model)
        self.smoke_accessibility.setChecked(settings.smoke_accessibility)

    @staticmethod
    def _model_combo() -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)  # a full model name can be typed too
        for key, label in MODEL_CHOICES.items():
            combo.addItem(label, key)
        return combo

    @staticmethod
    def _set_model(combo: QComboBox, model: str) -> None:
        index = combo.findData(model)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(model)

    @staticmethod
    def _model(combo: QComboBox) -> str:
        index = combo.findText(combo.currentText())
        return combo.itemData(index) if index >= 0 else combo.currentText().strip()

    def settings(self) -> Settings:
        return Settings(
            screenshot_timing=self.screenshot_timing.currentData(),
            export_format=self.export_format.currentData(),
            confirm_risky_actions=self.confirm_risky.isChecked(),
            tester_name=self.tester_name.text(),
            theme=self.theme.currentData(),
            pause_at_percent=PAUSE_OFF if self.pause_at.value() == self.pause_at.minimum() else self.pause_at.value(),
            scenario_model=self._model(self.scenario_model),
            smoke_model=self._model(self.smoke_model),
            smoke_accessibility=self.smoke_accessibility.isChecked(),
        ).cleaned()
