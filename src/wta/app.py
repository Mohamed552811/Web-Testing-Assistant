"""Application entry point and main window."""

import sys
import threading
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, qWebEngineChromiumVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QMenu,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wta import __version__
from wta import accessibility, cdp, compare, cost, fixtures, housekeeping, icons, test_data, theme
from wta import settings as app_settings
from wta import launchpad as modes
from wta.db import designer_settings
from wta.db import workbooks as workbook_store
from wta.designer.settings_dialog import DesignerSettingsDialog
from wta.designer.layout import LOW_CONFIDENCE
from wta.designer.mapping_dialog import MappingDialog
from wta.designer.setup_page import DesignerSetupPage, WorkbookEntry
from wta.designer.workspace import DesignerWorkspace
from wta.designer.summary import summary_html
from wta.welcome import AboutDialog, WelcomePage
from wta.housekeeping_dialog import HousekeepingDialog
from wta.search_dialog import SearchDialog
from wta.setup_check import SetupDialog
from wta.trends_dialog import TrendsDialog
from wta.browser import BrowserPane, make_profile
from wta.connection_dialog import ConnectionDialog
from wta.domain_guard import NavigationGuard
from wta.notify import Notifier
from wta.highlight import Highlighter, highlight_path
from wta.page_monitor import PageMonitor
from wta.processes import ProcessRegistry
from wta.scroll import scrollable
from wta.results import ConfirmationRequest
from wta.db import Database, store
from wta.db import runs as run_store
from wta.db import scenarios as sc
from wta.db import site_notes
from wta.db.models import PageIssueKind, StepStatus, TestSession
from wta.paths import data_dir, database_path
from wta.db.models import MAX_SCENARIOS_PER_BATCH, ScenarioKind
from wta.prompt import RunContext, SmokeContext
from wta.run_panel import RunPanel
from wta.runs_tab import RunsTab, local_time
from wta.runner import BatchRun, RunOutcome, ScenarioJob, ScenarioRun, default_runs_dir, run_folder
from wta.scenario_tab import ScenarioTab
from wta.suggest_dialog import SuggestDialog
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
        self.top_bar.setup_checked.connect(self._setup_checked)
        self.setup_dialog: SetupDialog | None = None
        self._setup_shown = False

        self.browser = BrowserPane(self, profile)
        self.browser.view.load(QUrl(START_PAGE))
        self.browser.status_message.connect(self.statusBar().showMessage)
        self.browser.stop_requested.connect(self.stop_run)
        self.browser.print_requested.connect(self._print_requested)
        self.notifier = Notifier(self, APP_TITLE)
        self.page_monitor = PageMonitor(db, self._current_run_id, self)
        self.highlighter = Highlighter(self.browser.view, self.page_monitor, self)
        self._batch_outcomes: list[RunOutcome] = []
        self._batch_smoke = False
        self._batch_regressions = 0

        self.scenario_tab = ScenarioTab(db, self)
        self.scenario_tab.status_message.connect(self.statusBar().showMessage)
        self.scenario_tab.run_requested.connect(lambda batch: self.run_batch(batch, ScenarioKind.SCENARIO))
        self.scenario_tab.suggest_requested.connect(self.suggest_scenarios)
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
        self.tabs.currentChanged.connect(self._size_tabs_by_current)
        self._size_tabs_by_current()

        # Left side: the welcome screen until a session is open, then the browser.
        self.welcome = WelcomePage(self)
        self.welcome.new_requested.connect(self.new_session)
        self.welcome.open_requested.connect(self.open_session)
        self.welcome.session_chosen.connect(self.open_session_by_id)
        self.left = QStackedWidget()
        self.left.addWidget(self.welcome)
        self.left.addWidget(self.browser)
        self.welcome.set_sessions(store.list_sessions(db))

        # The tabs sit in a scroll area so the splitter can narrow them below their ~600px
        # minimum (a scrollbar appears) and the browser can take most of the window.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left)
        splitter.addWidget(scrollable(self.tabs))
        splitter.setCollapsible(1, False)  # dragging far right stops at the scroll area's minimum, not a hidden panel
        splitter.setSizes([850, 750])
        self.splitter = splitter

        # Pages: the Launchpad first, then the mode's workspace (Smoke and Regression share the splitter).
        self.mode: str | None = None
        self.launchpad = modes.Launchpad(self)
        self.launchpad.mode_chosen.connect(self.set_mode)
        self.launchpad.continue_requested.connect(self.continue_last_mode)
        self.designer_setup = DesignerSetupPage(self)
        self.designer_setup.upload_requested.connect(self.upload_workbook)
        self.designer_setup.workspace_requested.connect(self.open_designer_workspace)
        self.designer_setup.workbook_selected.connect(self._show_workbook_summary)
        self.designer_setup.mapping_requested.connect(self.edit_workbook_mapping)
        self.designer_setup.settings_requested.connect(self.edit_designer_settings)
        self.designer_workspace = DesignerWorkspace(db, self.app_dir / "designer-runs", self.app_dir / USAGE_FILE, self)
        self.designer_workspace.generation_blocker = lambda: (
            "" if self.top_bar.signed_in else "Claude Code must be installed and signed in (Help, Check setup).")
        self.designer_workspace.remember = self._remember
        self.designer_workspace.recall = lambda key: self._ui_state().value(key)
        self.designer_workspace.status_message.connect(lambda text: self.statusBar().showMessage(text, 15000))
        self.designer_workspace.change_workbook_requested.connect(
            lambda: self.pages.setCurrentWidget(self.designer_setup))
        self.designer_workspace.settings_requested.connect(self.edit_designer_settings)
        self.pages = QStackedWidget()
        for page in (self.launchpad, self.designer_setup, self.designer_workspace, splitter):
            self.pages.addWidget(page)
        # Like the tabs: only the shown page sets the minimum size, so a wide workspace does not stop the window
        # (and the Launchpad's cards) from getting narrow.
        self.pages.currentChanged.connect(lambda _index: self._size_by_current(self.pages))
        self._size_by_current(self.pages)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self._build_menu()
        self._build_mode_bar()
        self._build_toolbar()
        self._apply_button_icons()
        self._update_title()
        self._update_run_state()
        self._restore_ui_state()
        self._sign_in_known = False
        self.top_bar.sign_in_changed.connect(self._update_mode_badges)
        self.show_launchpad()
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

        tools_menu = self.menuBar().addMenu("&Tools")
        self.search_action = QAction("&Search everything…", self, shortcut=QKeySequence("Ctrl+Shift+F"))
        self.search_action.triggered.connect(self.show_search)
        tools_menu.addAction(self.search_action)
        self.trends_action = QAction("&Trends…", self)
        self.trends_action.triggered.connect(self.show_trends)
        tools_menu.addAction(self.trends_action)
        self.housekeeping_action = QAction("&Clean up and back up…", self)
        self.housekeeping_action.triggered.connect(self.show_housekeeping)
        tools_menu.addAction(self.housekeeping_action)

        help_menu = self.menuBar().addMenu("&Help")
        setup_action = QAction("Check &setup…", self)
        setup_action.triggered.connect(self.show_setup)
        help_menu.addAction(setup_action)
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

    # --- modes and the Launchpad ---------------------------------------------------

    def _build_mode_bar(self) -> None:
        """The mode switcher: current mode with a dropdown of the others, plus Back to Launchpad.
        The same menu is in the menu bar (Alt+M) for the keyboard."""
        menu = QMenu("&Mode", self)
        self.mode_actions: dict[str, QAction] = {}
        for mode, label in modes.MODES.items():
            action = QAction(label, self, checkable=True)
            action.triggered.connect(lambda _checked=False, m=mode: self.set_mode(m))
            icons.set_icon(action, modes.MODE_ICONS[mode])
            menu.addAction(action)
            self.mode_actions[mode] = action
        menu.addSeparator()
        self.launchpad_action = QAction("&Back to Launchpad", self, shortcut=QKeySequence("Ctrl+L"))
        self.launchpad_action.triggered.connect(self.show_launchpad)
        icons.set_icon(self.launchpad_action, "arrow-left")
        menu.addAction(self.launchpad_action)
        self.menuBar().insertMenu(self.menuBar().actions()[0], menu)
        self.mode_menu = menu

        self.mode_button = QToolButton(self)
        self.mode_button.setProperty("modeSwitcher", True)
        self.mode_button.setMenu(menu)
        self.mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mode_button.setToolTip("Switch mode")
        self.mode_button.setAccessibleName("Mode")
        back = QToolButton(self)
        back.setDefaultAction(self.launchpad_action)
        back.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        back.setText("Launchpad")
        bar = QToolBar("Mode", self)
        bar.setObjectName("mode-toolbar")
        bar.setMovable(False)
        bar.setIconSize(QSize(18, 18))
        bar.addWidget(back)
        bar.addWidget(self.mode_button)
        self.addToolBar(bar)
        self.mode_bar = bar

    def show_launchpad(self) -> None:
        self.pages.setCurrentWidget(self.launchpad)
        self.mode_bar.hide()
        self.toolbar.hide()
        self.launchpad.set_last_mode(self.last_mode())
        self._update_mode_badges()
        self.launchpad.cards[modes.DESIGN].setFocus()

    def _remember(self, key: str, value) -> None:
        state = self._ui_state()
        state.setValue(key, value)
        state.sync()

    def last_mode(self) -> str | None:
        mode = self._ui_state().value("mode")
        return mode if mode in modes.MODES else None

    def set_mode(self, mode: str) -> None:
        """Enter a mode. Smoke and Regression open their tab (with the session picker until a session is
        open); Test Case Design opens its setup step."""
        if mode not in modes.MODES:
            return
        self.mode = mode
        state = self._ui_state()
        state.setValue("mode", mode)
        state.sync()
        for key, action in self.mode_actions.items():
            action.setChecked(key == mode)
        self.mode_button.setText(modes.MODES[mode])
        icons.set_icon(self.mode_button, modes.MODE_ICONS[mode], "accent")
        self.mode_bar.show()
        testing = mode in (modes.REGRESSION, modes.SMOKE)
        self.toolbar.setVisible(testing)
        if testing:
            self.pages.setCurrentWidget(self.splitter)
            self.tabs.setCurrentWidget(self.smoke_tab if mode == modes.SMOKE else self.scenario_tab)
        else:
            self._refresh_workbooks()
            self.pages.setCurrentWidget(self.designer_setup)

    def continue_last_mode(self) -> None:
        """Launchpad "Continue where you left off": the last mode, and its last session for Smoke/Regression."""
        mode = self.last_mode()
        if mode is None:
            return
        self.set_mode(mode)
        if mode != modes.DESIGN and self.current_session is None:
            self.reopen_last_session()

    def _update_mode_badges(self, _ready: bool | None = None) -> None:
        """Test Case Design needs Claude Code signed in (it generates with `claude -p`, no API key)."""
        if _ready is not None:
            self._sign_in_known = True
        card = self.launchpad.cards[modes.DESIGN]
        if not self._sign_in_known:
            card.set_badge("Checking setup…", "info")
        elif self.top_bar.signed_in:
            card.set_badge("Ready", "ok")
        else:
            card.set_badge("Setup required", "warn")
        card.setToolTip("" if self.top_bar.signed_in
                        else "Claude Code must be installed and signed in (Help, Check setup).")

    # --- Test Case Design: workbooks ----------------------------------------------------

    def _refresh_workbooks(self, select_id: int | None = None) -> None:
        if select_id is None:
            last = self._ui_state().value("last_workbook_id")
            select_id = int(last) if last is not None and str(last).isdigit() else None
        entries = [WorkbookEntry(w.id, w.file_name,
                                 f"{w.version_label} · uploaded {local_time(w.uploaded_at)} · "
                                 f"{w.sheet_count} sheet(s) · {w.backlog_count} backlog(s)")
                   for w in workbook_store.list_workbooks(self.db)]
        self.designer_setup.set_workbooks(entries, select_id)

    def _show_workbook_summary(self, workbook_id: int) -> None:
        try:
            self.designer_setup.set_summary(summary_html(workbook_store.load_workbook(self.db, workbook_id)))
        except LookupError as exc:
            self.designer_setup.set_summary(f"<p>{exc}</p>")
            return
        state = self._ui_state()
        state.setValue("last_workbook_id", workbook_id)
        state.sync()

    def upload_workbook(self, path: Path) -> None:
        """Analyze an uploaded workbook (spec 3.7), offer earlier hand-set mappings, then show the summary."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = workbook_store.import_workbook(self.db, path)
        except (OSError, ValueError, KeyError, ElementTree.ParseError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Upload workbook", f"Could not read {path.name}:\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if result.already_uploaded:
            self.statusBar().showMessage(f"{path.name} was uploaded before; showing that workbook.", 8000)
        view = workbook_store.load_workbook(self.db, result.workbook_id)
        if result.reusable_mappings:
            names = ", ".join(f"“{name}”" for name in result.reusable_mappings)
            answer = QMessageBox.question(
                self, "Reuse mapping",
                f"This looks like a new version of an earlier workbook. Reuse the mapping you set by hand for {names}?")
            if answer == QMessageBox.StandardButton.Yes:
                for sheet in view.sheets:
                    if sheet.name in result.reusable_mappings:
                        workbook_store.apply_mapping(self.db, sheet.id, result.reusable_mappings[sheet.name])
                view = workbook_store.load_workbook(self.db, result.workbook_id)
        self._refresh_workbooks(result.workbook_id)
        doubtful = [s for s in view.sheets if not s.manual and s.confidence < LOW_CONFIDENCE]
        if doubtful and not result.already_uploaded:
            names = ", ".join(f"“{s.name}”" for s in doubtful)
            answer = QMessageBox.question(
                self, "Check the mapping",
                f"The backlogs on {names} were detected with low confidence. Check the mapping now?")
            if answer == QMessageBox.StandardButton.Yes:
                self.edit_workbook_mapping(result.workbook_id, doubtful[0].id)

    def edit_workbook_mapping(self, workbook_id: int, sheet_id: int | None = None) -> None:
        view = workbook_store.load_workbook(self.db, workbook_id)
        if not view.sheets:
            return
        dialog = MappingDialog(view.sheets, sheet_id, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        sheet = workbook_store.apply_mapping(self.db, dialog.selected_sheet_id(), dialog.result_mapping())
        self._refresh_workbooks(workbook_id)
        self.statusBar().showMessage(f"Sheet “{sheet.name}”: {len(sheet.backlogs)} backlog(s) with the new mapping.", 8000)

    def edit_designer_settings(self) -> None:
        dialog = DesignerSettingsDialog(designer_settings.load(self.db), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            designer_settings.save(self.db, dialog.settings())
        except ValueError as exc:
            QMessageBox.warning(self, "Test Case Design settings", str(exc))
            return
        self.statusBar().showMessage("Test Case Design settings saved.", 5000)

    def open_designer_workspace(self, workbook_id: int) -> None:
        try:
            view = workbook_store.load_workbook(self.db, workbook_id)
        except LookupError as exc:
            QMessageBox.warning(self, "Open workspace", str(exc))
            self._refresh_workbooks()
            return
        self.designer_workspace.set_workbook(workbook_id, f"{view.file_name} ({view.version_label})")
        self.pages.setCurrentWidget(self.designer_workspace)

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
                                       (tab.site_note_button, "file-plus", None),
                                       (tab.from_library_button, "folder-open", None),
                                       (tab.suggest_button, "message-square", None),
                                       (tab.save_library_button, "download", None)):
                icons.set_icon(button, name, role)
        icons.set_icon(self.run_panel.stop_button, "square", "on-accent")
        self.browser.banner.icon.setPixmap(icons.icon("bot", "#ffffff").pixmap(20, 20))
        icons.set_icon(self.browser.banner.stop_button, "square", "on-accent")
        icons.set_icon(self.runs_tab.open_folder_button, "folder")
        icons.set_icon(self.runs_tab.copy_resume_button, "copy")
        icons.set_icon(self.runs_tab.refresh_button, "rotate-cw")
        # Close all browsers now lives in the toolbar; the top bar keeps only status and usage.
        self.top_bar.close_all_button.hide()

    def _size_tabs_by_current(self, _index: int = -1) -> None:
        """Size the tab area by the shown tab only (hidden pages get Ignored), so the tall Runs
        tab does not make Scenarios scroll as a whole; each section's list scrolls on its own."""
        self._size_by_current(self.tabs)

    @staticmethod
    def _size_by_current(stack) -> None:
        """Hidden pages of a tab or stacked widget get the Ignored size policy, so they count as 0."""
        current = stack.currentWidget()
        for i in range(stack.count()):
            page = stack.widget(i)
            policy = QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored
            page.setSizePolicy(policy, policy)
        stack.updateGeometry()

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
        if self.mode not in (modes.REGRESSION, modes.SMOKE):  # sessions live in the testing modes
            self.set_mode(modes.REGRESSION)
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
            return "Claude Code must be installed and signed in (Help, Check setup)."
        if not self.top_bar.node_ok:
            return "Node.js 18 or later must be installed for the browser tools (Help, Check setup)."
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
        passwords = store.session_passwords(session)
        fixture_files, skipped = fixtures.list_files(session.fixtures_dir)
        if skipped:
            self.statusBar().showMessage("Not offered for uploads: " + "; ".join(skipped), 15000)

        def account(scenario):
            return store.account_for(session, scenario.account_id)

        # {{variables}}: fresh values for each scenario's run, the same everywhere within it.
        fillers = {scenario.id: test_data.Filler() for scenario, _rerun in selected}

        def smoke_context(scenario):
            page = vars(sc.SmokePageInput.of(scenario.smoke_page))
            fill = fillers[scenario.id].fill
            return SmokeContext(**{k: fill(v) if isinstance(v, str) and k != "url" else v for k, v in page.items()})

        jobs = [
            ScenarioJob(
                scenario_id=scenario.id,
                title=(f"Smoke: {scenario.name}" if scenario.smoke_page else scenario.name),
                run_dir=run_folder(runs_dir, session.id, batch, scenario.position, smoke=scenario.smoke_page is not None),
                model=self.settings.smoke_model if scenario.smoke_page else self.settings.scenario_model,
                secrets=tuple(p for p in passwords if p != account(scenario).password),
                fixtures_dir=session.fixtures_dir or "",
                context=RunContext(
                    session_name=session.name,
                    target_url=session.target_url,
                    allowed_domains=list(session.allowed_domains),
                    test_account_email=account(scenario).email,
                    test_account_password=account(scenario).password,
                    test_account_role="" if scenario.account_id is None else account(scenario).role,
                    fixture_files=fixture_files,
                    accessibility_checks=bool(scenario.smoke_page) and self.settings.smoke_accessibility,
                    scenario_name=scenario.name,
                    scenario_steps=fillers[scenario.id].fill(scenario.test_steps or ""),
                    scenario_expected_result=fillers[scenario.id].fill(scenario.expected_result or ""),
                    scenario_test_data=fillers[scenario.id].fill(scenario.test_data),
                    test_data_values=fillers[scenario.id].values,
                    site_notes=self._site_notes_for(scenario.smoke_page.url if scenario.smoke_page else session.target_url),
                    confirm_risky_actions=self.settings.confirm_risky_actions,
                    screenshot_timing=self.settings.screenshot_timing,
                    smoke=smoke_context(scenario) if scenario.smoke_page else None,
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
        self.batch_run.highlight_requested.connect(self._highlight_step)
        for tab in self.review_tabs:
            self.batch_run.results_changed.connect(tab.refresh)
        self.batch_run.usage_changed.connect(lambda _state: self.top_bar.refresh_usage())
        self.batch_run.scenario_finished.connect(self._scenario_finished)
        self.batch_run.all_finished.connect(self._batch_finished)
        self.run_panel.clear()
        self.run_panel.set_running(True)
        self.browser.set_controlled(True)
        if self.cdp_port is not None:
            self.page_monitor.attach(self.cdp_port, self.browser.view.page().devToolsId())
        self.notifier.run_started()
        self._batch_outcomes = []
        self._batch_regressions = 0
        self._batch_smoke = kind == ScenarioKind.SMOKE
        self.tabs.setCurrentWidget(self.run_panel)
        self._update_run_state()
        if estimate is not None:
            self.run_panel.summary.appendPlainText(f"Estimated plan usage for this batch: {estimate.text()}.\n")
        self.batch_run.start()

    def show_search(self) -> None:
        dialog = SearchDialog(self.db, self)
        dialog.hit_chosen.connect(self.open_hit)
        dialog.exec()

    def open_hit(self, hit) -> None:
        """Open a search result: its session, tab, batch, scenario and step."""
        if hit.session_id is None:
            if hit.site:
                self.tabs.setCurrentWidget(self.runs_tab)
                self.statusBar().showMessage(f"Site notes for {hit.site} are on the Runs tab of a session on "
                                             "that site.", 8000)
            return
        if self.batch_run is not None and (self.current_session is None or self.current_session.id != hit.session_id):
            QMessageBox.information(self, "Search", "A test run is in progress. Open other sessions after it ends.")
            return
        if self.current_session is None or self.current_session.id != hit.session_id:
            self.open_session_by_id(hit.session_id)
        if hit.scenario_id is None:
            return
        tab = self.smoke_tab if sc.scenario_kind(self.db, hit.scenario_id) == ScenarioKind.SMOKE else self.scenario_tab
        self.tabs.setCurrentWidget(tab)
        tab.show_scenario(hit.scenario_id, hit.step_id)

    def show_setup(self) -> None:
        """What is installed and signed in, and how to fix what is not."""
        if self.setup_dialog is None:
            self.setup_dialog = SetupDialog(self.top_bar.claude_status, self.top_bar.node_status, self)
            self.setup_dialog.check_again.connect(self.top_bar.check_sign_in)
        self.setup_dialog.show_status(self.top_bar.claude_status, self.top_bar.node_status)
        self.setup_dialog.show()
        self.setup_dialog.raise_()

    def _setup_checked(self, claude, node) -> None:
        if self.setup_dialog is not None:
            self.setup_dialog.show_status(claude, node)
        if not (claude.ok and node.ok) and not self._setup_shown:
            self._setup_shown = True  # on its own only once per start; Help, Check setup any time
            self.show_setup()

    def show_trends(self) -> None:
        site = site_notes.site_for_url(self.current_session.target_url) if self.current_session else ""
        TrendsDialog(self.db, site, self).exec()

    def show_housekeeping(self) -> None:
        HousekeepingDialog(self.db, default_runs_dir(self.app_dir), self.app_dir, self).exec()
        self.runs_tab.refresh()

    def suggest_scenarios(self) -> None:
        tab = self.scenario_tab
        if tab.session_id is None:
            return
        room = MAX_SCENARIOS_PER_BATCH - tab.scenario_list.count()

        def page_text(callback) -> None:
            url = self.browser.view.url().toString()
            self.browser.view.page().toPlainText(lambda text: callback(text or "", url))

        dialog = SuggestDialog(self.app_dir / "suggest", self.app_dir / USAGE_FILE, self.settings.scenario_model,
                               room, page_text, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen():
            try:
                tab.add_suggested(dialog.chosen(), dialog.to_library)
            except sc.BatchFullError as exc:
                QMessageBox.warning(self, "Suggest scenarios", str(exc))
        self.top_bar.refresh_usage()

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
        popups = self.browser.close_popups()
        parts = (["stopped the run"] if stopped else []) + ([f"closed {popups} tab(s) the page opened"] if popups else []) + [
            f"closed {len(tracked)} process(es) the app started",
            "cleared the embedded browser",
        ]
        self.statusBar().showMessage("Close all browsers: " + ", ".join(parts) + ".")

    def _print_requested(self, page) -> None:
        """During a run, save what the page would print as print-N.pdf in the run folder, for Claude to read."""
        run = self.batch_run.current if self.batch_run is not None else None
        if run is None:
            self.statusBar().showMessage("The page asked to print. Printing is only captured during a test run.", 8000)
            return
        number = 1
        while (run.job.run_dir / f"print-{number}.pdf").exists():
            number += 1
        path = run.job.run_dir / f"print-{number}.pdf"
        page.printToPdf(str(path))
        run.add_note(f"App: the page asked to print; saved as {path.name} in the run folder.")

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

    def _highlight_step(self, scenario_id: int, order: int, text: str, run_dir) -> None:
        """Box the failing element of a Bug / Blocked step in the app's own screenshot."""
        run = self.batch_run.current if self.batch_run is not None else None

        def done(path) -> None:
            if path is None:
                if run is not None:
                    run.add_note(f"App: could not find “{text}” on the page to mark step {order}.")
                return
            if sc.add_highlight_screenshot(self.db, scenario_id, order, path):
                for tab in self.review_tabs:
                    tab.refresh(scenario_id)
                if run is not None:
                    run.add_note(f"App: marked “{text}” for step {order} in {path.name}.")

        self.highlighter.highlight(text, highlight_path(Path(run_dir), order), done)

    def _current_run_id(self) -> int | None:
        current = self.batch_run.current if self.batch_run is not None else None
        return current.run_id if current is not None else None

    def _check_contrast(self, run_id: int) -> None:
        """After a smoke run: measure text contrast on the page, in an isolated script world."""
        def done(result) -> None:
            issues = accessibility.contrast_issues(result)
            if issues:
                run_store.add_page_findings(self.db, run_id, issues, [])
                self.runs_tab.refresh()
        self.browser.view.page().runJavaScript(accessibility.CONTRAST_JS, QWebEngineScript.ScriptWorldId.ApplicationWorld,
                                               done)

    def _page_summary(self, run_id: int) -> str:
        findings = run_store.page_findings(self.db, run_id)
        parts = [f"{findings.count(kind)} {label}" for kind, label in (
            (PageIssueKind.CONSOLE, "console errors/warnings"), (PageIssueKind.EXCEPTION, "uncaught errors"),
            (PageIssueKind.REQUEST, "failed requests")) if findings.count(kind)]
        if findings.slow_loads:
            parts.append(f"{len(findings.slow_loads)} slow page load(s)")
        return ("  Seen by the app on the page: " + ", ".join(parts) + " (details on the Runs tab)") if parts else ""

    def _scenario_finished(self, outcome: RunOutcome) -> None:
        title = next((j.title for j in self.batch_run.jobs if j.scenario_id == outcome.scenario_id), "Scenario")
        self.run_panel.scenario_finished(outcome, title)
        run_id = self._current_run_id()
        self.page_monitor.flush()
        if run_id is not None:
            summary = self._page_summary(run_id)
            if summary:
                self.run_panel.summary.appendPlainText(summary + "\n")
            previous, comparison = compare.compare_with_previous(self.db, run_id)
            if comparison is not None:
                self._batch_regressions += comparison.count(compare.REGRESSION)
                self.run_panel.summary.appendPlainText(
                    f"  Since the previous run ({local_time(previous.started_at)}): {comparison.summary()}"
                    " (Runs tab, Compare with previous run)\n")
            job = next((j for j in self.batch_run.jobs if j.scenario_id == outcome.scenario_id), None)
            if job is not None and job.context.accessibility_checks:
                self._check_contrast(run_id)
        self._batch_outcomes.append(outcome)
        for tab in self.review_tabs:
            tab.refresh(outcome.scenario_id)
        self.runs_tab.refresh()

    def _batch_finished(self) -> None:
        if self.guard is not None:
            self.guard.deactivate()
        stopped = self.batch_run.stopped
        self.page_monitor.detach()
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
        if self._batch_regressions:
            message += f" {self._batch_regressions} step(s) failed that passed in the previous run."
        return heading, message

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.batch_run is not None:
            answer = QMessageBox.question(self, "Quit", "A test run is in progress. Stop it and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.batch_run.stop()
        self.registry.kill_all()  # on exit, close everything the app started
        self.browser.close_popups()
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
    if "--self-check" in argv:  # check an installed build without opening the window (see self_check.py)
        index = argv.index("--self-check")
        report = Path(argv[index + 1]) if index + 1 < len(argv) else data_dir() / "self-check.json"
        from wta import self_check
        check_app = QApplication(argv[:1])
        return self_check.run(report, check_app)
    cdp_port = cdp.find_free_port()
    _set_windows_app_id()  # before any window: the taskbar then shows our logo, not Python's
    app = QApplication(argv + cdp.enable(cdp_port))  # must happen before QApplication
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(icons.logo_icon())
    theme.apply(app, app_settings.load(data_dir() / SETTINGS_FILE).theme)
    theme.follow_system(app)
    housekeeping.apply_pending_restore(data_dir(), database_path())  # a restore chosen last time
    db = Database(database_path())
    run_store.mark_interrupted_runs(db)  # runs cut off by a crash or kill last time
    # Remove the test password from Claude Code's saved sessions of earlier runs (one-time after
    # the update, afterwards only for runs that could not be cleaned when they ended).
    run_store.clean_saved_sessions(db)
    run_store.backfill_run_steps(db)  # keep the history of runs from before Phase 17
    profile = make_profile(data_dir() / "browser-profile", app)
    guard = NavigationGuard(app)
    profile.setUrlRequestInterceptor(guard)
    window = MainWindow(db, profile, cdp_port, guard=guard)
    window.show()  # opens on the Launchpad; "Continue where you left off" reopens the last session
    try:
        return app.exec()
    finally:
        del window  # pages must go before their profile
        db.close()
