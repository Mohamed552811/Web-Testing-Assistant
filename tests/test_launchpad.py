import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from unittest import mock  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from wta import launchpad as modes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import workbooks  # noqa: E402
from wta.designer.setup_page import WorkbookEntry  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from designer_xlsx import SheetSpec, build  # noqa: E402
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import SheetSpec, build  # noqa: E402
    from tests.qt_app import app  # noqa: E402


class LaunchpadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.deleteLater()
        app.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def window(self) -> MainWindow:
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        window.resize(1400, 900)
        window.show()
        self.windows.append(window)
        return window

    def test_each_testing_mode_shows_only_its_own_review_tab(self):
        window = self.window()
        tabs = window.tabs
        visible = lambda: [tabs.tabText(i) for i in range(tabs.count()) if tabs.isTabVisible(i)]  # noqa: E731
        window.set_mode(modes.REGRESSION)
        self.assertEqual(visible(), ["Scenarios", "Live run", "Runs", "Checklists"])
        self.assertIs(tabs.currentWidget(), window.scenario_tab)
        window.set_mode(modes.SMOKE)
        self.assertEqual(visible(), ["Smoke test", "Live run", "Runs", "Checklists"])
        self.assertIs(tabs.currentWidget(), window.smoke_tab)
        self.assertIs(window.mode_tab(), window.smoke_tab)
        window.set_mode(modes.REGRESSION)
        self.assertIs(window.mode_tab(), window.scenario_tab)

    def test_session_and_browser_actions_are_only_on_the_testing_toolbar(self):
        window = self.window()
        menus = [a.text() for a in window.menuBar().actions()]
        self.assertNotIn("&Session", menus)
        self.assertNotIn("&Browser", menus)
        self.assertEqual(menus[0], "&Mode")
        self.assertIn(window.quit_action, window.mode_menu.actions())
        on_toolbar = window.toolbar.actions()
        for action in (window.new_action, window.open_action, window.details_action, window.run_action,
                       window.stop_action, window.export_action, window.settings_action, window.connection_action,
                       window.close_all_action):
            self.assertIn(action, on_toolbar, action.text())
        self.assertFalse(window.toolbar.isVisible())  # on the Launchpad
        window.set_mode(modes.DESIGN)
        self.assertFalse(window.toolbar.isVisible())
        window.set_mode(modes.SMOKE)
        self.assertTrue(window.toolbar.isVisible())

    def test_opens_on_the_launchpad_with_three_cards(self):
        window = self.window()
        self.assertIs(window.pages.currentWidget(), window.launchpad)
        self.assertEqual([c.title_label.text() for c in window.launchpad.cards.values()],
                         ["Test Case Design", "Regression Testing", "Smoke Testing"])
        for card in window.launchpad.cards.values():
            self.assertTrue(card.focusPolicy() & Qt.FocusPolicy.TabFocus)  # keyboard reachable
            self.assertTrue(card.description_label.text())
        self.assertFalse(window.mode_bar.isVisible())
        self.assertFalse(window.toolbar.isVisible())
        self.assertFalse(window.launchpad.continue_button.isVisible())  # nothing used yet

    def test_cards_open_their_mode(self):
        window = self.window()
        QTest.keyClick(window.launchpad.cards[modes.SMOKE], Qt.Key.Key_Space)  # keyboard activation
        self.assertIs(window.pages.currentWidget(), window.splitter)
        self.assertIs(window.tabs.currentWidget(), window.smoke_tab)
        self.assertIs(window.left.currentWidget(), window.welcome)  # choose a session first
        self.assertTrue(window.mode_bar.isVisible() and window.toolbar.isVisible())
        self.assertEqual(window.mode_button.text(), "Smoke Testing")

        window.mode_actions[modes.REGRESSION].trigger()  # the switcher's dropdown
        self.assertIs(window.tabs.currentWidget(), window.scenario_tab)
        self.assertTrue(window.mode_actions[modes.REGRESSION].isChecked())
        self.assertFalse(window.mode_actions[modes.SMOKE].isChecked())

        window.mode_actions[modes.DESIGN].trigger()
        self.assertIs(window.pages.currentWidget(), window.designer_setup)
        self.assertFalse(window.toolbar.isVisible())  # session/run actions belong to the testing modes
        self.assertTrue(window.mode_bar.isVisible())

        window.launchpad_action.trigger()
        self.assertIs(window.pages.currentWidget(), window.launchpad)
        self.assertFalse(window.mode_bar.isVisible())

    def test_continue_where_you_left_off(self):
        first = self.window()
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com"))
        first.set_mode(modes.SMOKE)
        first.show_session(session)
        first._save_ui_state()

        second = self.window()
        self.assertIs(second.pages.currentWidget(), second.launchpad)  # the Launchpad always opens first
        self.assertTrue(second.launchpad.continue_button.isVisible())
        self.assertIn("Smoke Testing", second.launchpad.continue_button.text())
        second.launchpad.continue_button.click()
        self.assertEqual(second.mode, modes.SMOKE)
        self.assertIs(second.tabs.currentWidget(), second.smoke_tab)
        self.assertEqual(second.current_session.id, session.id)
        self.assertIs(second.left.currentWidget(), second.browser)

    def test_opening_a_session_from_the_launchpad_enters_regression(self):
        window = self.window()
        window.show_session(store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com")))
        self.assertEqual(window.mode, modes.REGRESSION)
        self.assertIs(window.pages.currentWidget(), window.splitter)

    def test_design_badge_follows_claude_sign_in(self):
        window = self.window()
        badge = window.launchpad.cards[modes.DESIGN].badge
        self.assertEqual(badge.text(), "Checking setup…")
        window.top_bar.signed_in = False
        window.top_bar.sign_in_changed.emit(False)
        self.assertEqual((badge.text(), badge.property("badge")), ("Setup required", "warn"))
        window.top_bar.signed_in = True
        window.top_bar.sign_in_changed.emit(True)
        self.assertEqual((badge.text(), badge.property("badge")), ("Ready", "ok"))
        # Smoke and Regression have no open-run count to show (their runs are not left open).
        self.assertFalse(window.launchpad.cards[modes.SMOKE].badge.isVisible())

    def test_cards_stack_on_narrow_windows(self):
        window = self.window()
        cards = window.launchpad.cards
        app.processEvents()
        self.assertEqual(cards[modes.DESIGN].y(), cards[modes.SMOKE].y())  # side by side
        window.resize(600, 900)
        app.processEvents()
        self.assertLess(cards[modes.DESIGN].y(), cards[modes.REGRESSION].y())
        self.assertLess(cards[modes.REGRESSION].y(), cards[modes.SMOKE].y())

    def test_setup_step_enters_the_workspace(self):
        window = self.window()
        window.set_mode(modes.DESIGN)
        setup = window.designer_setup
        self.assertFalse(setup.enter_button.isEnabled())
        self.assertTrue(setup.empty_label.isVisible())
        ids = []
        for name in ("Print designer 6.xlsx", "Other.xlsx"):
            path = Path(self.tmp.name) / name
            path.write_bytes(build([SheetSpec("S", {"A1": 1, "B1": name, "A3": 2, "B3": "Second"})]))
            ids.append(workbooks.import_workbook(self.db, path).workbook_id)
        selected = []
        setup.workbook_selected.connect(selected.append)
        setup.set_workbooks([WorkbookEntry(ids[0], "Print designer 6.xlsx", "2 sheets"), WorkbookEntry(ids[1], "Other.xlsx")],
                            select_id=ids[1])
        self.assertEqual(selected, [ids[1]])
        self.assertTrue(setup.enter_button.isEnabled())
        setup.workbooks.setCurrentRow(0)
        setup.enter_button.click()
        self.assertIs(window.pages.currentWidget(), window.designer_workspace)
        self.assertEqual(window.designer_workspace.workbook_id, ids[0])
        self.assertIn("Print designer 6.xlsx", window.designer_workspace.title.text())
        window.designer_workspace.change_button.click()
        self.assertIs(window.pages.currentWidget(), setup)

    def test_missing_workbook_is_reported(self):
        window = self.window()
        window.set_mode(modes.DESIGN)
        with mock.patch.object(QMessageBox, "warning") as warn:
            window.open_designer_workspace(999)
        warn.assert_called_once()
        self.assertIs(window.pages.currentWidget(), window.designer_setup)


if __name__ == "__main__":
    unittest.main()
