"""Manual mapping override (spec 3.7 step 7): which columns and rows hold the backlogs of a sheet,
and the workbook's product context (what the app under test is)."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from wta.db.workbooks import SheetView
from wta.designer.layout import BLOCK, TABLE, Mapping
from wta.designer.xlsx_reader import col_letters, col_number

MAX_ROW = 1_048_576


def _letters(col: int | None) -> str:
    return col_letters(col) if col else ""


def _columns(text: str) -> list[int]:
    return [col_number(part) for part in text.replace(";", ",").split(",") if part.strip()]


class MappingDialog(QDialog):
    """Pick a sheet and set its mapping. result_mapping() is None when "Use automatic detection" was chosen."""

    def __init__(self, sheets: list[SheetView], sheet_id: int | None = None, parent=None,
                 product_context: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Backlog mapping")
        self.sheets = sheets
        self._reset = False
        self.sheet_combo = QComboBox()
        for sheet in sheets:
            self.sheet_combo.addItem(sheet.name, sheet.id)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Block: a number row, then description rows", BLOCK)
        self.layout_combo.addItem("Table: a header row, one backlog per row", TABLE)
        self.number_edit = QLineEdit(placeholderText="e.g. C")
        self.title_edit = QLineEdit(placeholderText="empty = first text right of the number")
        self.description_edit = QLineEdit(placeholderText="e.g. D, E (empty = every text column)")
        self.status_edit = QLineEdit(placeholderText="empty = none (the status is never used)")
        self.header_spin = QSpinBox(minimum=0, maximum=MAX_ROW, specialValueText="none")
        self.first_spin = QSpinBox(minimum=0, maximum=MAX_ROW, specialValueText="start of sheet")
        self.last_spin = QSpinBox(minimum=0, maximum=MAX_ROW, specialValueText="end of sheet")
        self.context_edit = QPlainTextEdit(product_context)
        self.context_edit.setFixedHeight(64)
        self.context_edit.setPlaceholderText("What the app under test is, e.g. an ERP 'Print' app (print designs, "
                                             "print settings, templates, …)")
        self.context_edit.setToolTip("Fills \"the product backlogs of …\" in the prompt for every backlog of this "
                                     "workbook. New versions of the workbook start with it.")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        self.error.hide()

        form = QFormLayout()
        form.addRow("Sheet", self.sheet_combo)
        form.addRow("Layout", self.layout_combo)
        form.addRow("Backlog number column", self.number_edit)
        form.addRow("Title column", self.title_edit)
        form.addRow("Description columns", self.description_edit)
        form.addRow("Status column", self.status_edit)
        form.addRow("Header row (table)", self.header_spin)
        form.addRow("First row", self.first_spin)
        form.addRow("Last row", self.last_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.reset_button = QPushButton("Use automatic detection")
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._accept_reset)

        intro = QLabel("Columns are letters as in Excel. Formula cells are never read as backlog data.")
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        context_box = QGroupBox("Product context (whole workbook)")
        context_layout = QVBoxLayout(context_box)
        context_layout.addWidget(self.context_edit)
        sheet_box = QGroupBox("Backlogs on the sheet")
        sheet_layout = QVBoxLayout(sheet_box)
        sheet_layout.addWidget(intro)
        sheet_layout.addLayout(form)
        layout = QVBoxLayout(self)
        layout.addWidget(context_box)
        layout.addWidget(sheet_box)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        self.sheet_combo.currentIndexChanged.connect(self._load)
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        if sheet_id is not None and self.sheet_combo.findData(sheet_id) >= 0:
            self.sheet_combo.setCurrentIndex(self.sheet_combo.findData(sheet_id))
        self._load()

    def selected_sheet_id(self) -> int:
        return self.sheet_combo.currentData()

    def _load(self) -> None:
        sheet = next(s for s in self.sheets if s.id == self.selected_sheet_id())
        mapping = sheet.mapping
        self.layout_combo.setCurrentIndex(1 if mapping.layout == TABLE else 0)
        self.number_edit.setText(_letters(mapping.number_col))
        self.title_edit.setText(_letters(mapping.title_col))
        self.description_edit.setText(", ".join(col_letters(c) for c in mapping.description_cols or []))
        self.status_edit.setText(_letters(mapping.status_col))
        self.header_spin.setValue(mapping.header_row or 0)
        self.first_spin.setValue(mapping.first_row or 0)
        self.last_spin.setValue(mapping.last_row or 0)
        self._layout_changed()

    def _layout_changed(self) -> None:
        self.header_spin.setEnabled(self.layout_combo.currentData() == TABLE)

    def mapping(self) -> Mapping:
        """The mapping in the form; raises ValueError with a message for the user."""
        try:
            number = col_number(self.number_edit.text()) if self.number_edit.text().strip() else 0
            title = col_number(self.title_edit.text()) if self.title_edit.text().strip() else None
            status = col_number(self.status_edit.text()) if self.status_edit.text().strip() else None
            description = _columns(self.description_edit.text()) or None
        except ValueError:
            raise ValueError("Columns must be letters, for example C or D, E.") from None
        if not number:
            raise ValueError("Enter the backlog number column.")
        table = self.layout_combo.currentData() == TABLE
        if table and not self.header_spin.value():
            raise ValueError("A table layout needs its header row.")
        first, last = self.first_spin.value() or None, self.last_spin.value() or None
        if first and last and last < first:
            raise ValueError("The last row is before the first row.")
        return Mapping(TABLE if table else BLOCK, number, title, description, status,
                       self.header_spin.value() if table else None, first, last)

    def product_context(self) -> str:
        return self.context_edit.toPlainText().strip()

    def result_mapping(self) -> Mapping | None:
        return None if self._reset else self.mapping()

    def _accept(self) -> None:
        try:
            self.mapping()
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self._reset = False
        self.accept()

    def _accept_reset(self) -> None:
        self._reset = True
        self.accept()
