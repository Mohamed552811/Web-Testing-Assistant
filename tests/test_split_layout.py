import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QSplitter, QWidget  # noqa: E402

from wta import launchpad as modes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database  # noqa: E402
from wta.split_layout import BROWSER, WORK, ExpandButton, SplitLayout, layout_buttons  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


class SplitLayoutTests(unittest.TestCase):
    def setUp(self):
        self.splitter = QSplitter()
        self.browser, self.work = QWidget(), QWidget()
        self.splitter.addWidget(self.browser)
        self.splitter.addWidget(self.work)
        self.splitter.resize(1000, 600)
        self.layout = SplitLayout(self.splitter, self.browser, self.work)
        self.splitter.show()
        app.processEvents()

    def tearDown(self):
        self.splitter.deleteLater()
        app.processEvents()

    def assert_halves(self):
        first, second = self.splitter.sizes()
        self.assertLessEqual(abs(first - second), 2)

    def test_starts_side_by_side_half_each(self):
        self.assertEqual(self.layout.order(), [BROWSER, WORK])
        self.assertFalse(self.layout.stacked())
        self.assert_halves()
        self.assertFalse(any(self.splitter.isCollapsible(i) for i in range(2)))  # dragging never hides a side

    def test_drag_size_swap_and_stack(self):
        self.splitter.setSizes([700, 300])
        self.layout.swap()
        self.assertEqual(self.layout.order(), [WORK, BROWSER])
        self.assertAlmostEqual(self.splitter.sizes()[1], 700, delta=5)  # the browser keeps its size on the other side
        self.layout.toggle_stacked()
        self.assertTrue(self.layout.stacked())
        self.assert_halves()
        self.layout.reset()
        self.assertEqual((self.layout.order(), self.layout.stacked()), ([BROWSER, WORK], False))
        self.assert_halves()

    def test_expand_one_side_then_restore(self):
        self.splitter.setSizes([600, 400])
        self.layout.toggle_expanded(WORK)
        self.assertEqual(self.layout.expanded, WORK)
        self.assertTrue(self.browser.isHidden())
        app.processEvents()
        self.assertEqual(self.work.width(), self.splitter.width())  # the work side fills the tab
        self.layout.toggle_expanded(BROWSER)  # straight to the other side
        self.assertTrue(self.work.isHidden() and not self.browser.isHidden())
        self.layout.toggle_expanded(BROWSER)
        self.assertIsNone(self.layout.expanded)
        self.assertFalse(self.browser.isHidden() or self.work.isHidden())
        app.processEvents()
        first, second = self.splitter.sizes()
        self.assertAlmostEqual(first, 600, delta=5)  # back to the sizes before expanding
        self.layout.expand(BROWSER)
        self.layout.reset()  # Reset also restores
        self.assertFalse(self.work.isHidden())
        self.assert_halves()

    def test_buttons_follow_the_layout(self):
        swap, stack, reset, expand_work = layout_buttons(self.layout)
        expand_browser = ExpandButton(self.layout, BROWSER)
        self.assertIn("Expand the browser", expand_browser.toolTip())
        expand_work.click()
        self.assertEqual(expand_work.toolTip(), "Restore both sides")
        self.assertFalse(swap.isEnabled() or stack.isEnabled())  # rearranging waits until both are shown
        expand_work.click()
        self.assertTrue(swap.isEnabled())
        self.assertIn("Stack", stack.toolTip())
        stack.click()
        self.assertIn("side by side", stack.toolTip())
        swap.click()
        self.assertEqual(self.layout.order(), [WORK, BROWSER])
        reset.click()
        self.assertEqual(self.layout.order(), [BROWSER, WORK])
        for button in (swap, stack, reset, expand_work, expand_browser):
            button.deleteLater()


class WindowLayoutTests(unittest.TestCase):
    def test_testing_modes_open_half_each_with_the_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "wta.sqlite3")
            window = MainWindow(db, app_dir=Path(tmp), check_sign_in=False)
            try:
                window.resize(1400, 900)
                window.show()
                window.set_mode(modes.REGRESSION)
                app.processEvents()
                left, right = window.splitter.sizes()
                self.assertLessEqual(abs(left - right), 2)
                self.assertTrue(window.layout_buttons.isVisible())  # over the tab bar, not inside the scroll area
                self.assertIs(window.layout_buttons.parentWidget(), window.splitter.widget(1))
                browser_expand = window.browser.findChildren(ExpandButton)
                self.assertEqual(len(browser_expand), 1)
                browser_expand[0].click()
                self.assertTrue(window.splitter.widget(1).isHidden())
                window.set_mode(modes.SMOKE)  # the same split serves Smoke
                self.assertEqual(window.split_layout.expanded, BROWSER)
                browser_expand[0].click()
                self.assertIsNone(window.split_layout.expanded)
                window.splitter.setSizes([1000, 300])
                window.close()  # the layout is not saved: the next start opens half each again
                again = MainWindow(db, app_dir=Path(tmp), check_sign_in=False)
                again.resize(1400, 900)
                again.show()
                again.set_mode(modes.REGRESSION)
                app.processEvents()
                left, right = again.splitter.sizes()
                self.assertLessEqual(abs(left - right), 2)
                again.deleteLater()
            finally:
                window.deleteLater()
                app.processEvents()
                db.close()


if __name__ == "__main__":
    unittest.main()
