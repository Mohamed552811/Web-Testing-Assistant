import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import processes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402

app = QApplication.instance() or QApplication([])


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

    def test_export_uses_file_extension_or_default_format(self):
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        self.assertFalse(window.export_action.isEnabled())  # no session yet
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.example.com"))
        window.show_session(session)
        self.assertTrue(window.export_action.isEnabled())
        out = Path(self.tmp.name) / "out"
        for fmt in ("xlsx", "pdf", "docx", "md"):
            self.assertEqual(window.export_report(out / f"r.{fmt}"), out / f"r.{fmt}")
            self.assertTrue((out / f"r.{fmt}").is_file(), fmt)
        window.settings.export_format = "md"
        self.assertEqual(window.export_report(out / "no-extension"), out / "no-extension.md")
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
