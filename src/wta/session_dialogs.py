"""Dialogs for creating, editing and opening test sessions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QHeaderView,
)

from wta.db import store
from wta.db.models import TestSession


class SessionDialog(QDialog):
    """Form for a session's details. `details()` returns validated values after OK."""

    def __init__(self, parent: QWidget | None = None, details: store.SessionDetails | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session details" if details else "New session")
        self.setMinimumWidth(520)
        self._result: store.SessionDetails | None = None

        self.name = QLineEdit()
        self.target_url = QLineEdit(placeholderText="https://app.example.com/login")
        self.allowed_domains = QPlainTextEdit(
            placeholderText="One per line or comma separated. The target URL's host is always included."
        )
        self.allowed_domains.setFixedHeight(80)
        self.viewport_width = QSpinBox(minimum=320, maximum=7680, value=1280, suffix=" px")
        self.viewport_height = QSpinBox(minimum=240, maximum=4320, value=800, suffix=" px")
        self.email = QLineEdit(placeholderText="test account email")
        self.password = QLineEdit(placeholderText="test account password", echoMode=QLineEdit.EchoMode.Password)

        viewport = QHBoxLayout()
        viewport.addWidget(self.viewport_width)
        viewport.addWidget(QLabel("×"))
        viewport.addWidget(self.viewport_height)
        viewport.addStretch(1)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Target URL", self.target_url)
        form.addRow("Allowed domains", self.allowed_domains)
        form.addRow("Viewport", viewport)
        form.addRow("Test account email", self.email)
        form.addRow("Test account password", self.password)
        self.application = QLineEdit(placeholderText="e.g. Amnivo ERP - Sales Order Module")
        self.test_scope = QPlainTextEdit(placeholderText="What this session covers, one item per line")
        self.test_scope.setFixedHeight(70)
        form.addRow("Application", self.application)
        form.addRow("Test scope", self.test_scope)
        note = QLabel("Test environment accounts only. Stored in the local database on this machine.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow("", note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if details:
            self.name.setText(details.name)
            self.target_url.setText(details.target_url)
            self.allowed_domains.setPlainText("\n".join(details.allowed_domains))
            self.viewport_width.setValue(details.viewport_width)
            self.viewport_height.setValue(details.viewport_height)
            self.email.setText(details.test_account_email)
            self.password.setText(details.test_account_password)
            self.application.setText(details.application)
            self.test_scope.setPlainText(details.test_scope)

    def accept(self) -> None:
        details = store.SessionDetails(
            name=self.name.text(),
            target_url=self.target_url.text(),
            allowed_domains=[self.allowed_domains.toPlainText()],
            viewport_width=self.viewport_width.value(),
            viewport_height=self.viewport_height.value(),
            test_account_email=self.email.text(),
            test_account_password=self.password.text(),
            application=self.application.text(),
            test_scope=self.test_scope.toPlainText(),
        )
        try:
            self._result = store.validate(details)
        except ValueError as exc:
            QMessageBox.warning(self, "Check the session details", str(exc))
            return
        super().accept()

    def details(self) -> store.SessionDetails | None:
        return self._result


class OpenSessionDialog(QDialog):
    """Pick a saved session. `selected_id()` returns its id after OK."""

    def __init__(self, sessions: list[TestSession], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open session")
        self.resize(700, 400)

        self.table = QTableWidget(len(sessions), 3)
        self.table.setHorizontalHeaderLabels(["Name", "Target URL", "Created (UTC)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, session in enumerate(sessions):
            name = QTableWidgetItem(session.name)
            name.setData(Qt.ItemDataRole.UserRole, session.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(session.target_url))
            self.table.setItem(row, 2, QTableWidgetItem(session.created_at.strftime("%Y-%m-%d %H:%M")))
        self.table.resizeColumnToContents(0)
        if sessions:
            self.table.selectRow(0)
        self.table.doubleClicked.connect(self.accept)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(bool(sessions))

        layout = QVBoxLayout(self)
        if not sessions:
            layout.addWidget(QLabel("No saved sessions yet."))
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def selected_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None
