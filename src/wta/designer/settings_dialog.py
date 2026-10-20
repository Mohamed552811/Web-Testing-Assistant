"""Test Case Designer settings (spec 8.3): generation rules, tags, output presets (5.4) and model.

The product context is per workbook (set with its mapping), not here."""

import copy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wta.designer.settings import (
    CUSTOM_PRESET_NAME,
    LATEST_OPUS,
    MAX_PADDING,
    MERGE_STEPS_RANGE,
    OPTIONAL_FIELDS,
    OUTPUT_LANGUAGE,
    PINNED,
    PRIORITY_SCALES,
    STEPS_STYLES,
    UI_POLICIES,
    DesignerSettings,
    OutputPreset,
)


def example_id(preset: OutputPreset) -> str:
    return f"{preset.id_prefix}-{'3'.zfill(preset.backlog_pad or 1)}-{'2'.zfill(preset.seq_pad or 1)}"


class DesignerSettingsDialog(QDialog):
    def __init__(self, settings: DesignerSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test Case Design settings")
        self._settings = settings.copy()
        self._preset_index = -1

        # Generation rules (spec 4.6)
        self.merge_check = QCheckBox("Merge same-flow test cases")
        self.merge_steps_spin = QSpinBox(minimum=MERGE_STEPS_RANGE[0], maximum=MERGE_STEPS_RANGE[1], suffix=" steps")
        self.merge_steps_spin.setToolTip("Soft limit for a merged test case; longer flows are split")
        self.heavy_check = QCheckBox("Avoid performance and heavy (data-volume) test cases")
        self.ui_combo = QComboBox()
        for key, label in UI_POLICIES.items():
            self.ui_combo.addItem(label, key)
        merge_row = QHBoxLayout()
        merge_row.addWidget(self.merge_check)
        merge_row.addWidget(QLabel("about"))
        merge_row.addWidget(self.merge_steps_spin)
        merge_row.addStretch(1)
        rules = QFormLayout()
        rules.addRow(merge_row)
        rules.addRow(self.heavy_check)
        rules.addRow("UI testing policy", self.ui_combo)
        rules_box = QGroupBox("Generation rules")
        rules_box.setLayout(rules)

        # Tags (spec 4.4)
        self.tags_check = QCheckBox("Suggest tags (smoke on High-priority happy paths, regression on every case)")
        self.vocabulary_edit = QLineEdit(placeholderText="smoke, regression, permissions")
        tags = QFormLayout()
        tags.addRow(self.tags_check)
        tags.addRow("Tag vocabulary", self.vocabulary_edit)
        tags_box = QGroupBox("Tags")
        tags_box.setLayout(tags)

        # Output (spec 5.4)
        self.language_combo = QComboBox()
        self.language_combo.addItem(OUTPUT_LANGUAGE)
        self.language_combo.setEnabled(False)  # until more languages are supported
        self.language_combo.setToolTip("Test cases are written in English; more languages will be added later")
        self.preset_combo = QComboBox()
        self.save_preset_button = QPushButton("Save as new preset…")
        self.delete_preset_button = QPushButton("Delete preset")
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_preset_button)
        preset_row.addWidget(self.delete_preset_button)
        self.field_checks = {name: QCheckBox(name.replace("_", " ")) for name in OPTIONAL_FIELDS}
        fields_row = QHBoxLayout()
        for check in self.field_checks.values():
            fields_row.addWidget(check)
        fields_row.addStretch(1)
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setMaxLength(10)
        self.backlog_pad_spin = QSpinBox(minimum=0, maximum=MAX_PADDING, specialValueText="no padding")
        self.seq_pad_spin = QSpinBox(minimum=0, maximum=MAX_PADDING, specialValueText="no padding")
        self.id_example = QLabel()
        self.id_example.setProperty("muted", True)
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("prefix"))
        id_row.addWidget(self.prefix_edit)
        id_row.addWidget(QLabel("backlog digits"))
        id_row.addWidget(self.backlog_pad_spin)
        id_row.addWidget(QLabel("sequence digits"))
        id_row.addWidget(self.seq_pad_spin)
        id_row.addWidget(self.id_example)
        id_row.addStretch(1)
        self.priority_combo = QComboBox()
        for scale in PRIORITY_SCALES:
            self.priority_combo.addItem(" / ".join(scale), scale)
        self.steps_combo = QComboBox()
        for key, label in STEPS_STYLES.items():
            self.steps_combo.addItem(label, key)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(40)
        self.preset_fields = QWidget()
        preset_form = QFormLayout(self.preset_fields)
        preset_form.setContentsMargins(0, 0, 0, 0)
        preset_form.addRow("Optional fields", fields_row)
        preset_form.addRow("ID pattern", id_row)
        preset_form.addRow("Priority labels", self.priority_combo)
        preset_form.addRow("Steps style", self.steps_combo)
        preset_form.addRow("Title prefix", self.title_edit)
        self.builtin_note = QLabel("The built-in Default preset cannot be changed. Choose Custom to set your own "
                                   "ID pattern, priority labels, steps style and title prefix.")
        self.builtin_note.setWordWrap(True)
        self.builtin_note.setProperty("muted", True)
        output = QFormLayout()
        output.addRow("Output language", self.language_combo)
        output.addRow("Output preset", preset_row)
        output.addRow(self.builtin_note)
        output.addRow(self.preset_fields)
        output_box = QGroupBox("Output format")
        output_box.setLayout(output)

        # Model (spec 7.3, decision 1: through Claude Code)
        self.latest_radio = QRadioButton("Latest Opus available to Claude Code (claude --model opus)")
        self.pinned_radio = QRadioButton("Pinned model id")
        self.pinned_edit = QLineEdit(placeholderText="claude-opus-5-5")
        pinned_row = QHBoxLayout()
        pinned_row.addWidget(self.pinned_radio)
        pinned_row.addWidget(self.pinned_edit, 1)
        model = QVBoxLayout()
        model.addWidget(self.latest_radio)
        model.addLayout(pinned_row)
        model_box = QGroupBox("Model")
        model_box.setLayout(model)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c0392b;")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for box in (rules_box, tags_box, output_box, model_box):
            layout.addWidget(box)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.setMinimumWidth(720)

        self.merge_check.toggled.connect(self.merge_steps_spin.setEnabled)
        self.pinned_radio.toggled.connect(self.pinned_edit.setEnabled)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.save_preset_button.clicked.connect(self._save_as_new_preset)
        self.delete_preset_button.clicked.connect(self._delete_preset)
        for widget in (self.prefix_edit, self.title_edit):
            widget.textChanged.connect(self._update_example)
        for spin in (self.backlog_pad_spin, self.seq_pad_spin):
            spin.valueChanged.connect(self._update_example)
        self._load()

    # --- form <-> settings ---------------------------------------------------------------------

    def _load(self) -> None:
        s = self._settings
        self.merge_check.setChecked(s.merge_same_flow)
        self.merge_steps_spin.setValue(s.merge_max_steps)
        self.merge_steps_spin.setEnabled(s.merge_same_flow)
        self.heavy_check.setChecked(s.avoid_heavy_and_performance)
        self.ui_combo.setCurrentIndex(self.ui_combo.findData(s.ui_testing_policy))
        self.tags_check.setChecked(s.tag_suggestions)
        self.vocabulary_edit.setText(", ".join(s.tag_vocabulary))
        self.latest_radio.setChecked(s.model_mode == LATEST_OPUS)
        self.pinned_radio.setChecked(s.model_mode == PINNED)
        self.pinned_edit.setText(s.pinned_model_id or "")
        self.pinned_edit.setEnabled(s.model_mode == PINNED)
        self._fill_presets(s.output_preset)

    def _fill_presets(self, select: str) -> None:
        self._preset_index = -1
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._settings.output_presets:
            suffix = " (built-in)" if preset.builtin else " (your own format)" if preset.name == CUSTOM_PRESET_NAME else ""
            self.preset_combo.addItem(preset.name + suffix, preset.name)
        index = max(0, self.preset_combo.findData(select))
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        self._show_preset(index)

    def _show_preset(self, index: int) -> None:
        preset = self._settings.output_presets[index]
        self._preset_index = index
        for name, check in self.field_checks.items():
            check.setChecked(preset.includes(name))
        self.prefix_edit.setText(preset.id_prefix)
        self.backlog_pad_spin.setValue(preset.backlog_pad)
        self.seq_pad_spin.setValue(preset.seq_pad)
        self.priority_combo.setCurrentIndex(PRIORITY_SCALES.index(tuple(preset.priority_labels)))
        self.steps_combo.setCurrentIndex(self.steps_combo.findData(preset.steps_style))
        self.title_edit.setText(preset.title_prefix)
        self.preset_fields.setEnabled(not preset.builtin)
        self.builtin_note.setVisible(preset.builtin)
        self.delete_preset_button.setEnabled(not preset.builtin and preset.name != CUSTOM_PRESET_NAME)
        self._update_example()

    def _form_preset(self, name: str, builtin: bool = False) -> OutputPreset:
        """The preset fields as entered (cleaned like stored values)."""
        return OutputPreset.from_json(OutputPreset(
            name=name, builtin=builtin,
            optional_fields={n: c.isChecked() for n, c in self.field_checks.items()},
            id_prefix=self.prefix_edit.text().strip(), backlog_pad=self.backlog_pad_spin.value(),
            seq_pad=self.seq_pad_spin.value(), priority_labels=self.priority_combo.currentData(),
            steps_style=self.steps_combo.currentData(), title_prefix=self.title_edit.text().strip(),
        ).to_json())

    def _store_shown_preset(self) -> None:
        if 0 <= self._preset_index < len(self._settings.output_presets):
            shown = self._settings.output_presets[self._preset_index]
            if not shown.builtin:
                self._settings.output_presets[self._preset_index] = self._form_preset(shown.name)

    def _preset_changed(self, index: int) -> None:
        self._store_shown_preset()
        if index >= 0:
            self._show_preset(index)

    def _update_example(self) -> None:
        preset = self._form_preset("example")
        self.id_example.setText(f"e.g. {example_id(preset)}")

    def _save_as_new_preset(self) -> None:
        self._store_shown_preset()
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name (for example Jira import):")
        name = name.strip()
        if not ok or not name:
            return
        if any(p.name == name for p in self._settings.output_presets):
            QMessageBox.warning(self, "Save preset", f"A preset named “{name}” already exists.")
            return
        self._settings.output_presets.append(self._form_preset(name[:60]))
        self._fill_presets(name[:60])

    def _delete_preset(self) -> None:
        index = self.preset_combo.currentIndex()
        if self._settings.output_presets[index].builtin or self._settings.output_presets[index].name == CUSTOM_PRESET_NAME:
            return
        del self._settings.output_presets[index]
        self._fill_presets(self._settings.output_presets[0].name)

    def settings(self) -> DesignerSettings:
        self._store_shown_preset()
        s = copy.deepcopy(self._settings)
        s.merge_same_flow = self.merge_check.isChecked()
        s.merge_max_steps = self.merge_steps_spin.value()
        s.avoid_heavy_and_performance = self.heavy_check.isChecked()
        s.ui_testing_policy = self.ui_combo.currentData()
        s.tag_suggestions = self.tags_check.isChecked()
        s.tag_vocabulary = list(dict.fromkeys(t.strip().lower() for t in self.vocabulary_edit.text().split(",")
                                              if t.strip()))
        s.output_preset = self.preset_combo.currentData()
        s.model_mode = PINNED if self.pinned_radio.isChecked() else LATEST_OPUS
        s.pinned_model_id = self.pinned_edit.text().strip() or None
        return s

    def _accept(self) -> None:
        problems = self.settings().problems()
        if problems:
            self.error.setText("\n".join(problems))
            self.error.show()
            return
        self.accept()
