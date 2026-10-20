"""What to hand over (spec 8.6): the selected test cases, or every test case of the workbook with a tag; for a
checklist run, a new run (name) or an open run to add to; for "Copy as prompt", the mode."""

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

SMOKE, REGRESSION, PROMPT, SCENARIOS = "smoke", "regression", "prompt", "scenarios"
TITLES = {SMOKE: "Create Smoke run", REGRESSION: "Create Regression run", PROMPT: "Copy as prompt",
          SCENARIOS: "Add as scenarios"}
SELECTED, TAGGED = "selected", "tagged"


class HandOffDialog(QDialog):
    def __init__(self, kind: str, selected_count: int, tags: list[str], default_name: str = "",
                 open_runs: list[tuple[int, str]] = (), tag_counts: dict[str, int] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(TITLES[kind])
        self.tag_counts = tag_counts or {}

        self.selected_radio = QRadioButton(f"The selected test cases ({selected_count})")
        self.selected_radio.setEnabled(selected_count > 0)
        self.tagged_radio = QRadioButton("All test cases in this workbook with the tag")
        self.tag_combo = QComboBox()
        for tag in tags:
            self.tag_combo.addItem(f"{tag} ({self.tag_counts.get(tag, 0)})", tag)
        default_tag = kind if kind in (SMOKE, REGRESSION) else "regression"
        self.tag_combo.setCurrentIndex(max(0, self.tag_combo.findData(default_tag)))
        self.tagged_radio.setEnabled(bool(tags))
        (self.selected_radio if selected_count else self.tagged_radio).setChecked(True)
        scope = QWidget()
        scope_layout = QVBoxLayout(scope)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.addWidget(self.selected_radio)
        scope_layout.addWidget(self.tagged_radio)
        scope_layout.addWidget(self.tag_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Smoke test", SMOKE)
        self.mode_combo.addItem("Regression test", REGRESSION)
        self.new_radio = QRadioButton("Create a new run")
        self.name_edit = QLineEdit(default_name)
        self.existing_radio = QRadioButton("Add to an open run")
        self.run_combo = QComboBox()
        for run_id, label in open_runs:
            self.run_combo.addItem(label, run_id)
        self.existing_radio.setEnabled(bool(open_runs))
        self.new_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.new_radio)
        group.addButton(self.existing_radio)
        run_box = QWidget()
        run_layout = QVBoxLayout(run_box)
        run_layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.new_radio, self.name_edit, self.existing_radio, self.run_combo):
            run_layout.addWidget(widget)

        form = QFormLayout()
        form.addRow("Test cases", scope)
        if kind == PROMPT:
            form.addRow("Checklist for", self.mode_combo)
        if kind in (SMOKE, REGRESSION):
            form.addRow("Run", run_box)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        self.error.hide()
        note = QLabel({SMOKE: "The run is a manual checklist; open it in Smoke Testing, Checklists tab.",
                       REGRESSION: "The run is a manual checklist; open it in Regression Testing, Checklists tab.",
                       PROMPT: "The prompt is copied to the clipboard; paste it into Claude for a printable checklist.",
                       SCENARIOS: "The test cases become scenarios in the library, for Claude Code to run in the "
                                  "browser (Scenarios tab, Add from library)."}[kind])
        note.setWordWrap(True)
        note.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(TITLES[kind])
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)
        for widget in (self.selected_radio, self.tagged_radio, self.new_radio, self.existing_radio):
            widget.toggled.connect(self._update)
        self._update()

    def _update(self) -> None:
        self.tag_combo.setEnabled(self.tagged_radio.isChecked())
        self.name_edit.setEnabled(self.new_radio.isChecked())
        self.run_combo.setEnabled(self.existing_radio.isChecked())

    def scope(self) -> str:
        return SELECTED if self.selected_radio.isChecked() else TAGGED

    def tag(self) -> str:
        return self.tag_combo.currentData() or ""

    def mode(self) -> str:
        return self.kind if self.kind in (SMOKE, REGRESSION) else self.mode_combo.currentData()

    def existing_run(self) -> int | None:
        return self.run_combo.currentData() if self.existing_radio.isChecked() else None

    def run_name(self) -> str:
        return self.name_edit.text().strip()

    def _accept(self) -> None:
        if self.kind in (SMOKE, REGRESSION) and self.existing_run() is None and not self.run_name():
            self.error.setText("Enter a name for the run.")
            self.error.show()
            return
        if self.scope() == TAGGED and not self.tag_counts.get(self.tag()):
            self.error.setText("No test case in this workbook has that tag.")
            self.error.show()
            return
        self.accept()
