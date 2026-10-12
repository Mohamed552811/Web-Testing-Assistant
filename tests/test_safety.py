import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import claude_launcher, processes, settings  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import StepStatus  # noqa: E402
from wta.domain_guard import NavigationGuard, host_allowed, navigation_allowed  # noqa: E402
from wta.processes import ProcessRegistry  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402
from wta.results import parse_results  # noqa: E402
from wta.runner import BatchRun, ScenarioJob, run_folder  # noqa: E402

app = QApplication.instance() or QApplication([])
FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def wait_for(signal, timeout_ms=30000):
    loop = QEventLoop()
    signal.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


class DomainGuardTests(unittest.TestCase):
    def test_host_allowed(self):
        allowed = ["app.example.com", "127.0.0.1"]
        self.assertTrue(host_allowed("app.example.com", allowed))
        self.assertTrue(host_allowed("API.app.example.com.", allowed))  # subdomain, case, trailing dot
        self.assertFalse(host_allowed("example.com", allowed))
        self.assertFalse(host_allowed("evilapp.example.com", allowed))
        self.assertFalse(host_allowed("app.example.com.evil.test", allowed))
        self.assertTrue(host_allowed("127.0.0.1", allowed))
        self.assertFalse(host_allowed("localhost", allowed))
        self.assertFalse(host_allowed("", allowed))

    def test_navigation_allowed_schemes(self):
        allowed = ["127.0.0.1"]
        self.assertTrue(navigation_allowed(QUrl("http://127.0.0.1:8765/a"), allowed))
        self.assertTrue(navigation_allowed(QUrl("about:blank"), allowed))
        for url in ("file:///C:/Windows/win.ini", "data:text/html,hi", "javascript:alert(1)", "ftp://127.0.0.1/"):
            self.assertFalse(navigation_allowed(QUrl(url), allowed), url)

    def test_guard_blocks_real_navigation(self):
        profile = QWebEngineProfile()  # off-the-record
        guard = NavigationGuard()
        profile.setUrlRequestInterceptor(guard)
        page = QWebEnginePage(profile)
        blocked = []
        guard.blocked.connect(blocked.append)

        guard.activate(["127.0.0.1"])
        page.load(QUrl("http://localhost:1/outside"))
        wait_for(page.loadFinished, 15000)
        self.assertEqual(blocked, ["http://localhost:1/outside"])

        guard.deactivate()  # inactive: not blocked by the guard (the load itself just fails)
        page.load(QUrl("http://localhost:1/again"))
        wait_for(page.loadFinished, 15000)
        self.assertEqual(len(blocked), 1)
        del page


class SettingsAndPromptTests(unittest.TestCase):
    def test_settings_round_trip_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertFalse(settings.load(path).confirm_risky_actions)  # off by default
            settings.save(path, settings.Settings(confirm_risky_actions=True))
            self.assertTrue(settings.load(path).confirm_risky_actions)
            path.write_text(json.dumps({"confirm_risky_actions": "yes", "unknown": 1}), "utf-8")
            self.assertFalse(settings.load(path).confirm_risky_actions)

    def test_prompt_risky_section(self):
        ctx = RunContext("S", "https://a.test", ["a.test"], "", "", "N", "G", "")
        self.assertIn("allowed only when the scenario asks", build_prompt(ctx))
        ctx.confirm_risky_actions = True
        text = build_prompt(ctx)
        self.assertIn('"state": "awaiting_confirmation"', text)
        self.assertIn("End your turn immediately", text)

    def test_confirmation_request_parsing(self):
        parsed = parse_results({"state": "awaiting_confirmation", "steps": [],
                                "confirmation_request": {"step": 3, "action": "Click Delete", "reason": "asked"}})
        self.assertEqual((parsed.confirmation.step, parsed.confirmation.action), (3, "Click Delete"))
        missing = parse_results({"state": "awaiting_confirmation", "steps": []})
        self.assertIsNone(missing.confirmation)
        self.assertTrue(missing.problems)
        self.assertIsNone(parse_results({"state": "running", "steps": [], "confirmation_request": {"action": "x"}}).confirmation)

    def test_launcher_resume_and_denied_tools(self):
        args = claude_launcher.run_args(Path("m.json"), resume_session="abc")
        self.assertEqual(args[:2], ["--resume", "abc"])
        denied = args[args.index("--disallowedTools") + 1].split(",")
        self.assertIn("mcp__playwright__browser_evaluate", denied)
        self.assertIn("mcp__playwright__browser_run_code_unsafe", denied)
        self.assertNotIn("--resume", claude_launcher.run_args(Path("m.json")))


class RunControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="http://127.0.0.1:1/"))
        self.session_id = session.id
        self.scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Notes"))
        self.registry = ProcessRegistry(self.base / "processes.json")

    def tearDown(self):
        self.registry.kill_all()
        self.db.close()
        self.tmp.cleanup()

    def batch(self, confirm=False):
        job = ScenarioJob(self.scenario.id, "Notes", RunContext("S", "http://127.0.0.1:1/", ["127.0.0.1"], "", "",
                                                                "Notes", "", "", confirm),
                          run_folder(self.base / "runs", self.session_id, 1, 1))
        return BatchRun(self.db, [job], 1, self.base / "usage.json", self.registry), job

    def test_stop_kills_claude_and_its_children(self):
        batch, job = self.batch()
        outcomes = []
        batch.scenario_finished.connect(outcomes.append)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE), \
                mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "slow"}):
            batch.start()
            child_file = job.run_dir.resolve() / "child.pid"
            deadline = time.time() + 20
            while not child_file.exists() and time.time() < deadline:
                app.processEvents()
                time.sleep(0.05)
            child_pid = int(child_file.read_text())
            claude_pid = batch.current.process.processId()
            self.assertIsNotNone(processes.create_time(child_pid))
            batch.stop()
            app.processEvents()

        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].stopped)
        self.assertIsNone(processes.create_time(child_pid), "MCP-like child survived Stop")
        self.assertIsNone(processes.create_time(claude_pid))
        self.assertEqual(self.registry.entries, {})
        # Results written before the stop are kept.
        self.assertEqual([s.status for s in sc.scenario_steps(self.db, self.scenario.id)], [StepStatus.PASS])

    def run_confirmation(self, approve: bool):
        batch, job = self.batch(confirm=True)
        requests, outcomes = [], []
        batch.confirmation_needed.connect(lambda run, req: (requests.append(req), run.answer_confirmation(approve)))
        batch.scenario_finished.connect(outcomes.append)
        with mock.patch.object(claude_launcher, "find_claude", return_value=FAKE), \
                mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "confirm"}):
            batch.start()
            wait_for(batch.all_finished)
        self.assertEqual([r.action for r in requests], ["Click Delete note"])
        self.assertEqual(outcomes[0].exit_code, 0)
        return sc.scenario_steps(self.db, self.scenario.id)

    def test_confirmation_approved_resumes(self):
        steps = self.run_confirmation(approve=True)
        self.assertEqual([s.status for s in steps], [StepStatus.PASS, StepStatus.PASS])

    def test_confirmation_declined_resumes_and_blocks_step(self):
        steps = self.run_confirmation(approve=False)
        self.assertEqual(steps[1].status, StepStatus.BLOCKED)
        self.assertEqual(steps[1].reasoning, "Declined by the user")


if __name__ == "__main__":
    unittest.main()
