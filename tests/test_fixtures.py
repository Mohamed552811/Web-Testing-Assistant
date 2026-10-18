"""Phase 16: uploads from the session's fixtures folder."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import claude_launcher, fixtures  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402
from wta.runner import ScenarioJob, ScenarioRun, run_folder  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


class ListAndCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "files"
        (self.root / "invoices").mkdir(parents=True)
        (self.root / "a.pdf").write_bytes(b"%PDF-1.4 test")
        (self.root / "invoices" / "b.csv").write_text("x,y\n1,2\n")
        (self.root / ".secret").write_text("hidden")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_skips_hidden_and_large_files(self):
        self.assertEqual(fixtures.list_files(None), ([], []))
        with mock.patch.object(fixtures, "MAX_FILE_BYTES", 10):
            offered, skipped = fixtures.list_files(self.root)
        self.assertEqual(offered, ["invoices/b.csv"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("a.pdf", skipped[0])
        self.assertEqual(fixtures.list_files(self.root)[0], ["a.pdf", "invoices/b.csv"])
        self.assertIn("missing", fixtures.list_files(self.root / "nope")[1][0])

    def test_copy_into_run_folder(self):
        run_dir = Path(self.tmp.name) / "run"
        target = fixtures.copy_into_run(self.root, ["a.pdf", "invoices/b.csv"], run_dir)
        self.assertEqual(target, run_dir / "fixtures")
        self.assertEqual((target / "invoices" / "b.csv").read_text(), "x,y\n1,2\n")
        self.assertFalse((target / ".secret").exists())


class LauncherAndPromptTests(unittest.TestCase):
    def test_upload_tool_only_with_fixtures(self):
        def tools(args, flag):
            return args[args.index(flag) + 1].split(",")
        closed = claude_launcher.run_args(Path("m.json"))
        self.assertNotIn(claude_launcher.UPLOAD_TOOL, tools(closed, "--allowedTools"))
        self.assertIn(claude_launcher.UPLOAD_TOOL, tools(closed, "--disallowedTools"))
        opened = claude_launcher.run_args(Path("m.json"), allow_upload=True)
        self.assertIn(claude_launcher.UPLOAD_TOOL, tools(opened, "--allowedTools"))
        self.assertNotIn(claude_launcher.UPLOAD_TOOL, tools(opened, "--disallowedTools"))
        self.assertIn("mcp__playwright__browser_evaluate", tools(opened, "--disallowedTools"))  # still denied

    def test_prompt_lists_the_files(self):
        ctx = RunContext("S", "https://a.test", ["a.test"], "", "", "N", "Attach the invoice", "",
                         fixture_files=["a.pdf", "invoices/b.csv"])
        prompt = build_prompt(ctx)
        self.assertIn("- fixtures/a.pdf\n- fixtures/invoices/b.csv", prompt)
        ctx.fixture_files = []
        self.assertIn("No files are available for uploads", build_prompt(ctx))


class RunnerTests(unittest.TestCase):
    def test_files_are_copied_and_upload_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "files").mkdir()
            (base / "files" / "a.pdf").write_bytes(b"%PDF")
            db = Database(base / "wta.sqlite3")
            session = store.create_session(db, store.SessionDetails(name="S", target_url="http://127.0.0.1:1/"))
            scenario = sc.add_scenario(db, session.id, 1, sc.ScenarioInput(name="Login", steps="Log in"))
            job = ScenarioJob(scenario.id, "Login",
                              RunContext("S", "http://127.0.0.1:1/", ["127.0.0.1"], "qa@x.test", "pw", "Login",
                                         "Log in", "", fixture_files=["a.pdf"]),
                              run_folder(base / "runs", session.id, 1, 1), fixtures_dir=str(base / "files"))
            run = ScenarioRun(db, job, 1, base / "usage.json")
            loop = QEventLoop()
            run.finished.connect(lambda _outcome: loop.quit())
            QTimer.singleShot(30000, loop.quit)
            with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE), \
                    mock.patch.object(claude_launcher, "run_args", wraps=claude_launcher.run_args) as run_args:
                run.start()
                loop.exec()
            self.assertEqual((job.run_dir / "fixtures" / "a.pdf").read_bytes(), b"%PDF")
            self.assertTrue(run_args.call_args.kwargs["allow_upload"])
            db.close()


if __name__ == "__main__":
    unittest.main()
