"""Phase 16: new tabs and pop-ups open in their own window; print requests are saved as PDF during a run."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta.app import MainWindow  # noqa: E402
from wta.db import Database  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
PAGE = """<html><body><h1>Orders</h1>
<a id="doc" href="about:blank" target="_blank">Open document</a></body></html>"""


def wait_until(condition, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.window = MainWindow(self.db, app_dir=self.base, check_sign_in=False)
        self.browser = self.window.browser
        loaded = []
        self.browser.view.loadFinished.connect(loaded.append)
        self.browser.view.setHtml(PAGE, QUrl("http://127.0.0.1/"))
        self.assertTrue(wait_until(lambda: loaded), "page did not load")

    def tearDown(self):
        self.browser.close_popups()
        app.processEvents()
        self.window.deleteLater()
        app.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def run_js(self, page, script):
        page.runJavaScript(script)

    def test_new_tab_opens_a_popup_that_the_page_can_close(self):
        self.run_js(self.browser.view.page(), "window.open('about:blank', '_blank')")
        self.assertTrue(wait_until(lambda: len(self.browser.popups) == 1), "no pop-up window")
        popup = self.browser.popups[0]
        self.assertIs(popup.parent(), self.window)  # closed together with the app
        self.run_js(popup.view.page(), "window.close()")
        self.assertTrue(wait_until(lambda: not self.browser.popups), "pop-up was not closed")

    def test_close_all_browsers_closes_popups(self):
        self.run_js(self.browser.view.page(), "window.open('about:blank'); window.open('about:blank')")
        self.assertTrue(wait_until(lambda: len(self.browser.popups) == 2))
        self.window.close_all_browsers()
        self.assertTrue(wait_until(lambda: not self.browser.popups))
        self.assertIn("closed 2 tab(s) the page opened", self.window.statusBar().currentMessage())

    def test_print_is_saved_as_pdf_during_a_run(self):
        notes = []
        run_dir = self.base / "run"
        run_dir.mkdir()
        run = SimpleNamespace(job=SimpleNamespace(run_dir=run_dir), add_note=notes.append)
        self.window.batch_run = SimpleNamespace(current=run)
        try:
            self.run_js(self.browser.view.page(), "window.print()")
            self.assertTrue(wait_until(lambda: (run_dir / "print-1.pdf").exists()
                                       and (run_dir / "print-1.pdf").stat().st_size > 0), "no PDF")
        finally:
            self.window.batch_run = None
        self.assertTrue((run_dir / "print-1.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(notes, ["App: the page asked to print; saved as print-1.pdf in the run folder."])

    def test_print_outside_a_run_is_not_captured(self):
        self.run_js(self.browser.view.page(), "window.print()")
        self.assertTrue(wait_until(lambda: "only captured during a test run"
                                   in self.window.statusBar().currentMessage()))


class PromptTests(unittest.TestCase):
    def test_prompt_explains_tabs_and_print(self):
        prompt = build_prompt(RunContext("S", "https://a.test", ["a.test"], "", "", "N", "Open", ""))
        self.assertIn("switch to it with the tabs tool", prompt)
        self.assertIn("print-1.pdf", prompt)
        self.assertNotIn("Do not open new tabs or windows", prompt)


if __name__ == "__main__":
    unittest.main()
