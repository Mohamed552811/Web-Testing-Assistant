"""First-start check: is Node.js installed, and is Claude Code installed and signed in? Explains how to fix it."""

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

from wta import claude_launcher as cl

OK, MISSING = "#2E8B57", "#E5484D"


def _row(ok: bool | None, title: str, message: str, fix_html: str) -> str:
    color = "#6B7280" if ok is None else OK if ok else MISSING
    mark = "…" if ok is None else "✓" if ok else "✗"
    fix = "" if ok else f"<div style='margin:4px 0 12px 22px'>{fix_html}</div>"
    return (f"<p style='margin:8px 0 2px 0'><b style='color:{color}'>{mark}</b>&nbsp; <b>{title}</b>: "
            f"{escape(message)}</p>{fix}")


def setup_html(claude: cl.SignInStatus | None, node: cl.NodeStatus | None) -> str:
    claude_fix = (
        f"Install Claude Code: open <b>PowerShell</b> and run<br><code>{escape(cl.CLAUDE_INSTALL_POWERSHELL)}</code><br>"
        "Then open a new terminal, run <code>claude</code> once and sign in with your Claude subscription "
        f"(Pro or Max). Details: <a href='{cl.CLAUDE_SETUP_URL}'>{cl.CLAUDE_SETUP_URL}</a>")
    node_fix = (f"Install Node.js {cl.MIN_NODE_MAJOR} or later (the LTS version) from "
                f"<a href='{cl.NODE_DOWNLOAD_URL}'>{cl.NODE_DOWNLOAD_URL}</a>, then restart this app.")
    return (
        "<p>Web Testing Assistant runs your tests with Claude Code on your own subscription, and Claude Code "
        "drives the browser through Playwright MCP, which needs Node.js.</p>"
        + _row(None if claude is None else claude.ok, "Claude Code",
               "checking…" if claude is None else claude.message, claude_fix)
        + _row(None if node is None else node.ok, "Node.js", "checking…" if node is None else node.message, node_fix)
    )


class SetupDialog(QDialog):
    check_again = Signal()

    def __init__(self, claude: cl.SignInStatus | None = None, node: cl.NodeStatus | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Check setup")
        self.setMinimumWidth(600)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setOpenExternalLinks(True)
        self.again_button = QPushButton("Check again")
        self.again_button.clicked.connect(self._again)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.again_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(buttons)
        self.show_status(claude, node)

    def show_status(self, claude: cl.SignInStatus | None, node: cl.NodeStatus | None) -> None:
        self.claude, self.node = claude, node
        self.text.setText(setup_html(claude, node))
        self.again_button.setEnabled(claude is not None and node is not None)

    def _again(self) -> None:
        self.show_status(None, None)
        self.check_again.emit()
