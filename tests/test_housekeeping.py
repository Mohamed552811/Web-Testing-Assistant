"""Phase 18: clean-up and backups, search across everything, trends."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wta import housekeeping as hk  # noqa: E402
from wta import trends  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, library, search, site_notes, store  # noqa: E402
from wta.db import runs  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Bug, Run, RunOutcomeKind, Step, StepStatus, utcnow  # noqa: E402
from wta.housekeeping_dialog import HousekeepingDialog  # noqa: E402
from wta.search_dialog import SearchDialog  # noqa: E402
from wta.trends_dialog import TrendsDialog  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.runs_dir = self.base / "runs"
        self.session = store.create_session(self.db, store.SessionDetails(name="Build 41",
                                                                          target_url="https://shop.test/app"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout", steps="Pay"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add_run(self, days_ago, scenario=None, steps=(("pass",),)):
        folder = self.runs_dir / f"run-{days_ago}-{len(list(self.runs_dir.glob('*'))) if self.runs_dir.exists() else 0}"
        folder.mkdir(parents=True)
        (folder / "trace.jsonl").write_text("x" * 1000)
        run_id = runs.start_run(self.db, (scenario or self.scenario).id, folder)
        runs.finish_run(self.db, run_id, runs.RunEnd(outcome=RunOutcomeKind.FINISHED))
        with self.db.Session.begin() as orm:
            orm.get(Run, run_id).started_at = utcnow() - timedelta(days=days_ago)
        runs.save_run_steps(self.db, run_id, [{"order": i + 1, "action": f"Step {i + 1}", "status": s[0]}
                                              for i, s in enumerate(steps)])
        return run_id, folder


class CleanUpTests(Fixture):
    def test_old_runs_keep_the_latest_per_scenario(self):
        old_id, old = self.add_run(40)
        _mid_id, mid = self.add_run(35)  # the scenario's latest run: kept even though it is old
        other = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Other"))
        self.add_run(1, other)
        found = hk.old_runs(self.db, self.runs_dir, 30)
        self.assertEqual([r.run_id for r in found], [old_id])
        self.assertEqual(found[0].size, 1000)
        self.assertEqual(len(hk.old_runs(self.db, self.runs_dir, 30, keep_latest=False)), 2)

        removed, freed = hk.remove_run_folders(self.db, self.runs_dir, found)
        self.assertEqual((removed, freed), (1, 1000))
        self.assertFalse(old.exists())
        self.assertTrue(mid.exists())
        self.assertEqual(len(runs.run_steps(self.db, old_id)), 1)  # history stays
        self.assertEqual(hk.old_runs(self.db, self.runs_dir, 30), [])  # not offered again

    def test_never_outside_the_runs_folder(self):
        outside = self.base / "elsewhere"
        outside.mkdir()
        run_id = runs.start_run(self.db, self.scenario.id, outside)
        runs.finish_run(self.db, run_id, runs.RunEnd(outcome=RunOutcomeKind.FINISHED))
        with self.db.Session.begin() as orm:
            orm.get(Run, run_id).started_at = utcnow() - timedelta(days=90)
        self.runs_dir.mkdir()
        self.assertEqual(hk.old_runs(self.db, self.runs_dir, 30, keep_latest=False), [])
        self.assertEqual(hk.remove_run_folders(self.db, self.runs_dir, [hk.OldRun(run_id, outside, 0, utcnow())]),
                         (0, 0))
        self.assertTrue(outside.exists())

    def test_sizes(self):
        self.assertEqual(hk.human_size(500), "500 bytes")
        self.assertEqual(hk.human_size(3 * 1024 * 1024), "3.0 MB")


class BackupTests(Fixture):
    def test_backup_restore_and_rotation(self):
        first = hk.backup(self.db.path, self.base, datetime(2026, 10, 1, 9, 0, 0))
        self.assertTrue(first.is_relative_to(self.base / hk.BACKUP_DIR))  # stays on this machine
        with sqlite3.connect(first) as conn:
            self.assertEqual(conn.execute("SELECT name FROM test_session").fetchone()[0], "Build 41")
        conn.close()
        store.create_session(self.db, store.SessionDetails(name="Build 42", target_url="https://shop.test"))

        hk.schedule_restore(first, self.base)
        self.db.close()
        safety = hk.apply_pending_restore(self.base, self.db.path)
        self.assertIsNotNone(safety)
        self.db = Database(self.db.path)
        self.assertEqual([s.name for s in store.list_sessions(self.db)], ["Build 41"])
        self.assertIsNone(hk.apply_pending_restore(self.base, self.db.path))  # only once
        restored_from_safety = Database(safety)
        self.assertEqual(len(store.list_sessions(restored_from_safety)), 2)  # the state before the restore
        restored_from_safety.close()

        for day in range(2, 15):
            hk.backup(self.db.path, self.base, datetime(2026, 10, day, 9, 0, 0))
        self.assertEqual(len(hk.list_backups(self.base)), hk.KEEP_BACKUPS)
        names = [p.name for p in hk.list_backups(self.base)]
        self.assertIn("wta-20261014-090000.sqlite3", names)
        self.assertNotIn("wta-20261001-090000.sqlite3", names)  # the oldest went first

    def test_restore_refuses_other_files(self):
        other = self.base / "other.sqlite3"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE foo (x)")
        conn.close()
        with self.assertRaises(ValueError):
            hk.schedule_restore(other, self.base)
        (self.base / "text.sqlite3").write_text("hello")
        with self.assertRaises(ValueError):
            hk.schedule_restore(self.base / "text.sqlite3", self.base)

    def test_dialog(self):
        _old_id, _ = self.add_run(60)
        self.add_run(1)
        dialog = HousekeepingDialog(self.db, self.runs_dir, self.base)
        self.assertIn("2.0 KB", dialog.disk.text())
        self.assertFalse(dialog.remove_button.isEnabled())
        dialog.find()
        self.assertIn("1 run folder(s)", dialog.found_label.text())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.remove()
        self.assertIn("Removed 1 run folder(s)", dialog.found_label.text())
        dialog.back_up()
        self.assertEqual(dialog.backups.count(), 1)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "information"):
            dialog.restore()
        self.assertTrue((self.base / hk.PENDING_RESTORE).exists())
        dialog.deleteLater()


class SearchTests(Fixture):
    def setUp(self):
        super().setUp()
        with self.db.Session.begin() as orm:
            orm.add(Step(scenario_id=self.scenario.id, order=1, action="Apply the coupon SAVE10",
                         status=StepStatus.FAIL, actual_result="Total unchanged",
                         bugs=[Bug(title="Coupon is ignored at checkout", actual="No discount applied")]))
        site_notes.add_note(self.db, "shop.test", "The coupon field needs a second click")
        library.add_items(self.db, [library.ItemInput(scenario=sc.ScenarioInput("Coupon regression", "Apply SAVE10"))])
        sc.add_smoke_page(self.db, self.session.id, 1, sc.SmokePageInput("Coupons", "https://shop.test/coupons"))

    def test_search_everything(self):
        hits = search.search(self.db, "coupon")
        kinds = sorted(h.kind for h in hits)
        self.assertEqual(kinds, ["Bug", "Library", "Site note", "Smoke page", "Step"])
        step = next(h for h in hits if h.kind == "Step")
        self.assertEqual((step.session_name, step.title), ("Build 41", "Checkout — step 1"))
        bug = next(h for h in hits if h.kind == "Bug")
        self.assertIsNotNone(bug.step_id)
        self.assertEqual(search.search(self.db, "c"), [])  # too short
        self.assertEqual(search.snippet("a " * 100 + "needle " + "b " * 100, "needle", 10)[:1], "…")

    def test_dialog_opens_the_step(self):
        window = MainWindow(self.db, app_dir=self.base, check_sign_in=False)
        dialog = SearchDialog(self.db)
        dialog.hit_chosen.connect(window.open_hit)
        dialog.query.setText("ignored at checkout")
        dialog.run_search()
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.table.selectRow(0)
        dialog.open_current()
        self.assertEqual(window.current_session.id, self.session.id)
        self.assertIs(window.tabs.currentWidget(), window.scenario_tab)
        self.assertEqual(window.scenario_tab._current_step().action, "Apply the coupon SAVE10")
        dialog.deleteLater()
        window.deleteLater()


class TrendTests(Fixture):
    def test_pass_rate_by_day_and_bugs_per_session(self):
        self.add_run(2, steps=(("pass",), ("fail",)))
        self.add_run(2, steps=(("pass",), ("pass",)))
        self.add_run(0, steps=(("pass",), ("blocked",)))
        other_site = store.create_session(self.db, store.SessionDetails(name="X", target_url="https://other.test"))
        self.add_run(0, sc.add_scenario(self.db, other_site.id, 1, sc.ScenarioInput(name="Y")), steps=(("fail",),))

        self.assertEqual(trends.sites(self.db), ["other.test", "shop.test"])
        days = trends.runs_by_day(self.db, "shop.test")
        self.assertEqual([(d.runs, d.steps, d.passed, d.failed) for d in days], [(2, 4, 3, 1), (1, 2, 1, 0)])
        self.assertEqual(days[0].pass_rate, 75.0)

        with self.db.Session.begin() as orm:
            orm.add_all([
                Step(scenario_id=self.scenario.id, order=1, action="Pay", status=StepStatus.FAIL, bugs=[Bug(title="A")]),
                Step(scenario_id=self.scenario.id, order=2, action="Ship", status=StepStatus.FAIL, bugs=[Bug(title="B")]),
            ])
        steps = sc.scenario_steps(self.db, self.scenario.id)
        sc.override_step(self.db, steps[1].id, StepStatus.PASS, "not a bug")
        bugs, = trends.bugs_by_session(self.db, "shop.test")
        self.assertEqual((bugs.name, bugs.open_bugs, bugs.reviewed, bugs.steps), ("Build 41", 1, 1, 2))

        dialog = TrendsDialog(self.db, "shop.test")
        self.assertEqual(dialog.site.currentText(), "shop.test")
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(1, 3).text(), "75%")  # newest day first
        self.assertEqual(dialog.empty.text(), "")
        dialog.site.setCurrentText("other.test")
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
