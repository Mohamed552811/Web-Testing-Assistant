"""Windows notifications when a run finishes or needs your confirmation.

On Windows the app registers its own identity (AppUserModelID with its name and logo, per user, in
HKCU\\Software\\Classes\\AppUserModelId) and sends real Windows toast notifications under it: they show
"Web Testing Assistant" with our logo, not Python's, and they stay in the notification center after the
app closes. Toasts are sent through Windows PowerShell's built-in WinRT access, so no extra package is
needed. If that fails (or on other systems), Qt's tray icon message is the fallback.

The taskbar button also flashes until you look at the window.
"""

import base64
import subprocess
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from wta import icons

NOTIFY_MS = 8000
IDENTITY_KEY = r"Software\Classes\AppUserModelId"
TOAST_TIMEOUT_S = 30

# Set by register_app_identity() at startup; None means "no toasts, use the tray message".
_toast_app_id: str | None = None


def register_app_identity(app_id: str, display_name: str, icon_png: Path) -> bool:
    """Tell Windows the name and logo of our AppUserModelID (per user, no admin rights), so toasts show
    them and are kept in the notification center. Returns whether toasts can be used."""
    global _toast_app_id
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{IDENTITY_KEY}\{app_id}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_png))
    except OSError:
        return False
    _toast_app_id = app_id
    return True


def toast_xml(heading: str, message: str, warning: bool = False) -> str:
    # A confirmation request stays on screen until it is dealt with; a finished run goes away on its own.
    scenario = ' scenario="reminder"' if warning else ""
    actions = ('<actions><action content="Dismiss" arguments="dismiss" activationType="system"/></actions>'
               if warning else "")
    return (f"<toast{scenario}><visual><binding template=\"ToastGeneric\"><text>{escape(heading)}</text>"
            f"<text>{escape(message)}</text></binding></visual>{actions}</toast>")


def toast_script(app_id: str, xml: str) -> str:
    def quoted(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]"
        " | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]"
        " | Out-Null\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$xml.LoadXml({quoted(xml)})\n"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({quoted(app_id)}).Show($toast)\n"
    )


def toast_command(app_id: str, xml: str) -> list[str]:
    encoded = base64.b64encode(toast_script(app_id, xml).encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]


class Notifier(QObject):
    _toast_failed = Signal(str, str, bool)  # heading, message, warning: show the tray message instead

    def __init__(self, window: QWidget, title: str) -> None:
        super().__init__(window)
        self.window = window
        self.title = title
        self.tray: QSystemTrayIcon | None = None
        self.sent: list[tuple[str, str]] = []  # (heading, message), for tests and the status bar
        self._toast_failed.connect(self._tray_message)

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable() and QSystemTrayIcon.supportsMessages()

    def toasts(self) -> bool:
        return _toast_app_id is not None

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
        """Without toasts, show the tray icon before the first notification, so Windows has a sender."""
        if self.toasts():
            return
        tray = self._ensure_tray()
        if tray is not None:
            tray.show()

    def notify(self, heading: str, message: str, warning: bool = False) -> None:
        self.sent.append((heading, message))
        QApplication.alert(self.window)  # flash the taskbar button until the window is active
        if self.toasts():
            self._send_toast(heading, message, warning)
        else:
            self._tray_message(heading, message, warning)

    def _send_toast(self, heading: str, message: str, warning: bool) -> None:
        command = toast_command(_toast_app_id, toast_xml(heading, message, warning))

        def send() -> None:
            try:
                done = subprocess.run(command, capture_output=True, timeout=TOAST_TIMEOUT_S, stdin=subprocess.DEVNULL,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = done.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if not ok:
                self._toast_failed.emit(heading, message, warning)

        threading.Thread(target=send, daemon=True).start()

    def _tray_message(self, heading: str, message: str, warning: bool = False) -> None:
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
