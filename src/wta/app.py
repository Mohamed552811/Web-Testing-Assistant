"""Application entry point and main window."""

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWebEngineCore import QWebEngineProfile, qWebEngineChromiumVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from wta import __version__
from wta import cdp
from wta import settings as app_settings
from wta.browser import BrowserPane, make_profile
from wta.connection_dialog import ConnectionDialog
from wta.domain_guard import NavigationGuard
from wta.processes import ProcessRegistry
from wta.results import ConfirmationRequest
from wta.db import Database, store
from wta.db import runs as run_store
from wta.db import scenarios as sc
from wta.db import site_notes
from wta.db.models import TestSession
from wta.paths import data_dir, database_path
from wta.db.models import ScenarioKind
from wta.prompt import RunContext, SmokeContext
from wta.run_panel import RunPanel
from wta.runs_tab import RunsTab
from wta.runner import BatchRun, RunOutcome, ScenarioJob, ScenarioRun, default_runs_dir, run_folder
from wta.scenario_tab import ScenarioTab
from wta.session_dialogs import OpenSessionDialog, SessionDialog
from wta.usage_bar import TopBar

APP_TITLE = "Web Testing Assistant"
START_PAGE = "about:blank"
USAGE_FILE = "usage.json"
PROCESSES_FILE = "processes.json"
SETTINGS_FILE = "settings.json"
CDP_CHECK_ATTEMPTS = 4


class _CdpChecker(QObject):
    """Checks the debugging endpoint on a worker thread (see wta.cdp for why)."""

    done = Signal(object)  # dict | None

    def run(self, port: int) -> None:
        threading.Thread(target=lambda: self.done.emit(cdp.endpoint_info(port, timeout=3)), daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(
        self,
        db: Database,
        profile: QWebEngineProfile | None = None,
        cdp_port: int | None = None,
        app_dir: Path | None = None,
        check_sign_in: bool = True,
        guard: NavigationGuard | None = None,
    ) -> None:
        super().__init__()
        self.db = db
        self.cdp_port = cdp_port
        self.app_dir = app_dir or data_dir()
        self.guard = guard
        self.registry = ProcessRegistry(self.app_dir / PROCESSES_FILE)
        self.settings = app_settings.load(self.app_dir / SETTINGS_FILE)
        if guard is not None:
            guard.blocked.connect(self._on_navigation_blocked)
        self.current_session: TestSession | None = None
        self.batch_run: BatchRun | None = None
        self.cdp_info: dict | None = None
        self._cdp_attempts = 0
        self._cdp_checker = _CdpChecker(self)
        self._cdp_checker.done.connect(self._on_cdp_checked)
        self.resize(1600, 940)

        self.top_bar = TopBar(self.app_dir / USAGE_FILE, self)
        self.top_bar.sign_in_changed.connect(lambda _ok: self._update_run_state())
        self.top_bar.close_all_requested.connect(self.close_all_browsers)

        self.browser = BrowserPane(self, profile)
        self.browser.view.load(QUrl(START_PAGE))
        self.browser.status_message.connect(self.statusBar().showMessage)

        self.scenario_tab = ScenarioTab(db, self)
        self.scenario_tab.status_message.connect(self.statusBar().showMessage)
        self.scenario_tab.run_requested.connect(lambda batch: self.run_batch(batch, ScenarioKind.SCENARIO))
        self.smoke_tab = ScenarioTab(db, self, kind=ScenarioKind.SMOKE)
        self.smoke_tab.status_message.connect(self.statusBar().showMessage)
        self.smoke_tab.run_requested.connect(lambda batch: self.run_batch(batch, ScenarioKind.SMOKE))
        self.review_tabs = (self.scenario_tab, self.smoke_tab)
        self.run_panel = RunPanel(self)
        self.run_panel.stop_requested.connect(self.stop_run)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.scenario_tab, "Scenarios")
        self.tabs.addTab(self.smoke_tab, "Smoke test")
        self.tabs.addTab(self.run_panel, "Live run")
        self.runs_tab = RunsTab(db, self)
        self.tabs.addTab(self.runs_tab, "Runs")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(self.tabs)
        splitter.setSizes([850, 750])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self._build_menu()
        self._update_title()
        self._update_run_state()
        if cdp_port is not None:
            QTimer.singleShot(1000, self._check_cdp)
        if check_sign_in:
            self.top_bar.check_sign_in()

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
        close_all = QAction("Close &all browsers", self)
        close_all.triggered.connect(self.close_all_browsers)
        browser_menu.addAction(close_all)

        run_menu = self.menuBar().addMenu("&Run")
        self.confirm_action = QAction("Confirm risky actions before Claude does them", self, checkable=True)
        self.confirm_action.setChecked(self.settings.confirm_risky_actions)
        self.confirm_action.setToolTip(
            "Claude pauses before deleting, sending, paying or other irreversible actions and asks you first."
        )
        self.confirm_action.toggled.connect(self._set_confirm_risky)
        run_menu.addAction(self.confirm_action)

    def _set_confirm_risky(self, enabled: bool) -> None:
        self.settings.confirm_risky_actions = enabled
        app_settings.save(self.app_dir / SETTINGS_FILE, self.settings)
        self.statusBar().showMessage(
            "Claude will ask before risky actions (from the next run)." if enabled
            else "Claude will not pause before risky actions.", 5000
        )

    def _check_cdp(self) -> None:
        self._cdp_attempts += 1
        self._cdp_checker.run(self.cdp_port)

    def _on_cdp_checked(self, info: dict | None) -> None:
        self.cdp_info = info
        if info is None and self._cdp_attempts < CDP_CHECK_ATTEMPTS:
            QTimer.singleShot(2000, self._check_cdp)
            return
        if info is None:
            QMessageBox.warning(
                self,
                "Browser debugging port",
                f"The embedded browser is not answering on {cdp.endpoint_url(self.cdp_port)}.\n"
                "Claude Code will not be able to use it. Restart the app to pick a new port.",
            )
        else:
            self.statusBar().showMessage(f"Browser debugging on {cdp.endpoint_url(self.cdp_port)}", 5000)
        self._update_run_state()

    def show_connection(self) -> None:
        ConnectionDialog(
            self.cdp_port, self.cdp_info, self.app_dir / "mcp" / "playwright-mcp.json", self.app_dir / "manual-run", self
        ).exec()

    def _update_title(self) -> None:
        suffix = f" — {self.current_session.name}" if self.current_session else ""
        self.setWindowTitle(f"{APP_TITLE} {__version__}{suffix}")

    # --- sessions ----------------------------------------------------------------

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
        self._update_title()
        for tab in self.review_tabs:
            tab.set_session(session.id)
        self.runs_tab.set_session(session.id, site_notes.site_for_url(session.target_url))
        self.browser.set_viewport(session.viewport_width, session.viewport_height)
        self.browser.view.load(QUrl(session.target_url))
        self.statusBar().showMessage(f"Opened session “{session.name}”")
        self._update_run_state()

    # --- running -----------------------------------------------------------------

    def _run_blocker(self) -> str:
        """Why a run cannot start now, or "" when it can."""
        if self.current_session is None:
            return "Open a session first."
        if not self.top_bar.signed_in:
            return "Claude Code must be installed and signed in (see the top bar)."
        if self.cdp_port is None:
            return "The embedded browser has no debugging port."
        if self.cdp_info is None:
            if self._cdp_attempts < CDP_CHECK_ATTEMPTS:
                return "Checking the embedded browser's debugging port…"
            return "The embedded browser's debugging port is not available. Restart the app."
        return ""

    def _update_run_state(self) -> None:
        running = self.batch_run is not None
        blocker = "" if running else self._run_blocker()
        for tab in self.review_tabs:
            tab.set_run_state(allowed=not blocker, running=running, reason=blocker)
        self.new_action.setEnabled(not running)
        self.open_action.setEnabled(not running)
        self.details_action.setEnabled(not running and self.current_session is not None)

    def _site_notes_for(self, url: str) -> list[str]:
        return [n.note for n in site_notes.list_notes(self.db, site_notes.site_for_url(url))]

    def run_batch(self, batch: int, kind: ScenarioKind = ScenarioKind.SCENARIO) -> None:
        blocker = self._run_blocker()
        if blocker:
            QMessageBox.information(self, "Run batch", blocker)
            return
        session = store.load_session(self.db, self.current_session.id)
        selected = sc.scenarios_to_run(self.db, session.id, batch, kind)
        if not selected:
            QMessageBox.information(
                self,
                "Run batch",
                "Nothing to run: every scenario in this batch already has results.\n"
                "Use “Request re-run” on a step to run its scenario again.",
            )
            return
        reruns = [scenario.name for scenario, rerun in selected if rerun]
        if reruns:
            answer = QMessageBox.question(
                self,
                "Re-run scenarios",
                "These scenarios will run again. Claude's new results replace its earlier ones; "
                "your reviews are kept.\n\n• " + "\n• ".join(reruns),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        runs_dir = default_runs_dir(self.app_dir)
        jobs = [
            ScenarioJob(
                scenario_id=scenario.id,
                title=(f"Smoke: {scenario.name}" if scenario.smoke_page else scenario.name),
                run_dir=run_folder(runs_dir, session.id, batch, scenario.position, smoke=scenario.smoke_page is not None),
                context=RunContext(
                    session_name=session.name,
                    target_url=session.target_url,
                    allowed_domains=list(session.allowed_domains),
                    test_account_email=session.test_account_email or "",
                    test_account_password=session.test_account_password or "",
                    scenario_name=scenario.name,
                    scenario_goal=scenario.goal,
                    scenario_test_data=scenario.test_data,
                    site_notes=self._site_notes_for(scenario.smoke_page.url if scenario.smoke_page else session.target_url),
                    confirm_risky_actions=self.settings.confirm_risky_actions,
                    smoke=SmokeContext(**vars(sc.SmokePageInput.of(scenario.smoke_page))) if scenario.smoke_page else None,
                ),
            )
            for scenario, _rerun in selected
        ]
        if self.guard is not None:
            self.guard.activate(list(session.allowed_domains))
        self.batch_run = BatchRun(self.db, jobs, self.cdp_port, self.app_dir / USAGE_FILE, self.registry, self)
        self.batch_run.confirmation_needed.connect(self._ask_confirmation)
        self.batch_run.scenario_started.connect(self.run_panel.scenario_started)
        self.batch_run.event.connect(self.run_panel.add_event)
        for tab in self.review_tabs:
            self.batch_run.results_changed.connect(tab.refresh)
        self.batch_run.usage_changed.connect(lambda _state: self.top_bar.refresh_usage())
        self.batch_run.scenario_finished.connect(self._scenario_finished)
        self.batch_run.all_finished.connect(self._batch_finished)
        self.run_panel.clear()
        self.run_panel.set_running(True)
        self.tabs.setCurrentWidget(self.run_panel)
        self._update_run_state()
        self.batch_run.start()

    def stop_run(self) -> None:
        if self.batch_run is not None:
            self.batch_run.stop()
            self.statusBar().showMessage("Run stopped.")

    def close_all_browsers(self) -> None:
        """Stop any run and close every process the app or Playwright started (tracked by PID).
        The embedded browser is part of the app, so its page is unloaded instead."""
        stopped = self.batch_run is not None
        self.registry.refresh()
        tracked = {pid for pid, entry in self.registry.entries.items() if self.registry.is_same_process(entry)}
        self.stop_run()  # kills the run's process tree
        tracked |= {entry.pid for entry in self.registry.kill_all()}  # anything else still tracked
        self.browser.view.stop()
        self.browser.view.load(QUrl(START_PAGE))
        parts = (["stopped the run"] if stopped else []) + [
            f"closed {len(tracked)} process(es) the app started",
            "cleared the embedded browser",
        ]
        self.statusBar().showMessage("Close all browsers: " + ", ".join(parts) + ".")

    def _on_navigation_blocked(self, url: str) -> None:
        message = f"Blocked navigation to {url}: outside the allowed domains."
        self.statusBar().showMessage(message, 8000)
        if self.batch_run is not None and self.batch_run.current is not None:
            self.batch_run.current.add_note(f"App: {message}")

    def _ask_confirmation(self, run: ScenarioRun, request: ConfirmationRequest) -> None:
        self.tabs.setCurrentWidget(self.run_panel)
        step = f" (step {request.step})" if request.step else ""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Claude asks for confirmation")
        box.setText(f"Claude wants to do a risky action{step}:\n\n{request.action}")
        box.setInformativeText(f"Why: {request.reason or '(no reason given)'}\n\nAllow it?")
        allow = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Decline", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if self.batch_run is None or run.stopped:
            return  # stopped while the question was open
        run.answer_confirmation(box.clickedButton() is allow)

    def _scenario_finished(self, outcome: RunOutcome) -> None:
        title = next((j.title for j in self.batch_run.jobs if j.scenario_id == outcome.scenario_id), "Scenario")
        self.run_panel.scenario_finished(outcome, title)
        for tab in self.review_tabs:
            tab.refresh(outcome.scenario_id)
        self.runs_tab.refresh()

    def _batch_finished(self) -> None:
        if self.guard is not None:
            self.guard.deactivate()
        self.run_panel.all_finished(stopped=self.batch_run.stopped)
        self.batch_run.deleteLater()
        self.batch_run = None
        self._update_run_state()
        self.statusBar().showMessage(
            "Run finished. Review each step on the Scenarios or Smoke test tab; add site notes on the Runs tab."
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.batch_run is not None:
            answer = QMessageBox.question(self, "Quit", "A test run is in progress. Stop it and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.batch_run.stop()
        self.registry.kill_all()  # on exit, close everything the app started
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    cdp_port = cdp.find_free_port()
    app = QApplication(argv + cdp.enable(cdp_port))  # must happen before QApplication
    app.setApplicationName(APP_TITLE)
    db = Database(database_path())
    run_store.mark_interrupted_runs(db)  # runs cut off by a crash or kill last time
    profile = make_profile(data_dir() / "browser-profile", app)
    guard = NavigationGuard(app)
    profile.setUrlRequestInterceptor(guard)
    window = MainWindow(db, profile, cdp_port, guard=guard)
    window.show()
    try:
        return app.exec()
    finally:
        del window  # pages must go before their profile
        db.close()
