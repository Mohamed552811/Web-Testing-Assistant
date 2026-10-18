"""Welcome screen shown instead of an empty browser until a session is open, and the About box."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wta import __version__, icons
from wta.db.models import TestSession

REPO_URL = "https://github.com/Mohamed552811/Web-Testing-Assistant"
RECENT_COUNT = 6


class WelcomePage(QWidget):
    new_requested = Signal()
    open_requested = Signal()
    session_chosen = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(112))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Web Testing Assistant", alignment=Qt.AlignmentFlag.AlignCenter)
        title.setProperty("heading", True)
        subtitle = QLabel("Claude Code tests your web app. You review every step.",
                          alignment=Qt.AlignmentFlag.AlignCenter)
        subtitle.setProperty("muted", True)

        self.new_button = QPushButton("New session")
        self.new_button.setProperty("primary", True)
        icons.set_icon(self.new_button, "file-plus", "on-accent")
        self.open_button = QPushButton("Open session…")
        icons.set_icon(self.open_button, "folder-open")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)

        self.recent_label = QLabel("Recent sessions")
        self.recent_label.setProperty("muted", True)
        self.recent = QListWidget()
        self.recent.setFixedWidth(520)
        self.recent.setMaximumHeight(190)

        column = QVBoxLayout()
        column.addStretch(2)
        column.addWidget(logo)
        column.addWidget(title)
        column.addWidget(subtitle)
        column.addSpacing(18)
        column.addLayout(buttons)
        column.addSpacing(22)
        column.addWidget(self.recent_label, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.recent, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(3)
        version = QLabel(f"Version {__version__}", alignment=Qt.AlignmentFlag.AlignCenter)
        version.setProperty("muted", True)
        column.addWidget(version)
        self.setLayout(column)

        self.new_button.clicked.connect(self.new_requested)
        self.open_button.clicked.connect(self.open_requested)
        self.recent.itemActivated.connect(lambda item: self.session_chosen.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.recent.itemClicked.connect(lambda item: self.session_chosen.emit(item.data(Qt.ItemDataRole.UserRole)))

    def set_sessions(self, sessions: list[TestSession]) -> None:
        self.recent.clear()
        for session in sessions[:RECENT_COUNT]:
            item = QListWidgetItem(f"{session.name}    —    {session.target_url}")
            item.setData(Qt.ItemDataRole.UserRole, session.id)
            item.setToolTip(f"Created {session.created_at:%Y-%m-%d %H:%M} (UTC)")
            self.recent.addItem(item)
        has_sessions = bool(sessions)
        self.recent.setVisible(has_sessions)
        self.recent_label.setVisible(has_sessions)


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Web Testing Assistant")
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(80))
        text = QLabel(
            f"<h2>Web Testing Assistant</h2><p>Version {__version__}</p>"
            "<p>A desktop app in which Claude Code tests web apps in an embedded browser through Playwright MCP, "
            "and you review every step.</p>"
            f"<p><a href='{REPO_URL}'>{REPO_URL}</a></p>"
            "<p style='color:gray'>Icons: Lucide (ISC licence). Logo: original artwork.</p>"
        )
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        row = QHBoxLayout()
        row.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        row.addSpacing(12)
        row.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)
