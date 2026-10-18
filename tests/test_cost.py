"""Phase 15: cost control (usage estimate, pause limit, model per run type, sign-in reuse)."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wta import claude_launcher, cost, settings, usage  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Run, RunOutcomeKind, ScenarioKind  # noqa: E402
from wta.notify import Notifier  # noqa: E402
from wta.prompt import SIGN_IN_REUSE, RunContext, build_prompt  # noqa: E402
from wta.runner import BatchRun, RunOutcome, ScenarioJob, run_folder  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.settings_dialog import SettingsDialog  # noqa: E402

app = QApplication.instance() or QApplication([])
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
FAR = 4102444800  # a reset time far in the future


def reading(percent, resets_at=FAR):
    return usage.WindowUsage(percent, resets_at, 0)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_cleaning(self):
        default = settings.Settings()
        self.assertEqual((default.pause_at_percent, default.scenario_model, default.smoke_model), (80, "", "sonnet"))
        cleaned = settings.Settings(pause_at_percent=30, scenario_model="claude-opus-5-5",
                                    smoke_model="sonnet --dangerous").cleaned()
        self.assertEqual(cleaned.pause_at_percent, 80)  # out of range: back to the default
        self.assertEqual(cleaned.scenario_model, "claude-opus-5-5")
        self.assertEqual(cleaned.smoke_model, "")  # not a model name: Claude Code's default
        self.assertEqual(settings.Settings(pause_at_percent=0).cleaned().pause_at_percent, 0)  # off

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings.save(path, settings.Settings(pause_at_percent=90, scenario_model="opus", smoke_model="haiku"))
            loaded = settings.load(path)
            self.assertEqual((loaded.pause_at_percent, loaded.scenario_model, loaded.smoke_model), (90, "opus", "haiku"))

    def test_dialog(self):
        dialog = SettingsDialog(settings.Settings())
        self.assertEqual(dialog.smoke_model.currentText(), "Sonnet")
        self.assertEqual(dialog.scenario_model.currentText(), "Claude Code default")
        self.assertEqual(dialog.pause_at.value(), 80)
        dialog.pause_at.setValue(dialog.pause_at.minimum())  # "Never"
        self.assertEqual(dialog.pause_at.text(), "Never")
        dialog.scenario_model.setEditText("claude-opus-5-5")
        result = dialog.settings()
        self.assertEqual((result.pause_at_percent, result.scenario_model, result.smoke_model),
                         (0, "claude-opus-5-5", "sonnet"))
        again = SettingsDialog(result)
        self.assertEqual(again.scenario_model.currentText(), "claude-opus-5-5")
        self.assertEqual(again.pause_at.text(), "Never")
        dialog.deleteLater()
        again.deleteLater()


class LauncherAndPromptTests(unittest.TestCase):
    def test_model_flag(self):
        args = claude_launcher.run_args(Path("mcp.json"), model="sonnet")
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertNotIn("--model", claude_launcher.run_args(Path("mcp.json")))
        resumed = claude_launcher.run_args(Path("mcp.json"), resume_session="abc", model="sonnet")
        self.assertEqual(resumed[:4], ["--resume", "abc", "--model", "sonnet"])

    def test_sign_in_is_reused(self):
        with_account = RunContext("S", "https://a.test", ["a.test"], "qa@a.test", "pw", "N", "Open cart", "")
        self.assertIn(SIGN_IN_REUSE, build_prompt(with_account))
        without = RunContext("S", "https://a.test", ["a.test"], "", "", "N", "Open cart", "")
        self.assertNotIn(SIGN_IN_REUSE, build_prompt(without))


class UsedTests(unittest.TestCase):
    def test_five_hour_used(self):
        def outcome(before, seen):
            return RunOutcome(1, 0, None, usage_at_start={"five_hour": before} if before else {},
                              usage_seen={"five_hour": seen} if seen else {})
        self.assertEqual(outcome(reading(40), reading(43)).five_hour_used, 3.0)
        self.assertIsNone(outcome(reading(90, 1000), reading(4)).five_hour_used)  # window reset in between
        self.assertIsNone(outcome(None, reading(7)).five_hour_used)  # would count all earlier use too
        self.assertIsNone(outcome(reading(40), None).five_hour_used)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://a.test"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login", steps="Log in"))
        self.smoke = sc.add_smoke_page(self.db, self.session.id, 1, sc.SmokePageInput(page_name="Home",
                                                                                      url="https://a.test"))
        self.usage_path = self.base / "usage.json"

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add_run(self, scenario, used, outcome=RunOutcomeKind.FINISHED):
        with self.db.Session.begin() as orm:
            orm.add(Run(scenario_id=scenario.id, run_dir="x", outcome=outcome, five_hour_used=used))

    def set_usage(self, percent, resets_at=None):
        usage.save(self.usage_path, {"five_hour": usage.WindowUsage(percent, resets_at or int(time.time()) + 3600,
                                                                     time.time())})


class EstimateTests(Fixture):
    def test_no_history_no_estimate(self):
        self.assertIsNone(cost.estimate_batch(self.db, [ScenarioKind.SCENARIO]))
        self.assertIsNone(cost.estimate_batch(self.db, []))

    def test_average_of_recent_finished_runs_per_kind(self):
        for used in (2, 4, 6):
            self.add_run(self.scenario, used)
        self.add_run(self.scenario, 50, RunOutcomeKind.FAILED)  # not counted
        self.add_run(self.scenario, None)  # no reading: not counted
        self.add_run(self.smoke, 1)
        self.assertEqual(cost.average_use(self.db, ScenarioKind.SCENARIO), (4.0, 3))
        estimate = cost.estimate_batch(self.db, [ScenarioKind.SCENARIO, ScenarioKind.SCENARIO, ScenarioKind.SMOKE])
        self.assertEqual(estimate.percent, 9.0)
        self.assertEqual(estimate.text(), "about 9% of the 5-hour window (from 3 earlier runs)")

    def test_kind_without_history_borrows(self):
        self.add_run(self.scenario, 3)
        estimate = cost.estimate_batch(self.db, [ScenarioKind.SMOKE])
        self.assertEqual((estimate.percent, estimate.runs), (3.0, 1))
        self.assertEqual(cost.Estimate(0.4, 2).text(), "less than 1% of the 5-hour window (from 2 earlier runs)")

    def test_only_the_latest_runs_count(self):
        for used in [10] * 5 + [1] * cost.ESTIMATE_RUNS:
            self.add_run(self.scenario, used)
        self.assertEqual(cost.average_use(self.db, ScenarioKind.SCENARIO), (1.0, cost.ESTIMATE_RUNS))

    def test_estimate_label_on_the_tab(self):
        tab = ScenarioTab(self.db)
        tab.set_session(self.session.id)
        self.assertEqual(tab.estimate_label.text(), "")
        self.add_run(self.scenario, 5)
        sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Second"))
        tab.set_session(self.session.id)
        # Both scenarios still need a run: 2 x 5%.
        self.assertEqual(tab.estimate_label.text(), "Estimated: about 10% of the 5-hour window (from 1 earlier run)")
        tab.deleteLater()


class LimitTests(Fixture):
    def test_pause_reason(self):
        self.assertIsNone(cost.pause_reason(self.usage_path, 80))  # no reading yet
        self.set_usage(79)
        self.assertIsNone(cost.pause_reason(self.usage_path, 80))
        self.set_usage(80)
        self.assertIn("reached your pause limit of 80%", cost.pause_reason(self.usage_path, 80))
        self.assertIsNone(cost.pause_reason(self.usage_path, 0))  # pausing is off
        self.set_usage(95, resets_at=int(time.time()) - 10)  # that window is over
        self.assertIsNone(cost.pause_reason(self.usage_path, 80))

    def test_before_batch_warning(self):
        self.set_usage(70)
        self.assertIsNone(cost.before_batch_warning(self.usage_path, 80, None))
        self.assertIsNone(cost.before_batch_warning(self.usage_path, 80, cost.Estimate(5, 3)))
        warning = cost.before_batch_warning(self.usage_path, 80, cost.Estimate(15, 3))
        self.assertIn("about 85%", warning)
        self.assertIsNone(cost.before_batch_warning(self.usage_path, 0, cost.Estimate(15, 3)))
        self.set_usage(91)
        self.assertIn("at 91%", cost.before_batch_warning(self.usage_path, 80, None))


class BatchPauseTests(Fixture):
    def jobs(self, count=2):
        return [ScenarioJob(scenario_id=self.scenario.id, title=f"Login {i}",
                            run_dir=run_folder(self.base / "runs", self.session.id, 1, i),
                            context=RunContext("S", "http://127.0.0.1:1/", ["127.0.0.1"], "qa@x.test", "pw-1",
                                               "Login", "Log in", ""))
                for i in range(1, count + 1)]

    def run_batch(self, answer):
        """Run two jobs; the pause check always says stop. `answer` is called with the batch when it pauses."""
        batch = BatchRun(self.db, self.jobs(), cdp_port=1, usage_path=self.usage_path)
        batch.pause_check = lambda: "limit reached"
        started, paused = [], []
        batch.scenario_started.connect(lambda job, i, n: started.append(i))
        batch.paused.connect(lambda reason: (paused.append(reason), QTimer.singleShot(0, lambda: answer(batch))))
        loop = QEventLoop()
        batch.all_finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE):
            batch.start()
            loop.exec()
        return batch, started, paused

    def test_pause_then_continue(self):
        batch, started, paused = self.run_batch(lambda b: b.resume())
        self.assertEqual(paused, ["limit reached"])  # asked once, before the second scenario only
        self.assertEqual(started, [1, 2])
        self.assertFalse(batch.stopped)

    def test_pause_then_stop(self):
        batch, started, paused = self.run_batch(lambda b: b.stop())
        self.assertEqual(paused, ["limit reached"])
        self.assertEqual(started, [1])
        self.assertTrue(batch.stopped)


class AppTests(Fixture):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.db, app_dir=self.base, check_sign_in=False)

    def tearDown(self):
        self.window.deleteLater()
        super().tearDown()

    def paused_with(self, answer):
        calls = []
        batch = SimpleNamespace(waiting=True, remaining=2, resume=lambda: calls.append("resume"))
        self.window.batch_run = batch
        with mock.patch.object(QMessageBox, "question", return_value=answer), \
                mock.patch.object(Notifier, "available", return_value=False), \
                mock.patch.object(QApplication, "alert"), \
                mock.patch.object(MainWindow, "stop_run", lambda w: calls.append("stop")):
            self.window._batch_paused("Your 5-hour plan usage is at 82%")
        self.window.batch_run = None
        return calls

    def test_pause_dialog_continue(self):
        self.assertEqual(self.paused_with(QMessageBox.StandardButton.Yes), ["resume"])
        self.assertEqual(self.window.notifier.sent[-1][0], "Batch paused")
        self.assertFalse(self.window.browser.banner.isHidden())  # Claude controls the browser again

    def test_pause_dialog_stop(self):
        self.assertEqual(self.paused_with(QMessageBox.StandardButton.No), ["stop"])
        self.assertTrue(self.window.browser.banner.isHidden())


class MigrationTests(unittest.TestCase):
    def test_earlier_runs_get_their_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wta.sqlite3"
            db = Database(path)
            session = store.create_session(db, store.SessionDetails(name="S", target_url="https://a.test"))
            scenario = sc.add_scenario(db, session.id, 1, sc.ScenarioInput(name="Login"))
            with db.Session.begin() as orm:
                orm.add_all([
                    Run(scenario_id=scenario.id, run_dir="a", outcome=RunOutcomeKind.FINISHED,
                        five_hour_before=40, five_hour_seen=44),
                    Run(scenario_id=scenario.id, run_dir="b", outcome=RunOutcomeKind.FINISHED,
                        five_hour_before=90, five_hour_seen=3),  # window reset: unknown, left empty
                    Run(scenario_id=scenario.id, run_dir="c", outcome=RunOutcomeKind.STOPPED,
                        five_hour_before=10, five_hour_seen=12),
                ])
            db.close()
            conn = sqlite3.connect(path)  # make it a database from before Phase 15
            conn.execute("ALTER TABLE run DROP COLUMN five_hour_used")
            conn.execute("ALTER TABLE run DROP COLUMN model")
            conn.commit()
            conn.close()
            db = Database(path)
            self.assertIn("run.five_hour_used", db.upgraded_columns)
            with db.Session() as orm:
                used = [r.five_hour_used for r in orm.query(Run).order_by(Run.run_dir)]
            db.close()
            self.assertEqual(used, [4.0, None, None])


if __name__ == "__main__":
    unittest.main()
