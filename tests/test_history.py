import json
import os
import sqlite3
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

from wta import claude_launcher  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import runs as run_store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db import site_notes  # noqa: E402
from wta.db.models import RunOutcomeKind, TraceKind  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402
from wta.runner import BatchRun, ScenarioJob, run_folder  # noqa: E402
from wta.runs_tab import RunsTab  # noqa: E402

app = QApplication.instance() or QApplication([])
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="http://127.0.0.1:8765/app"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()


class UpgradeTests(unittest.TestCase):
    def test_old_database_gets_run_id_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite3"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE run_trace (id INTEGER PRIMARY KEY, scenario_id INTEGER NOT NULL, step_id INTEGER,
                                        timestamp DATETIME NOT NULL, kind VARCHAR(20) NOT NULL, text TEXT NOT NULL);
                INSERT INTO run_trace (scenario_id, timestamp, kind, text) VALUES (1, '2026-01-01', 'note', 'old row');
            """)
            con.close()
            db = Database(path)
            try:
                self.assertEqual(db.upgraded_columns, ["run_trace.run_id"])
                with db.engine.connect() as conn:
                    cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(run_trace)")]
                    self.assertIn("run_id", cols)
                    fks = list(conn.exec_driver_sql("PRAGMA foreign_key_list(run_trace)"))
                    self.assertTrue(any(fk[2] == "run" for fk in fks))
                    self.assertEqual(conn.exec_driver_sql("SELECT text FROM run_trace").scalar(), "old row")
            finally:
                db.close()
            again = Database(path)
            try:
                self.assertEqual(again.upgraded_columns, [])  # second open: nothing to do
            finally:
                again.close()


class RunStoreTests(Base):
    def test_start_finish_list_and_traces(self):
        run_id = run_store.start_run(self.db, self.scenario.id, self.base / "r1", five_hour_before=40.0, site_notes_used=2)
        run_store.add_trace(self.db, self.scenario.id, run_id, TraceKind.NOTE, "Logging in")
        run_store.add_trace(self.db, self.scenario.id, run_id, TraceKind.ACTION, "browser_click Sign in")
        run_store.add_trace(self.db, self.scenario.id, None, TraceKind.NOTE, "not part of the run")
        run_store.finish_run(self.db, run_id, run_store.RunEnd(RunOutcomeKind.FINISHED, exit_code=0, num_turns=5,
                                                               five_hour_seen=43.0, summary="ok"))
        (run,) = run_store.list_runs(self.db, self.session.id)
        self.assertEqual((run.outcome, run.num_turns, run.five_hour_before, run.five_hour_seen, run.site_notes_used),
                         (RunOutcomeKind.FINISHED, 5, 40.0, 43.0, 2))
        self.assertEqual(run.scenario.name, "Login")
        self.assertIsNotNone(run.ended_at)
        self.assertEqual(len(run_store.run_traces(self.db, run_id)), 2)
        self.assertEqual([t.text for t in run_store.run_traces(self.db, run_id, TraceKind.ACTION)], ["browser_click Sign in"])
        self.assertEqual([t.text for t in run_store.run_traces(self.db, run_id, search="logging")], ["Logging in"])

    def test_mark_interrupted_runs(self):
        run_id = run_store.start_run(self.db, self.scenario.id, self.base / "r1")
        self.assertEqual(run_store.mark_interrupted_runs(self.db), 1)
        (run,) = run_store.list_runs(self.db, self.session.id)
        self.assertEqual(run.outcome, RunOutcomeKind.FAILED)
        self.assertIn("closed before", run.summary)
        self.assertEqual(run.id, run_id)
        self.assertEqual(run_store.mark_interrupted_runs(self.db), 0)

    def test_steps_from_folder(self):
        folder = self.base / "r1"
        folder.mkdir()
        self.assertEqual(run_store.run_steps_from_folder(folder), [])
        (folder / "results.json").write_text(json.dumps({"steps": [{"order": 1, "status": "pass"}, "junk"]}), "utf-8")
        self.assertEqual(run_store.run_steps_from_folder(folder), [{"order": 1, "status": "pass"}])

    def test_deleting_scenario_removes_its_runs(self):
        run_id = run_store.start_run(self.db, self.scenario.id, self.base / "r1")
        run_store.add_trace(self.db, self.scenario.id, run_id, TraceKind.NOTE, "x")
        sc.delete_scenario(self.db, self.scenario.id)
        self.assertEqual(run_store.list_runs(self.db, self.session.id), [])
        self.assertEqual(run_store.run_traces(self.db, run_id), [])


class SiteNotesTests(Base):
    def test_site_is_host_only(self):
        self.assertEqual(site_notes.site_for_url("http://127.0.0.1:8765/app"), "127.0.0.1")
        self.assertEqual(site_notes.site_for_url("https://App.Example.com:3000/x?y"), "app.example.com")
        self.assertEqual(site_notes.site_for_url("app.example.com/login"), "app.example.com")
        self.assertEqual(site_notes.site_for_url("not a url"), "")

    def test_add_list_delete(self):
        first = site_notes.add_note(self.db, "127.0.0.1", "  Reload after saving  ")
        site_notes.add_note(self.db, "127.0.0.1", "Second note")
        site_notes.add_note(self.db, "other.test", "Elsewhere")
        self.assertEqual([n.note for n in site_notes.list_notes(self.db, "127.0.0.1")], ["Reload after saving", "Second note"])
        site_notes.delete_note(self.db, first.id)
        self.assertEqual([n.note for n in site_notes.list_notes(self.db, "127.0.0.1")], ["Second note"])
        with self.assertRaises(ValueError):
            site_notes.add_note(self.db, "127.0.0.1", "   ")
        with self.assertRaises(LookupError):
            site_notes.delete_note(self.db, 9999)

    def test_prompt_includes_notes_only_when_present(self):
        ctx = RunContext("S", "http://127.0.0.1/", ["127.0.0.1"], "", "", "N", "G", "")
        self.assertNotIn("notes about this site", build_prompt(ctx))
        ctx.site_notes = ["Reload after saving", "Use the Enter key"]
        text = build_prompt(ctx)
        self.assertIn("## The user's notes about this site", text)
        self.assertIn("- Reload after saving\n- Use the Enter key", text)
        self.assertLess(text.index("notes about this site"), text.index("## How to work"))


class RunnerHistoryTests(Base):
    def run_once(self, notes=(), mode=""):
        job = ScenarioJob(self.scenario.id, "Login",
                          RunContext("S", "http://127.0.0.1:1/", ["127.0.0.1"], "", "", "Login", "", "",
                                     site_notes=list(notes)),
                          run_folder(self.base / "runs", self.session.id, 1, 1))
        batch = BatchRun(self.db, [job], 1, self.base / "usage.json")
        loop = QEventLoop()
        batch.all_finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE), \
                mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": mode}):
            batch.start()
            loop.exec()
        return job

    def test_each_run_has_its_own_row_and_trace(self):
        self.run_once()
        job2 = self.run_once(notes=["Reload after saving"])
        newest, oldest = run_store.list_runs(self.db, self.session.id)
        self.assertEqual(oldest.outcome, RunOutcomeKind.FINISHED)
        self.assertEqual((oldest.num_turns, oldest.output_tokens, oldest.five_hour_seen), (3, 50, 31.0))
        self.assertEqual(newest.site_notes_used, 1)
        self.assertEqual(Path(newest.run_dir), job2.run_dir)
        first_traces = run_store.run_traces(self.db, oldest.id)
        second_traces = run_store.run_traces(self.db, newest.id)
        self.assertTrue(first_traces and second_traces)
        self.assertFalse({t.id for t in first_traces} & {t.id for t in second_traces})
        self.assertTrue(any("Using 1 site note(s): Reload after saving" in t.text for t in second_traces))
        self.assertIn("Reload after saving", (job2.run_dir / "prompt.md").read_text("utf-8"))


class RunsTabTests(Base):
    def test_shows_runs_trace_filter_and_site_notes(self):
        folder = self.base / "r1"
        folder.mkdir()
        (folder / "results.json").write_text(json.dumps({"steps": [{"order": 1, "status": "pass", "action": "Open"}]}), "utf-8")
        run_id = run_store.start_run(self.db, self.scenario.id, folder)
        run_store.add_trace(self.db, self.scenario.id, run_id, TraceKind.NOTE, "Thinking about login")
        run_store.add_trace(self.db, self.scenario.id, run_id, TraceKind.ACTION, "browser_click Sign in")
        run_store.finish_run(self.db, run_id, run_store.RunEnd(RunOutcomeKind.STOPPED, summary="Stopped early"))

        tab = RunsTab(self.db)
        tab.set_session(self.session.id, "127.0.0.1")
        self.assertEqual(tab.runs_table.rowCount(), 1)
        self.assertEqual(tab.runs_table.item(0, 3).text(), "Stopped")
        self.assertEqual(tab.trace.count(), 2)
        self.assertEqual(tab.steps_table.item(0, 2).text(), "Open")
        self.assertIn("Stopped early", tab.summary.text())
        tab.kind_filter.setCurrentIndex(tab.kind_filter.findData(TraceKind.ACTION))
        self.assertEqual(tab.trace.count(), 1)
        tab.kind_filter.setCurrentIndex(0)
        tab.search.setText("login")
        self.assertEqual(tab.trace.count(), 1)

        tab.notes_panel.new_note.setPlainText("Reload after saving")
        tab.notes_panel.add_button.click()
        self.assertEqual([n.note for n in site_notes.list_notes(self.db, "127.0.0.1")], ["Reload after saving"])
        tab.notes_panel.list.setCurrentRow(0)
        tab.notes_panel.delete_button.click()
        self.assertEqual(site_notes.list_notes(self.db, "127.0.0.1"), [])
        tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
