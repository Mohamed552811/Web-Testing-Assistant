"""Application entry point and main window."""

import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWebEngineCore import QWebEngineProfile, qWebEngineChromiumVersion
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox, QSplitter, QTabWidget

from wta import __version__
from wta import cdp
from wta.browser import BrowserPane, make_profile
from wta.connection_dialog import ConnectionDialog
from wta.db import Database, store
from wta.db.models import TestSession
from wta.paths import data_dir, database_path
from wta.scenario_tab import ScenarioTab
from wta.session_dialogs import OpenSessionDialog, SessionDialog

APP_TITLE = "Web Testing Assistant"
START_PAGE = "about:blank"


class MainWindow(QMainWindow):
    def __init__(self, db: Database, profile: QWebEngineProfile | None = None, cdp_port: int | None = None) -> None:
        super().__init__()
        self.db = db
        self.cdp_port = cdp_port
        self.current_session: TestSession | None = None
        self.resize(1600, 940)

        self.browser = BrowserPane(self, profile)
        self.browser.view.load(QUrl(START_PAGE))
        self.browser.status_message.connect(self.statusBar().showMessage)

        self.scenario_tab = ScenarioTab(db, self)
        self.scenario_tab.status_message.connect(self.statusBar().showMessage)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.scenario_tab, "Scenarios")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(self.tabs)
        splitter.setSizes([850, 750])
        self.setCentralWidget(splitter)

        self._build_menu()
        self._update_title()
        if cdp_port is not None:
            QTimer.singleShot(1500, self._check_cdp)

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Session")

        self.new_action = QAction("&New session…", self, shortcut=QKeySequence.StandardKey.New)
        self.open_action = QAction("&Open session…", self, shortcut=QKeySequence.StandardKey.Open)
        self.details_action = QAction("Session &details…", self, enabled=False)
        quit_action = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit)

        self.new_action.triggered.connect(self.new_session)
        self.open_action.triggered.connect(self.open_session)
        self.details_action.triggered.connect(self.edit_session)
        quit_action.triggered.connect(self.close)

        menu.addActions([self.new_action, self.open_action, self.details_action])
        menu.addSeparator()
        menu.addAction(quit_action)

        browser_menu = self.menuBar().addMenu("&Browser")
        self.connection_action = QAction("&Claude connection…", self, enabled=self.cdp_port is not None)
        self.connection_action.triggered.connect(self.show_connection)
        browser_menu.addAction(self.connection_action)

    def _check_cdp(self) -> None:
        if cdp.endpoint_info(self.cdp_port) is None:
            QMessageBox.warning(
                self,
                "Browser debugging port",
                f"The embedded browser is not answering on {cdp.endpoint_url(self.cdp_port)}.\n"
                "Claude Code will not be able to use it. Restart the app to pick a new port.",
            )
        else:
            self.statusBar().showMessage(f"Browser debugging on {cdp.endpoint_url(self.cdp_port)}", 5000)

    def show_connection(self) -> None:
        base = data_dir()
        ConnectionDialog(self.cdp_port, base / "mcp" / "playwright-mcp.json", base / "manual-run", self).exec()

    def _update_title(self) -> None:
        suffix = f" — {self.current_session.name}" if self.current_session else ""
        self.setWindowTitle(f"{APP_TITLE} {__version__}{suffix}")

    def new_session(self) -> None:
        dialog = SessionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        session = store.create_session(self.db, dialog.details(), browser_version=f"Chromium {qWebEngineChromiumVersion()}")
        self.show_session(session)

    def open_session(self) -> None:
        dialog = OpenSessionDialog(store.list_sessions(self.db), self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_id() is None:
            return
        try:
            session = store.load_session(self.db, dialog.selected_id())
        except LookupError as exc:
            QMessageBox.warning(self, "Open session", str(exc))
            return
        self.show_session(session)

    def edit_session(self) -> None:
        if self.current_session is None:
            return
        dialog = SessionDialog(self, store.details_of(self.current_session))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.show_session(store.update_session(self.db, self.current_session.id, dialog.details()))

    def show_session(self, session: TestSession) -> None:
        self.current_session = session
        self.details_action.setEnabled(True)
        self._update_title()
        self.scenario_tab.set_session(session.id)
        self.browser.set_viewport(session.viewport_width, session.viewport_height)
        self.browser.view.load(QUrl(session.target_url))
        self.statusBar().showMessage(f"Opened session “{session.name}”")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    cdp_port = cdp.find_free_port()
    app = QApplication(argv + cdp.enable(cdp_port))  # must happen before QApplication
    app.setApplicationName(APP_TITLE)
    db = Database(database_path())
    profile = make_profile(data_dir() / "browser-profile", app)
    window = MainWindow(db, profile, cdp_port)
    window.show()
    try:
        return app.exec()
    finally:
        del window  # pages must go before their profile
        db.close()
