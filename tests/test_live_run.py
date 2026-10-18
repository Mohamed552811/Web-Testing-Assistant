"""Phase 14: live run experience (progress, timeline, banner, top bar, notifications)."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget  # noqa: E402

from wta import claude_launcher  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import StepStatus, TraceKind  # noqa: E402
from wta.notify import Notifier  # noqa: E402
from wta.prompt import RunContext, SmokeContext  # noqa: E402
from wta.results import Results, StepResult  # noqa: E402
from wta.run_panel import COLLAPSED_CHARS, RunPanel, format_elapsed, icon_for, one_line  # noqa: E402
from wta.runner import RunOutcome, ScenarioJob, run_progress  # noqa: E402
from wta.stream import RunSummary, TraceEvent  # noqa: E402
from wta.usage_bar import TopBar  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


def context(steps="", expected="", smoke=None):
    return RunContext("S", "https://app.example", ["app.example"], "", "", "Checkout", steps, "",
                      smoke=smoke, scenario_expected_result=expected)


def job(**kwargs):
    return ScenarioJob(scenario_id=1, title="Checkout", context=context(**kwargs), run_dir=Path("."))


def result_step(order, status):
    return StepResult(order, "", "", "", "", "", status, "", "", [], [], [], None, None)


class ProgressTests(unittest.TestCase):
    def test_planned_steps(self):
        self.assertEqual(job(steps="1. Open\n2. Pay", expected="Order placed").planned_steps, 3)
        self.assertEqual(job(steps="Open\nPay").planned_steps, 2)
        self.assertEqual(job(expected="Order placed").planned_steps, 0)  # Claude plans the steps itself
        smoke = SmokeContext(page_name="Home", url="https://app.example")
        self.assertEqual(job(steps="x", smoke=smoke).planned_steps, 0)

    def test_run_progress(self):
        results = Results("Checkout", "running", "", [], [result_step(1, StepStatus.PASS),
                                                          result_step(2, StepStatus.FAIL),
                                                          result_step(3, StepStatus.NOT_RUN)])
        self.assertEqual(run_progress(results), (2, 3))
        self.assertEqual(run_progress(results, planned=5), (2, 5))  # given steps not written yet


class RunPanelTests(unittest.TestCase):
    def setUp(self):
        self.panel = RunPanel()

    def tearDown(self):
        self.panel.deleteLater()

    def test_progress_line(self):
        self.assertTrue(self.panel.progress_label.isHidden())
        self.panel.scenario_started(job(steps="a\nb\nc\nd", expected="ok"), 1, 2)
        self.assertEqual(self.panel.progress_label.text(), "Step 1 of 5 · 0 done")
        self.panel.set_progress(3, 5)
        self.assertEqual(self.panel.progress_label.text(), "Step 4 of 5 · 3 done")
        self.assertEqual((self.panel.progress_bar.value(), self.panel.progress_bar.maximum()), (3, 5))
        self.panel.set_progress(5, 5)
        self.assertEqual(self.panel.progress_label.text(), "All 5 steps done")
        self.assertIn("scenario 1 of 2", self.panel.header.text())

    def test_smoke_progress_says_check(self):
        smoke = SmokeContext(page_name="Home", url="https://app.example")
        self.panel.scenario_started(job(smoke=smoke), 1, 1)
        self.assertTrue(self.panel.progress_label.isHidden())  # checklist not written yet
        self.panel.set_progress(5, 15)
        self.assertEqual(self.panel.progress_label.text(), "Check 6 of 15 · 5 done")
        self.assertIn("page 1 of 1", self.panel.header.text())

    def test_long_output_is_collapsed_until_clicked(self):
        seen = "- generic [ref=e1]:\n" + "  - button \"Save\" [ref=e2]\n" * 40
        self.panel.add_event(TraceEvent(TraceKind.RESULT, seen))
        item = self.panel.events.item(0)
        self.assertNotIn("\n", item.text())
        self.assertIn("▸", item.text())
        self.assertLess(len(item.text()), COLLAPSED_CHARS + 30)
        self.panel.toggle_item(item)
        self.assertIn("▾", item.text())
        self.assertEqual(item.text().count("button"), 40)
        self.panel.toggle_item(item)
        self.assertIn("▸", item.text())

    def test_short_lines_and_notes_are_not_collapsible(self):
        self.panel.add_event(TraceEvent(TraceKind.RESULT, "Clicked"))
        self.panel.add_event(TraceEvent(TraceKind.NOTE, "I will sign in first.\nThen open the cart."))
        for row in range(2):
            item = self.panel.events.item(row)
            before = item.text()
            self.panel.toggle_item(item)
            self.assertEqual(item.text(), before)
            self.assertFalse(item.icon().isNull())

    def test_icons_per_tool(self):
        self.assertEqual(icon_for(TraceEvent(TraceKind.ACTION, "x", "browser_click")), "mouse-pointer-click")
        self.assertEqual(icon_for(TraceEvent(TraceKind.ACTION, "x", "browser_navigate")), "globe")
        self.assertEqual(icon_for(TraceEvent(TraceKind.ACTION, "x", "browser_type")), "keyboard")
        self.assertEqual(icon_for(TraceEvent(TraceKind.ACTION, "x", "Write")), "file-text")
        self.assertEqual(icon_for(TraceEvent(TraceKind.ACTION, "x", "something_new")), "mouse-pointer-click")
        self.assertEqual(icon_for(TraceEvent(TraceKind.NOTE, "x")), "message-square")
        self.assertEqual(icon_for(TraceEvent(TraceKind.RESULT, "x")), "eye")

    def test_timer(self):
        self.assertEqual([format_elapsed(s) for s in (0, 65, 3725)], ["0:00", "1:05", "1:02:05"])
        self.panel.set_running(True)
        self.panel._update_elapsed(now=self.panel.started_at + 75)
        self.assertEqual(self.panel.elapsed_label.text(), "Elapsed 1:15")
        self.panel.all_finished()
        self.assertFalse(self.panel._timer.isActive())
        self.assertFalse(self.panel.stop_button.isEnabled())

    def test_finished_line_has_an_icon(self):
        outcome = RunOutcome(scenario_id=1, exit_code=0, summary=RunSummary(ok=True, subtype="success"))
        self.panel.scenario_finished(outcome, "Checkout")
        item = self.panel.events.item(0)
        self.assertEqual(item.text(), "Checkout: finished")
        self.assertFalse(item.icon().isNull())

    def test_run_folder_paths_are_shortened(self):
        run = job(steps="a")
        run.run_dir = Path(r"C:\data\runs\session-1\batch-1\scenario-1-20261018")
        self.panel.scenario_started(run, 1, 1)
        self.panel.add_event(TraceEvent(TraceKind.ACTION, f"Write {run.run_dir}\\results.json", "Write"))
        self.panel.add_event(TraceEvent(TraceKind.NOTE, f"Starting Claude Code in {run.run_dir}"))
        self.assertTrue(self.panel.events.item(1).text().endswith("Action: Write results.json"))
        self.assertTrue(self.panel.events.item(2).text().endswith("Starting Claude Code in the run folder"))

    def test_follows_new_lines_until_scrolled_up(self):
        self.panel.resize(500, 300)
        self.panel.show()
        for i in range(60):
            self.panel.add_event(TraceEvent(TraceKind.NOTE, f"note {i}"))
        app.processEvents()
        bar = self.panel.events.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())
        bar.setValue(0)  # the user scrolls up to read
        for i in range(10):
            self.panel.add_event(TraceEvent(TraceKind.NOTE, f"more {i}"))
        app.processEvents()
        self.assertEqual(bar.value(), 0)
        self.panel.hide()

    def test_one_line(self):
        self.assertEqual(one_line("a\n  b"), "a b")
        self.assertEqual(len(one_line("x" * 500, 20)), 20)


class TopBarTests(unittest.TestCase):
    def test_sign_in_dot_and_hover_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            bar = TopBar(Path(tmp) / "usage.json")
            bar._on_sign_in(claude_launcher.SignInStatus(False, "Claude Code is not installed (no `claude` on PATH)."))
            self.assertEqual(bar.claude_label.text(), "Runs blocked: Claude Code not ready")
            self.assertIn("not installed", bar.claude_label.toolTip())
            self.assertIn("#da3633", bar.dot.styleSheet())
            self.assertFalse(bar.check_button.isHidden())
            bar._on_sign_in(claude_launcher.SignInStatus(True, "Signed in as someone (Max plan)"))
            self.assertIn("#2ea043", bar.dot.styleSheet())
            self.assertIn("Max plan", bar.dot.toolTip())
            self.assertIn("No usage reported yet", bar.five_hour.bar.toolTip())
            bar.deleteLater()


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.window = QWidget()
        self.notifier = Notifier(self.window, "Web Testing Assistant")

    def tearDown(self):
        self.window.deleteLater()

    def test_without_a_tray_it_only_flashes(self):
        with mock.patch.object(Notifier, "available", return_value=False), \
                mock.patch.object(QApplication, "alert") as alert:
            self.notifier.run_started()
            self.notifier.notify("Run finished", "1 scenario")
        alert.assert_called_once_with(self.window)
        self.assertIsNone(self.notifier.tray)
        self.assertEqual(self.notifier.sent, [("Run finished", "1 scenario")])

    def test_with_a_tray_it_shows_a_message(self):
        with mock.patch.object(Notifier, "available", return_value=True), \
                mock.patch.object(QSystemTrayIcon, "showMessage") as show, \
                mock.patch.object(QApplication, "alert"):
            self.notifier.run_started()
            self.notifier.notify("Claude needs your confirmation", "Delete note", warning=True)
        self.assertIsNotNone(self.notifier.tray)
        heading, message, kind, _ms = show.call_args.args
        self.assertEqual((heading, message), ("Claude needs your confirmation", "Delete note"))
        self.assertEqual(kind, QSystemTrayIcon.MessageIcon.Warning)
        self.notifier.tray.hide()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://app.example"))
        self.scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Checkout"))
        for status in (StepStatus.PASS, StepStatus.PASS, StepStatus.FAIL):
            sc.add_step(self.db, self.scenario.id, sc.StepInput(action="x", status=status))

    def tearDown(self):
        self.window.deleteLater()
        self.db.close()
        self.tmp.cleanup()

    def outcome(self, ok=True, stopped=False):
        return RunOutcome(scenario_id=self.scenario.id, exit_code=0 if ok else 1, stopped=stopped,
                          summary=RunSummary(ok=ok, subtype="success" if ok else "error"))

    def test_batch_message(self):
        w = self.window
        w._batch_outcomes = [self.outcome()]
        self.assertEqual(w._batch_message(False),
                         ("Run finished", "1 scenario, 3 steps: 2 pass, 1 fail. Ready for your review."))
        w._batch_smoke = True
        self.assertEqual(w._batch_message(True)[1], "1 page, 3 checks: 2 pass, 1 bug. Ready for your review.")
        w._batch_smoke = False
        w._batch_outcomes = [self.outcome(ok=False)]
        heading, message = w._batch_message(False)
        self.assertEqual(heading, "Run finished with problems")
        self.assertIn("1 ended with an error", message)

    def test_banner_during_a_run_and_notification_at_the_end(self):
        w = self.window
        self.assertTrue(w.browser.banner.isHidden())
        w.browser.set_controlled(True)
        self.assertFalse(w.browser.banner.isHidden())
        stops = []
        w.browser.stop_requested.connect(lambda: stops.append(True))
        w.browser.banner.stop_button.click()
        self.assertEqual(stops, [True])

        w.batch_run = SimpleNamespace(stopped=False, deleteLater=lambda: None)
        w._batch_outcomes = [self.outcome()]
        with mock.patch.object(Notifier, "available", return_value=False), mock.patch.object(QApplication, "alert"):
            w._batch_finished()
        self.assertTrue(w.browser.banner.isHidden())
        self.assertEqual(w.notifier.sent[-1][0], "Run finished")
        self.assertIsNone(w.batch_run)


if __name__ == "__main__":
    unittest.main()
