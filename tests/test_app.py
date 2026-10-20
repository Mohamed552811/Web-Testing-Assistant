import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from wta.settings_dialog import SettingsDialog  # noqa: E402

from wta import processes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


class MainWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_builds_and_applies_session_viewport(self):
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        self.assertFalse(window.connection_action.isEnabled())  # no debugging port given
        session = store.create_session(
            self.db,
            store.SessionDetails(name="S", target_url="about-blank.test", viewport_width=1024, viewport_height=600),
        )
        window.show_session(session)
        self.assertEqual(window.browser.view.size().toTuple(), (1024, 600))
        self.assertIn("S", window.windowTitle())
        # Sign-in check skipped and no debug port: running is blocked, with the reason shown.
        self.assertFalse(window.scenario_tab.run_button.isEnabled())
        self.assertIn("signed in", window.scenario_tab.run_button.toolTip())
        window.deleteLater()

    def test_browser_can_take_most_of_the_window(self):
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        window.resize(1200, 800)
        window.show()
        window.show_session(store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com")))
        window.splitter.setSizes([5000, 1])
        app.processEvents()
        browser_width, tabs_width = window.splitter.sizes()
        self.assertGreater(browser_width, 900)  # was capped near half: the tabs held ~600px
        self.assertGreater(tabs_width, 0)  # narrowed, not collapsed away
        # The tall Runs tab no longer sets the height of the others, so Scenarios does not scroll as a whole.
        self.assertLess(window.tabs.minimumSizeHint().height(), window.runs_tab.minimumSizeHint().height())
        self.assertFalse(window.work_scroll.verticalScrollBar().isVisible())
        window.deleteLater()

    def test_export_uses_file_extension_or_default_format(self):
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        self.assertFalse(window.export_action.isEnabled())  # no session yet
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com"))
        window.show_session(session)
        self.assertTrue(window.export_action.isEnabled())
        out = Path(self.tmp.name) / "out"
        for fmt in ("xlsx", "pdf", "docx"):
            self.assertEqual(window.export_report(out / f"r.{fmt}"), out / f"r.{fmt}")
            self.assertTrue((out / f"r.{fmt}").is_file(), fmt)
        # Markdown writes a folder named after the file and returns its SUMMARY.md.
        self.assertEqual(window.export_report(out / "r.md"), out / "r" / "SUMMARY.md")
        window.settings.export_format = "md"
        self.assertEqual(window.export_report(out / "no-extension"), out / "no-extension" / "SUMMARY.md")
        window.deleteLater()

    def test_export_from_the_menu(self):
        """The real path a user takes: Session > Export report… (triggered passes `checked`)."""
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        window.show_session(store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com")))
        target = Path(self.tmp.name) / "menu" / "report.xlsx"
        target.parent.mkdir()
        shown = []
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "Excel (.xlsx) (*.xlsx)")) as ask, \
                mock.patch.object(window, "_show_exported", side_effect=shown.append):
            window.export_action.trigger()
        ask.assert_called_once()
        self.assertTrue(target.is_file())
        self.assertEqual(shown, [target])
        # Settings… from the menu works the same way.
        with mock.patch.object(SettingsDialog, "exec", return_value=0) as settings_exec:
            window.settings_action.trigger()
        settings_exec.assert_called_once()
        window.deleteLater()

    def test_closing_the_app_closes_tracked_processes(self):
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            window.registry.track(child.pid, "test process")
            window.close()
            child.wait(timeout=10)
            self.assertIsNone(processes.create_time(child.pid))
            self.assertEqual(window.registry.entries, {})
        finally:
            child.kill()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
