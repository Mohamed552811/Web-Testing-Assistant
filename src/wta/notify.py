"""Windows notifications when a run finishes or needs your confirmation.

Uses Qt's own tray icon (QSystemTrayIcon): the icon appears in the notification area while
the app runs tests, and clicking the notification or the icon brings the window back.
The taskbar button also flashes until you look at the window.
"""

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from wta import icons

NOTIFY_MS = 8000


class Notifier(QObject):
    def __init__(self, window: QWidget, title: str) -> None:
        super().__init__(window)
        self.window = window
        self.title = title
        self.tray: QSystemTrayIcon | None = None
        self.sent: list[tuple[str, str]] = []  # (heading, message), for tests and the status bar

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable() and QSystemTrayIcon.supportsMessages()

    def _ensure_tray(self) -> QSystemTrayIcon | None:
        if self.tray is None and self.available():
            self.tray = QSystemTrayIcon(icons.logo_icon(), self)
            self.tray.setToolTip(self.title)
            menu = QMenu(self.window)
            menu.addAction("Show window", self.show_window)
            self.tray.setContextMenu(menu)
            self.tray.messageClicked.connect(self.show_window)
            self.tray.activated.connect(lambda _reason: self.show_window())
        return self.tray

    def run_started(self) -> None:
        """Show the tray icon before the first notification, so Windows has a sender to show."""
        tray = self._ensure_tray()
        if tray is not None:
            tray.show()

    def notify(self, heading: str, message: str, warning: bool = False) -> None:
        self.sent.append((heading, message))
        QApplication.alert(self.window)  # flash the taskbar button until the window is active
        tray = self._ensure_tray()
        if tray is None:
            return
        tray.show()
        kind = QSystemTrayIcon.MessageIcon.Warning if warning else QSystemTrayIcon.MessageIcon.Information
        tray.showMessage(heading, message, kind, NOTIFY_MS)

    def show_window(self) -> None:
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
