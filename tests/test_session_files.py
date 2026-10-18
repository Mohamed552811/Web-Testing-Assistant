import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import claude_launcher, session_files  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import runs as run_store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import RunOutcomeKind  # noqa: E402
from wta.prompt import RunContext  # noqa: E402
from wta.runner import BatchRun, ScenarioJob, run_folder  # noqa: E402
from wta.runs_tab import RunsTab  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
PASSWORD = 'Pw"with\\odd-chars-é'


class SessionFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name) / "projects"
        self.run_dir = Path(r"C:\Users\me\AppData\Roaming\Web Testing Assistant\runs\session-1\batch-1\scenario-1-20261012-171802")
        self.folder = self.projects / session_files.project_folder_name(self.run_dir)

    def tearDown(self):
        # Through the long-path form: ordinary removal fails on paths over 260 characters.
        shutil.rmtree(session_files._fs(Path(self.tmp.name)), ignore_errors=True)
        self.tmp.cleanup()

    def test_folder_name_matches_claude_code(self):
        # The name Claude Code used for a real run folder (checked on this machine).
        self.assertEqual(
            session_files.project_folder_name(self.run_dir),
            "C--Users-me-AppData-Roaming-Web-Testing-Assistant-runs-session-1-batch-1-scenario-1-20261012-171802",
        )

    def test_finds_all_files_of_the_run_only(self):
        (self.folder / "abc" / "tool-results").mkdir(parents=True)
        (self.folder / "abc.jsonl").write_text("x")
        (self.folder / "abc" / "tool-results" / "big.txt").write_text("y")
        other = self.projects / "some-other-project"
        other.mkdir()
        (other / "abc.jsonl").write_text("not ours")
        found = session_files.session_files(self.run_dir, "abc", self.projects)
        self.assertEqual({f.name for f in found}, {"abc.jsonl", "big.txt"})

    def test_fallback_by_session_id_when_folder_name_differs(self):
        renamed = self.projects / "shortened-name-123"
        renamed.mkdir(parents=True)
        (renamed / "sid-1.jsonl").write_text("x")
        (renamed / "other.jsonl").write_text("y")
        self.assertEqual([f.name for f in session_files.session_files(self.run_dir, "sid-1", self.projects)],
                         ["sid-1.jsonl"])
        self.assertEqual(session_files.session_files(self.run_dir, None, self.projects), [])

    def test_redact_plain_and_json_escaped_forms(self):
        self.folder.mkdir(parents=True)
        f = self.folder / "s.jsonl"
        f.write_text(json.dumps({"content": f"Password: {PASSWORD}"}) + "\n" + f"raw {PASSWORD}\n", encoding="utf-8")
        self.assertEqual(session_files.redact_file(f, [PASSWORD]), 2)
        text = f.read_text("utf-8")
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(json.dumps(PASSWORD)[1:-1], text)
        self.assertEqual(text.count(session_files.PLACEHOLDER), 2)
        self.assertEqual(json.loads(text.splitlines()[0])["content"],  # still valid JSON
                         f"Password: {session_files.PLACEHOLDER}")
        self.assertIn("removed after the run", session_files.PLACEHOLDER)  # a resumed Claude can tell what happened
        self.assertEqual([p.name for p in self.folder.iterdir()], ["s.jsonl"])  # no temp file left

    def test_redact_leaves_clean_files_untouched(self):
        self.folder.mkdir(parents=True)
        f = self.folder / "s.jsonl"
        f.write_text("nothing secret")
        before = f.stat().st_mtime_ns
        self.assertEqual(session_files.redact_file(f, [PASSWORD, ""]), 0)
        self.assertEqual(f.stat().st_mtime_ns, before)
        self.assertEqual(session_files.redact_file(f, []), 0)

    def test_clean_run_reports_errors(self):
        self.folder.mkdir(parents=True)
        (self.folder / "s.jsonl").write_text(PASSWORD)
        with mock.patch.object(session_files, "redact_file", side_effect=PermissionError("in use")):
            result = session_files.clean_run(self.run_dir, "s", [PASSWORD], self.projects)
        self.assertEqual((result.files, result.replaced), (1, 0))
        self.assertTrue(result.errors)

    def test_paths_longer_than_260_characters(self):
        """Session folders are named after the whole run path, so real paths pass Windows' 260-character limit."""
        long_run = Path("C:/") / ("x" * 90) / ("y" * 90) / "scenario-1-20261018-154853"
        folder = self.projects / session_files.project_folder_name(long_run)
        f = folder / "8d54436f-6215-44a5-b567-cd653348b908.jsonl"
        self.assertGreater(len(str(f)), 260)  # the whole path is too long for ordinary Windows file calls…
        self.assertLess(len(folder.name), 255)  # …while each name stays within Windows' per-name limit
        session_files._fs(folder).mkdir(parents=True)
        session_files._fs(f).write_text(json.dumps({"content": PASSWORD}), encoding="utf-8")
        found = session_files.session_files(long_run, f.stem, self.projects)
        self.assertEqual([p.name for p in found], [f.name])
        result = session_files.clean_run(long_run, f.stem, [PASSWORD], self.projects)
        self.assertEqual((result.files, result.replaced, result.errors), (1, 1, []))
        self.assertNotIn(json.dumps(PASSWORD)[1:-1], session_files._fs(f).read_text("utf-8"))
        self.assertEqual(session_files.find_session_id(long_run, self.projects), f.stem)

    def test_not_trusted_as_clean_when_the_session_was_not_found(self):
        missing = session_files.clean_run(self.run_dir, "sid-404", [PASSWORD], self.projects)
        self.assertFalse(missing.done("sid-404"))  # Claude reported a session but no file was found: retry later
        self.assertTrue(missing.done(None))  # no session reported (e.g. Claude never started): nothing to clean

    def test_find_session_id(self):
        self.assertIsNone(session_files.find_session_id(self.run_dir, self.projects))
        self.folder.mkdir(parents=True)
        (self.folder / "older.jsonl").write_text("a")
        newest = self.folder / "newest.jsonl"
        newest.write_text("b")
        os.utime(self.folder / "older.jsonl", (1, 1))
        self.assertEqual(session_files.find_session_id(self.run_dir, self.projects), "newest")

    def test_resume_command(self):
        command = claude_launcher.resume_command(Path("C:/runs/run 1"), "sid-1")
        self.assertIn("--resume sid-1", command)
        self.assertIn("--restricted", command)
        self.assertIn("--tools=", command)
        self.assertIn("run 1", command)  # starts in the run's folder


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.config = self.base / "claude-config"
        self.db = Database(self.base / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(
            name="S", target_url="http://127.0.0.1:1/", test_account_email="qa@x.test", test_account_password=PASSWORD))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_run_end_cleans_its_saved_session_and_records_the_id(self):
        job = ScenarioJob(self.scenario.id, "Login",
                          RunContext("S", "http://127.0.0.1:1/", ["127.0.0.1"], "qa@x.test", PASSWORD, "Login", "", ""),
                          run_folder(self.base / "runs", self.session.id, 1, 1))
        batch = BatchRun(self.db, [job], 1, self.base / "usage.json")
        loop = QEventLoop()
        batch.all_finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE), \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config), "FAKE_CLAUDE_MODE": ""}):
            batch.start()
            loop.exec()
            transcript = self.config / "projects" / session_files.project_folder_name(job.run_dir) / "fake-session.jsonl"
        self.assertTrue(transcript.is_file(), "the fake Claude Code did not save a session")
        text = transcript.read_text("utf-8")
        self.assertNotIn(json.dumps(PASSWORD)[1:-1], text)
        self.assertIn(session_files.PLACEHOLDER, text)
        self.assertIn("qa@x.test", text)  # only the password is removed; the history stays
        (run,) = run_store.list_runs(self.db, self.session.id)
        self.assertEqual(run.claude_session_id, "fake-session")
        self.assertIsNotNone(run.transcript_cleaned_at)

    def test_one_time_cleanup_of_earlier_runs(self):
        projects = self.base / "projects"
        run_dir = self.base / "runs" / "old-run"
        run_id = run_store.start_run(self.db, self.scenario.id, run_dir)
        run_store.finish_run(self.db, run_id, run_store.RunEnd(RunOutcomeKind.FINISHED))
        running_id = run_store.start_run(self.db, self.scenario.id, self.base / "runs" / "still-running")
        folder = projects / session_files.project_folder_name(run_dir)
        folder.mkdir(parents=True)
        (folder / "old-session.jsonl").write_text(json.dumps({"content": PASSWORD}), encoding="utf-8")

        self.assertEqual(run_store.clean_saved_sessions(self.db, projects), (1, 1, []))
        self.assertNotIn(json.dumps(PASSWORD)[1:-1], (folder / "old-session.jsonl").read_text("utf-8"))
        runs = {r.id: r for r in run_store.list_runs(self.db, self.session.id)}
        self.assertEqual(runs[run_id].claude_session_id, "old-session")  # linked to its session
        self.assertIsNotNone(runs[run_id].transcript_cleaned_at)
        self.assertIsNone(runs[running_id].transcript_cleaned_at)  # a run in progress is left alone
        self.assertEqual(run_store.clean_saved_sessions(self.db, projects), (0, 0, []))  # nothing left to do

    def test_runs_tab_shows_session_and_copies_resume_command(self):
        run_id = run_store.start_run(self.db, self.scenario.id, self.base / "runs" / "r1")
        run_store.finish_run(self.db, run_id, run_store.RunEnd(RunOutcomeKind.FINISHED, claude_session_id="sid-9"))
        run_store.mark_transcript_cleaned(self.db, run_id)
        tab = RunsTab(self.db)
        tab.set_session(self.session.id, "127.0.0.1")
        self.assertIn("sid-9 (test password removed)", tab.session_label.text())
        self.assertTrue(tab.copy_resume_button.isEnabled())
        tab.copy_resume_button.click()
        self.assertIn("--resume sid-9", QApplication.clipboard().text())
        tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
