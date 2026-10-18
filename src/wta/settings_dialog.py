"""Settings screen. Values are saved to settings.json and remembered between sessions."""

from PySide6.QtWidgets import (
    QCheckBox,
    QLineEdit,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from wta.settings import EXPORT_FORMATS, SCREENSHOT_TIMINGS, Settings
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
        note = QLabel("Screenshot timing and risky-action confirmation apply from the next run.")
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

    def settings(self) -> Settings:
        return Settings(
            screenshot_timing=self.screenshot_timing.currentData(),
            export_format=self.export_format.currentData(),
            confirm_risky_actions=self.confirm_risky.isChecked(),
            tester_name=self.tester_name.text(),
            theme=self.theme.currentData(),
        ).cleaned()
