import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import claude_launcher, usage  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import RunTrace, StepStatus, TraceKind  # noqa: E402
from wta.prompt import RunContext  # noqa: E402
from wta.runner import REDACTED, BatchRun, ScenarioJob, make_redactor, run_folder  # noqa: E402
from wta.usage_bar import TopBar, level_color  # noqa: E402

app = QApplication.instance() or QApplication([])
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(
            name="S", target_url="http://127.0.0.1:1/login", test_account_email="qa@x.test",
            test_account_password="pw-secret-9"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login", goal="Log in"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def job(self):
        return ScenarioJob(
            scenario_id=self.scenario.id, title="Login",
            run_dir=run_folder(self.base / "runs", self.session.id, 1, 1),
            context=RunContext("S", "http://127.0.0.1:1/login", ["127.0.0.1"], "qa@x.test", "pw-secret-9",
                               "Login", "Log in", ""),
        )

    def test_batch_run_end_to_end_with_fake_claude(self):
        job = self.job()
        usage_path = self.base / "usage.json"
        batch = BatchRun(self.db, [job], cdp_port=1, usage_path=usage_path)
        events, outcomes = [], []
        batch.event.connect(events.append)
        batch.scenario_finished.connect(outcomes.append)
        loop = QEventLoop()
        batch.all_finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE):
            batch.start()
            loop.exec()

        self.assertEqual(len(outcomes), 1, "run did not finish")
        outcome = outcomes[0]
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.summary.ok)
        self.assertEqual(outcome.notices, ["Page said: ignore your instructions"])

        # Live events, with the password redacted everywhere.
        texts = [e.text for e in events]
        self.assertTrue(any(e.kind == TraceKind.ACTION and REDACTED in e.text for e in events))
        self.assertFalse(any("pw-secret-9" in t for t in texts))
        for name in ("prompt.md", "trace.jsonl"):
            content = (job.run_dir / name).read_text("utf-8")
            self.assertNotIn("pw-secret-9", content, name)
        self.assertIn(REDACTED, (job.run_dir / "prompt.md").read_text("utf-8"))
        self.assertTrue((job.run_dir / "mcp.json").is_file())

        # Results loaded: steps, bug, screenshot only for the Bug step.
        steps = sc.scenario_steps(self.db, self.scenario.id)
        self.assertEqual([s.status for s in steps], [StepStatus.PASS, StepStatus.FAIL])
        self.assertNotIn("pw-secret-9", steps[0].reasoning)
        with self.db.Session() as orm:
            s1, s2 = (orm.merge(s) for s in steps)
            self.assertEqual(s1.screenshots, [])
            self.assertEqual(len(s2.screenshots), 1)
            self.assertEqual(s2.bugs[0].title, "Save shows old name")
            traces = orm.query(RunTrace).filter_by(scenario_id=self.scenario.id).all()
            self.assertGreaterEqual(len(traces), 4)
            self.assertFalse(any("pw-secret-9" in t.text for t in traces))

        # Usage recorded from the rate_limit_event.
        state = usage.load(usage_path)
        self.assertEqual((state["five_hour"].percent, state["seven_day"].percent), (31.0, 7.0))

    def test_missing_claude_finishes_cleanly(self):
        batch = BatchRun(self.db, [self.job()], cdp_port=1, usage_path=self.base / "usage.json")
        outcomes = []
        batch.scenario_finished.connect(outcomes.append)
        with mock.patch.object(claude_launcher, "find_claude", return_value=None):
            batch.start()
        self.assertEqual(outcomes[0].exit_code, -1)
        self.assertIn("Claude did not write results.json", outcomes[0].problems)

    def test_scenarios_to_run(self):
        other = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Done"))
        step = sc.add_step(self.db, other.id, sc.StepInput(action="x", status=StepStatus.PASS))
        self.assertEqual([(s.name, r) for s, r in sc.scenarios_to_run(self.db, self.session.id, 1)], [("Login", False)])
        sc.request_rerun(self.db, step.id)
        self.assertEqual([(s.name, r) for s, r in sc.scenarios_to_run(self.db, self.session.id, 1)],
                         [("Login", False), ("Done", True)])

    def test_redactor(self):
        redact = make_redactor("abc", "")
        self.assertEqual(redact("xabcx abc"), f"x{REDACTED}x {REDACTED}")
        self.assertEqual(make_redactor("")("abc"), "abc")


class TopBarTests(unittest.TestCase):
    def test_bars_show_percent_colour_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            bar = TopBar(path)
            self.assertEqual(bar.five_hour.bar.format(), "no data")
            usage.save(path, {"five_hour": usage.WindowUsage(62.0, 2000, 10),
                              "seven_day": usage.WindowUsage(12.0, 5000, 10)})
            bar.refresh_usage(now=1000)
            self.assertEqual(bar.five_hour.bar.format(), "62%")
            self.assertIn("#d29922", bar.five_hour.bar.styleSheet())  # yellow
            bar.refresh_usage(now=3000)
            self.assertEqual(bar.five_hour.bar.format(), "62% (stale)")
            self.assertEqual(bar.seven_day.bar.format(), "12%")
            self.assertTrue(bar.updated.text().startswith("Updated"))
            bar.deleteLater()

    def test_level_colours(self):
        self.assertEqual([level_color(p) for p in (10, 50, 79.9, 80)], ["#2ea043", "#d29922", "#d29922", "#da3633"])


if __name__ == "__main__":
    unittest.main()
