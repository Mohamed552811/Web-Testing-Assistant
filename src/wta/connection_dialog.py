"""Shows how Claude Code can reach the embedded browser, with a command to run by hand."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wta import cdp, claude_launcher

DEFAULT_PROMPT = (
    "Use only the Playwright browser tools. Do not navigate away from the open page. "
    "Take an accessibility snapshot, take a screenshot named manual-check.png, "
    "then describe the page in two sentences."
)


class ConnectionDialog(QDialog):
    def __init__(self, port: int, mcp_config: Path, output_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Claude connection")
        self.setMinimumWidth(720)

        info = cdp.endpoint_info(port)
        status = (
            f"Reachable — {info.get('User-Agent', '')}"
            if info
            else "Not reachable. Restart the app; the port may have been taken."
        )
        claude_launcher.write_mcp_config(mcp_config, port, output_dir)
        command = claude_launcher.format_for_shell(
            claude_launcher.manual_command(mcp_config, DEFAULT_PROMPT), cwd=output_dir
        )

        form = QFormLayout()
        form.addRow("CDP endpoint", QLabel(cdp.endpoint_url(port)))
        status_label = QLabel(status)
        status_label.setWordWrap(True)
        form.addRow("Status", status_label)
        form.addRow("MCP config", QLabel(str(mcp_config)))
        form.addRow("Screenshots go to", QLabel(str(output_dir)))
        form.addRow("Playwright MCP", QLabel(claude_launcher.PLAYWRIGHT_MCP_PACKAGE))
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget()
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.command = QPlainTextEdit(command, readOnly=True)
        self.command.setFixedHeight(110)
        copy = QPushButton("Copy command")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.command.toPlainText()))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Run this in a terminal to check Claude Code can drive the visible browser:"))
        layout.addWidget(self.command)
        layout.addWidget(copy)
        layout.addWidget(buttons)
