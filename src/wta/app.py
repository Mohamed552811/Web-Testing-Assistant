"""Application entry point and main window."""

import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox, QSplitter, QTabWidget

from wta import __version__
from wta.browser import BrowserPane
from wta.db import Database, store
from wta.db.models import TestSession
from wta.paths import database_path
from wta.scenario_tab import ScenarioTab
from wta.session_dialogs import OpenSessionDialog, SessionDialog

APP_TITLE = "Web Testing Assistant"
START_PAGE = "about:blank"


class MainWindow(QMainWindow):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.current_session: TestSession | None = None
        self.resize(1600, 940)

        self.browser = BrowserPane(self)
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
        self.browser.view.load(QUrl(session.target_url))
        self.statusBar().showMessage(f"Opened session “{session.name}”")


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_TITLE)
    db = Database(database_path())
    window = MainWindow(db)
    window.show()
    try:
        return app.exec()
    finally:
        db.close()
