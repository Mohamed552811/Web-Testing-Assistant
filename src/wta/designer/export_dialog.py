"""Choose what to export (spec 8.1 (9)): this backlog or every backlog of the workbook with test cases,
the format, an optional tag filter, and for JSON one file per backlog or one array."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from wta.designer.export import FORMATS

THIS_BACKLOG, WORKBOOK = "backlog", "workbook"


class ExportDialog(QDialog):
    def __init__(self, backlog_label: str | None, workbook_count: int, tags: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export test cases")
        self.this_radio = QRadioButton(f"This backlog ({backlog_label})" if backlog_label else "This backlog")
        self.this_radio.setEnabled(backlog_label is not None)
        self.all_radio = QRadioButton(f"All backlogs of this workbook with test cases ({workbook_count})")
        self.all_radio.setEnabled(workbook_count > 0)
        (self.this_radio if backlog_label is not None else self.all_radio).setChecked(True)
        scope = QWidget()
        scope_layout = QVBoxLayout(scope)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.addWidget(self.this_radio)
        scope_layout.addWidget(self.all_radio)
        self.format_combo = QComboBox()
        for key, label in FORMATS.items():
            self.format_combo.addItem(label, key)
        self.format_combo.setCurrentIndex(self.format_combo.findData("xlsx"))
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("All test cases", "")
        for tag in tags:
            self.tag_combo.addItem(f"Only tagged “{tag}”", tag)
        self.per_backlog = QCheckBox("One JSON file per backlog (in a folder)")
        form = QFormLayout()
        form.addRow("Export", scope)
        form.addRow("Format", self.format_combo)
        form.addRow("Test cases", self.tag_combo)
        form.addRow(self.per_backlog)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.format_combo.currentIndexChanged.connect(self._update)
        self.this_radio.toggled.connect(self._update)
        self._update()

    def _update(self) -> None:
        self.per_backlog.setEnabled(self.fmt() == "json" and self.scope() == WORKBOOK)
        if not self.per_backlog.isEnabled():
            self.per_backlog.setChecked(False)

    def scope(self) -> str:
        return THIS_BACKLOG if self.this_radio.isChecked() else WORKBOOK

    def fmt(self) -> str:
        return self.format_combo.currentData()

    def tag(self) -> str:
        return self.tag_combo.currentData()
