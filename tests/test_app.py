import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
