"""Application entry point and main window."""

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWebEngineCore import QWebEngineProfile, qWebEngineChromiumVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from wta import __version__
from wta import cdp, cost, icons, theme
from wta import settings as app_settings
from wta.welcome import AboutDialog, WelcomePage
from wta.browser import BrowserPane, make_profile
from wta.connection_dialog import ConnectionDialog
from wta.domain_guard import NavigationGuard
from wta.notify import Notifier
from wta.processes import ProcessRegistry
from wta.results import ConfirmationRequest
from wta.db import Database, store
from wta.db import runs as run_store
from wta.db import scenarios as sc
from wta.db import site_notes
from wta.db.models import StepStatus, TestSession
from wta.paths import data_dir, database_path
from wta.db.models import ScenarioKind
from wta.prompt import RunContext, SmokeContext
from wta.run_panel import RunPanel
from wta.runs_tab import RunsTab
from wta.runner import BatchRun, RunOutcome, ScenarioJob, ScenarioRun, default_runs_dir, run_folder
from wta.scenario_tab import ScenarioTab
from wta.session_dialogs import OpenSessionDialog, SessionDialog
from wta.settings_dialog import SettingsDialog
from wta.export import export_report
from wta.usage_bar import TopBar

APP_TITLE = "Web Testing Assistant"
START_PAGE = "about:blank"
USAGE_FILE = "usage.json"
PROCESSES_FILE = "processes.json"
SETTINGS_FILE = "settings.json"
UI_STATE_FILE = "ui-state.ini"
CDP_CHECK_ATTEMPTS = 4
APP_USER_MODEL_ID = "WebTestingAssistant.App"


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
        self.browser.stop_requested.connect(self.stop_run)
        self.notifier = Notifier(self, APP_TITLE)
        self._batch_outcomes: list[RunOutcome] = []
        self._batch_smoke = False

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
        for tab in self.review_tabs:
            tab.site_note_saved.connect(self.runs_tab.notes_panel.refresh)

        # Left side: the welcome screen until a session is open, then the browser.
        self.welcome = WelcomePage(self)
        self.welcome.new_requested.connect(self.new_session)
        self.welcome.open_requested.connect(self.open_session)
        self.welcome.session_chosen.connect(self.open_session_by_id)
        self.left = QStackedWidget()
        self.left.addWidget(self.welcome)
        self.left.addWidget(self.browser)
        self.welcome.set_sessions(store.list_sessions(db))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left)
        splitter.addWidget(self.tabs)
        splitter.setSizes([850, 750])
        self.splitter = splitter

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self._build_menu()
        self._build_toolbar()
        self._apply_button_icons()
        self._update_title()
        self._update_run_state()
        self._restore_ui_state()
        if cdp_port is not None:
            QTimer.singleShot(1000, self._check_cdp)
        if check_sign_in:
            self.top_bar.check_sign_in()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Session")

        self.new_action = QAction("&New session…", self, shortcut=QKeySequence.StandardKey.New)
        self.open_action = QAction("&Open session…", self, shortcut=QKeySequence.StandardKey.Open)
        self.details_action = QAction("Session &details…", self, enabled=False)
        self.export_action = QAction("&Export report…", self, enabled=False, shortcut=QKeySequence("Ctrl+E"))
        self.settings_action = QAction("Se&ttings…", self, shortcut=QKeySequence("Ctrl+,"))
        quit_action = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit)

        self.new_action.triggered.connect(self.new_session)
        self.open_action.triggered.connect(self.open_session)
        self.details_action.triggered.connect(self.edit_session)
        # lambda: triggered(bool) would otherwise pass `checked` as the path.
        self.export_action.triggered.connect(lambda: self.export_report())
        self.settings_action.triggered.connect(self.edit_settings)
        quit_action.triggered.connect(self.close)

        menu.addActions([self.new_action, self.open_action, self.details_action])
        menu.addSeparator()
        menu.addAction(self.export_action)
        menu.addAction(self.settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        browser_menu = self.menuBar().addMenu("&Browser")
        self.connection_action = QAction("&Claude connection…", self, enabled=self.cdp_port is not None)
        self.connection_action.triggered.connect(self.show_connection)
        browser_menu.addAction(self.connection_action)
        self.close_all_action = QAction("Close &all browsers", self)
        self.close_all_action.triggered.connect(self.close_all_browsers)
        browser_menu.addAction(self.close_all_action)

        self.run_action = QAction("&Run batch", self, shortcut=QKeySequence("F5"))
        self.run_action.setToolTip("Run the batch on the Scenarios or Smoke test tab (F5)")
        self.run_action.triggered.connect(self.run_current_tab)
        self.stop_action = QAction("&Stop", self, shortcut=QKeySequence("Shift+F5"), enabled=False)
        self.stop_action.triggered.connect(self.stop_run)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About Web Testing Assistant", self)
        about.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(about)
        icons.set_icon(about, "info")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("main-toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for action, name in ((self.new_action, "file-plus"), (self.open_action, "folder-open"),
                             (self.run_action, "play"), (self.stop_action, "square"),
                             (self.export_action, "download"), (self.settings_action, "settings"),
                             (self.close_all_action, "circle-x")):
            icons.set_icon(action, name, "accent" if action is self.run_action else None)
        toolbar.addActions([self.new_action, self.open_action])
        toolbar.addSeparator()
        toolbar.addActions([self.run_action, self.stop_action])
        toolbar.addSeparator()
        toolbar.addActions([self.export_action, self.settings_action])
        toolbar.addSeparator()
        toolbar.addAction(self.close_all_action)
        self.addToolBar(toolbar)
        self.toolbar = toolbar

    def _apply_button_icons(self) -> None:
        """Icons on the buttons inside the tabs and panels (re-tinted with the theme)."""
        b = self.browser
        for button, name in ((b.back_button, "arrow-left"), (b.forward_button, "arrow-right"),
                             (b.reload_button, "rotate-cw")):
            button.setText("")
            icons.set_icon(button, name)
        for tab in self.review_tabs:
            tab.run_button.setProperty("primary", True)
            for button, name, role in ((tab.run_button, "play", "on-accent"), (tab.new_batch_button, "plus", None),
                                       (tab.add_scenario_button, "plus", None), (tab.edit_scenario_button, "pencil", None),
                                       (tab.remove_scenario_button, "trash-2", None), (tab.add_step_button, "plus", None),
                                       (tab.edit_step_button, "pencil", None), (tab.delete_step_button, "trash-2", None),
                                       (tab.approve_button, "check", None), (tab.override_button, "pencil", None),
                                       (tab.rerun_button, "rotate-ccw", None),
                                       (tab.site_note_button, "file-plus", None)):
                icons.set_icon(button, name, role)
        icons.set_icon(self.run_panel.stop_button, "square", "on-accent")
        self.browser.banner.icon.setPixmap(icons.icon("bot", "#ffffff").pixmap(20, 20))
        icons.set_icon(self.browser.banner.stop_button, "square", "on-accent")
        icons.set_icon(self.runs_tab.open_folder_button, "folder")
        icons.set_icon(self.runs_tab.copy_resume_button, "copy")
        icons.set_icon(self.runs_tab.refresh_button, "rotate-cw")
        # Close all browsers now lives in the toolbar; the top bar keeps only status and usage.
        self.top_bar.close_all_button.hide()

    def run_current_tab(self) -> None:
        """Toolbar Run: the batch on the Scenarios or Smoke test tab, whichever is showing (Scenarios otherwise)."""
        tab = self.tabs.currentWidget()
        if tab not in self.review_tabs:
            tab = self.scenario_tab
            self.tabs.setCurrentWidget(tab)
        if tab.run_button.isEnabled():
            tab.run_button.click()

    def open_session_by_id(self, session_id: int) -> None:
        try:
            self.show_session(store.load_session(self.db, session_id))
        except LookupError as exc:
            QMessageBox.warning(self, "Open session", str(exc))

    # --- remembered layout ---------------------------------------------------------

    def _ui_state(self) -> QSettings:
        return QSettings(str(self.app_dir / UI_STATE_FILE), QSettings.Format.IniFormat)

    def _restore_ui_state(self) -> None:
        state = self._ui_state()
        if state.value("geometry") is not None:
            self.restoreGeometry(state.value("geometry"))
        if state.value("splitter") is not None:
            self.splitter.restoreState(state.value("splitter"))
        tab = state.value("tab")
        if tab is not None and 0 <= int(tab) < self.tabs.count():
            self.tabs.setCurrentIndex(int(tab))

    def _save_ui_state(self) -> None:
        state = self._ui_state()
        state.setValue("geometry", self.saveGeometry())
        state.setValue("splitter", self.splitter.saveState())
        state.setValue("tab", self.tabs.currentIndex())
        if self.current_session is not None:
            state.setValue("last_session_id", self.current_session.id)
        state.sync()

    def reopen_last_session(self) -> bool:
        """Open the session that was open when the app last closed, if it still exists."""
        last = self._ui_state().value("last_session_id")
        if last is None:
            return False
        try:
            session = store.load_session(self.db, int(last))
        except (LookupError, ValueError):
            return False
        tab = self.tabs.currentIndex()
        self.show_session(session)
        self.tabs.setCurrentIndex(tab)
        return True

    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        theme_changed = dialog.settings().theme != self.settings.theme
        self.settings = dialog.settings()
        app_settings.save(self.app_dir / SETTINGS_FILE, self.settings)
        if theme_changed and QApplication.instance() is not None:
            theme.apply(QApplication.instance(), self.settings.theme)
        self.statusBar().showMessage("Settings saved.", 5000)

    def export_report(self, path: Path | None = None) -> Path | None:
        """Export the open session's report in the default format (Settings) or the one chosen."""
        if self.current_session is None:
            return None
        interactive = path is None  # from the menu: ask where to save, then show the result
        fmt = self.settings.export_format
        if path is not None and path.suffix.lstrip(".").lower() in app_settings.EXPORT_FORMATS:
            fmt = path.suffix.lstrip(".").lower()  # an explicit file name decides the format
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.current_session.name).strip()
            default = self.app_dir / "exports" / f"{safe_name or 'report'}-{stamp}.{fmt}"
            default.parent.mkdir(parents=True, exist_ok=True)
            filters = [f"{label} (*.{key})" for key, label in app_settings.EXPORT_FORMATS.items()]
            selected = next(f for f in filters if f.endswith(f"(*.{fmt})"))
            chosen, chosen_filter = QFileDialog.getSaveFileName(
                self, "Export report", str(default), ";;".join(filters), selected
            )
            if not chosen:
                return None
            path = Path(chosen)
            fmt = chosen_filter.rsplit("*.", 1)[-1].rstrip(")") if chosen_filter else fmt
        try:
            written = export_report(self.db, self.current_session.id, path, fmt, self.settings.tester_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export report", f"Could not export the report:\n{exc}")
            return None
        self.statusBar().showMessage(f"Report exported to {written}", 10000)
        if interactive:
            self._show_exported(written)
        return written

    def _show_exported(self, path: Path) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Report exported")
        box.setText(f"The report was saved to:\n{path}")
        open_button = box.addButton("Open report", QMessageBox.ButtonRole.AcceptRole)
        folder_button = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif box.clickedButton() is folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

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
        site = site_notes.site_for_url(session.target_url)
        for tab in self.review_tabs:
            tab.set_session(session.id, site)
        self.runs_tab.set_session(session.id, site)
        self.left.setCurrentWidget(self.browser)
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
        self.export_action.setEnabled(self.current_session is not None)
        self.run_action.setEnabled(not running and not blocker)
        self.run_action.setToolTip(blocker or "Run the batch on the Scenarios or Smoke test tab (F5)")
        self.stop_action.setEnabled(running)

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
        usage_path = self.app_dir / USAGE_FILE
        estimate = cost.estimate_batch(self.db, [scenario.kind for scenario, _rerun in selected])
        warning = cost.before_batch_warning(usage_path, self.settings.pause_at_percent, estimate)
        if warning:
            answer = QMessageBox.question(self, "Plan usage", f"{warning}\n\nRun the batch anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return

        runs_dir = default_runs_dir(self.app_dir)
        jobs = [
            ScenarioJob(
                scenario_id=scenario.id,
                title=(f"Smoke: {scenario.name}" if scenario.smoke_page else scenario.name),
                run_dir=run_folder(runs_dir, session.id, batch, scenario.position, smoke=scenario.smoke_page is not None),
                model=self.settings.smoke_model if scenario.smoke_page else self.settings.scenario_model,
                context=RunContext(
                    session_name=session.name,
                    target_url=session.target_url,
                    allowed_domains=list(session.allowed_domains),
                    test_account_email=session.test_account_email or "",
                    test_account_password=session.test_account_password or "",
                    scenario_name=scenario.name,
                    scenario_steps=scenario.test_steps or "",
                    scenario_expected_result=scenario.expected_result or "",
                    scenario_test_data=scenario.test_data,
                    site_notes=self._site_notes_for(scenario.smoke_page.url if scenario.smoke_page else session.target_url),
                    confirm_risky_actions=self.settings.confirm_risky_actions,
                    screenshot_timing=self.settings.screenshot_timing,
                    smoke=SmokeContext(**vars(sc.SmokePageInput.of(scenario.smoke_page))) if scenario.smoke_page else None,
                ),
            )
            for scenario, _rerun in selected
        ]
        if self.guard is not None:
            self.guard.activate(list(session.allowed_domains))
        self.batch_run = BatchRun(self.db, jobs, self.cdp_port, self.app_dir / USAGE_FILE, self.registry, self)
        self.batch_run.confirmation_needed.connect(self._ask_confirmation)
        limit = self.settings.pause_at_percent
        self.batch_run.pause_check = lambda: cost.pause_reason(usage_path, limit)
        self.batch_run.paused.connect(self._batch_paused)
        self.batch_run.scenario_started.connect(self.run_panel.scenario_started)
        self.batch_run.event.connect(self.run_panel.add_event)
        self.batch_run.progress.connect(self.run_panel.set_progress)
        for tab in self.review_tabs:
            self.batch_run.results_changed.connect(tab.refresh)
        self.batch_run.usage_changed.connect(lambda _state: self.top_bar.refresh_usage())
        self.batch_run.scenario_finished.connect(self._scenario_finished)
        self.batch_run.all_finished.connect(self._batch_finished)
        self.run_panel.clear()
        self.run_panel.set_running(True)
        self.browser.set_controlled(True)
        self.notifier.run_started()
        self._batch_outcomes = []
        self._batch_smoke = kind == ScenarioKind.SMOKE
        self.tabs.setCurrentWidget(self.run_panel)
        self._update_run_state()
        if estimate is not None:
            self.run_panel.summary.appendPlainText(f"Estimated plan usage for this batch: {estimate.text()}.\n")
        self.batch_run.start()

    def _batch_paused(self, reason: str) -> None:
        """Between scenarios, plan usage reached the pause limit: ask whether to go on."""
        batch = self.batch_run
        left = batch.remaining
        self.browser.set_controlled(False)
        self.run_panel.header.setText(f"Batch paused: plan usage limit reached. {left} left.")
        self.notifier.notify("Batch paused", reason, warning=True)
        answer = QMessageBox.question(
            self, "Batch paused",
            f"{reason}\n\n{left} more {'page' if self._batch_smoke else 'scenario'}(s) in this batch. "
            "Continue anyway? Choosing No stops the batch; results so far are kept.",
        )
        if self.batch_run is not batch or not batch.waiting:
            return  # stopped from elsewhere while the question was open
        if answer == QMessageBox.StandardButton.Yes:
            self.browser.set_controlled(True)
            batch.resume()
        else:
            self.stop_run()

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
        self.browser.set_controlled(True, "Claude is waiting for your confirmation before a risky action.")
        self.notifier.notify("Claude needs your confirmation", f"Risky action{step}: {request.action}", warning=True)
        box.exec()
        self.browser.set_controlled(self.batch_run is not None)
        if self.batch_run is None or run.stopped:
            return  # stopped while the question was open
        run.answer_confirmation(box.clickedButton() is allow)

    def _scenario_finished(self, outcome: RunOutcome) -> None:
        title = next((j.title for j in self.batch_run.jobs if j.scenario_id == outcome.scenario_id), "Scenario")
        self.run_panel.scenario_finished(outcome, title)
        self._batch_outcomes.append(outcome)
        for tab in self.review_tabs:
            tab.refresh(outcome.scenario_id)
        self.runs_tab.refresh()

    def _batch_finished(self) -> None:
        if self.guard is not None:
            self.guard.deactivate()
        stopped = self.batch_run.stopped
        self.run_panel.all_finished(stopped=stopped)
        self.browser.set_controlled(False)
        heading, message = self._batch_message(stopped)
        self.notifier.notify(heading, message, warning=stopped)
        self.batch_run.deleteLater()
        self.batch_run = None
        self._update_run_state()
        self.statusBar().showMessage(
            "Run finished. Review each step on the Scenarios or Smoke test tab; add site notes on the Runs tab."
        )

    def _batch_message(self, stopped: bool) -> tuple[str, str]:
        """Notification text for the end of a batch: what ran and what needs review."""
        outcomes = self._batch_outcomes
        failed = sum(1 for o in outcomes if not o.stopped and not (o.summary and o.summary.ok))
        steps = [step for o in outcomes for step in sc.scenario_steps(self.db, o.scenario_id)]
        labels = ((StepStatus.PASS, "pass"), (StepStatus.FAIL, "bug" if self._batch_smoke else "fail"),
                  (StepStatus.BLOCKED, "blocked"),
                  (StepStatus.NOT_RUN, "not run"))
        counts = {status: sum(1 for s in steps if s.status == status) for status, _ in labels}
        found = ", ".join(f"{counts[status]} {label}" for status, label in labels if counts[status])
        item, unit = ("page", "checks") if self._batch_smoke else ("scenario", "steps")
        ran = f"{len(outcomes)} {item}{'' if len(outcomes) == 1 else 's'}"
        heading = "Run stopped" if stopped else ("Run finished with problems" if failed else "Run finished")
        message = f"{ran}, {len(steps)} {unit}" + (f": {found}" if found else "") + ". Ready for your review."
        if failed:
            message += f" {failed} ended with an error; see the Live run tab."
        return heading, message

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.batch_run is not None:
            answer = QMessageBox.question(self, "Quit", "A test run is in progress. Stop it and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.batch_run.stop()
        self.registry.kill_all()  # on exit, close everything the app started
        self._save_ui_state()
        super().closeEvent(event)


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity, so Windows groups it under our icon, not python.exe's."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    cdp_port = cdp.find_free_port()
    _set_windows_app_id()  # before any window: the taskbar then shows our logo, not Python's
    app = QApplication(argv + cdp.enable(cdp_port))  # must happen before QApplication
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(icons.logo_icon())
    theme.apply(app, app_settings.load(data_dir() / SETTINGS_FILE).theme)
    theme.follow_system(app)
    db = Database(database_path())
    run_store.mark_interrupted_runs(db)  # runs cut off by a crash or kill last time
    # Remove the test password from Claude Code's saved sessions of earlier runs (one-time after
    # the update, afterwards only for runs that could not be cleaned when they ended).
    run_store.clean_saved_sessions(db)
    profile = make_profile(data_dir() / "browser-profile", app)
    guard = NavigationGuard(app)
    profile.setUrlRequestInterceptor(guard)
    window = MainWindow(db, profile, cdp_port, guard=guard)
    window.show()
    window.reopen_last_session()
    try:
        return app.exec()
    finally:
        del window  # pages must go before their profile
        db.close()
