"""Dialogs for creating, editing and opening test sessions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QPushButton,
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


class AccountDialog(QDialog):
    """One other test account: role, email and password."""

    def __init__(self, parent: QWidget | None = None, account: store.AccountDetails | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test account" if account else "Add test account")
        self.setMinimumWidth(420)
        self.account_id = account.id if account else None
        self.role = QLineEdit(placeholderText="e.g. Sales manager, Team B, Read-only")
        self.email = QLineEdit(placeholderText="test account email")
        self.password = QLineEdit(placeholderText="test account password", echoMode=QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Role", self.role)
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if account:
            self.role.setText(account.role)
            self.email.setText(account.email)
            self.password.setText(account.password)

    def accept(self) -> None:
        if not self.role.text().strip() or not self.email.text().strip():
            QMessageBox.warning(self, "Test account", "Enter a role and an email.")
            return
        super().accept()

    def account(self) -> store.AccountDetails:
        return store.AccountDetails(self.role.text().strip(), self.email.text().strip(), self.password.text(),
                                    self.account_id)


class AccountsEditor(QWidget):
    """The session's other test accounts. Passwords are never shown."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.accounts: list[store.AccountDetails] = []
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Role", "Email", "Password"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(110)
        self.add_button = QPushButton("Add…")
        self.edit_button = QPushButton("Edit…")
        self.remove_button = QPushButton("Remove")
        buttons = QVBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit)
        self.remove_button.clicked.connect(self.remove)
        self.table.doubleClicked.connect(lambda _index: self.edit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._fill()

    def set_accounts(self, accounts: list[store.AccountDetails]) -> None:
        self.accounts = list(accounts)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(len(self.accounts))
        for row, account in enumerate(self.accounts):
            for column, text in enumerate((account.role, account.email, "••••••" if account.password else "")):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.add_button.setEnabled(len(self.accounts) < store.MAX_OTHER_ACCOUNTS)
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self.table.currentRow() >= 0 and bool(self.table.selectedItems())
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)

    def add(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.accounts.append(dialog.account())
            self._fill()

    def edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        dialog = AccountDialog(self, self.accounts[row])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.accounts[row] = dialog.account()
            self._fill()

    def remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            del self.accounts[row]
            self._fill()


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
        self.accounts = AccountsEditor()
        self.accounts.setToolTip("More test accounts, for example other roles or teams. "
                                 "Each scenario can run as one of them.")
        form.addRow("Other accounts", self.accounts)
        self.fixtures_dir = QLineEdit(placeholderText="Optional: a folder of files Claude may upload")
        self.fixtures_dir.setToolTip("Files in this folder are copied into each run folder, and Claude may "
                                     "upload only those. Without a folder, uploads are not allowed.")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_fixtures)
        fixtures = QHBoxLayout()
        fixtures.addWidget(self.fixtures_dir, 1)
        fixtures.addWidget(browse)
        form.addRow("Upload files", fixtures)
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
            self.accounts.set_accounts(details.other_accounts)
            self.fixtures_dir.setText(details.fixtures_dir)

    def _choose_fixtures(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder of files Claude may upload", self.fixtures_dir.text())
        if folder:
            self.fixtures_dir.setText(folder)

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
            other_accounts=self.accounts.accounts,
            fixtures_dir=self.fixtures_dir.text(),
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
