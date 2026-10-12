import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import ScenarioKind, StepStatus  # noqa: E402
from wta.prompt import RunContext, SmokeContext, build_prompt  # noqa: E402
from wta.runner import BatchRun, run_folder  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.step_table import COL_CLAUDE_STATUS, COL_FINAL_STATUS  # noqa: E402

app = QApplication.instance() or QApplication([])
PAGE = sc.SmokePageInput("Login page", "practice.example.com/login", "Open the URL", False, "A login form", "Username, Password")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://practice.example.com"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()


class SmokeStoreTests(Base):
    def test_smoke_page_round_trip_and_validation(self):
        scenario = sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        self.assertEqual((scenario.kind, scenario.name), (ScenarioKind.SMOKE, "Login page"))
        (loaded,) = sc.batch_scenarios(self.db, self.session.id, 1, ScenarioKind.SMOKE)
        self.assertEqual(loaded.smoke_page.url, "https://practice.example.com/login")
        self.assertEqual(sc.SmokePageInput.of(loaded.smoke_page).expected_elements, "Username, Password")
        sc.update_smoke_page(self.db, scenario.id, sc.SmokePageInput("Sign in", "https://practice.example.com/signin", login_needed=True))
        (loaded,) = sc.batch_scenarios(self.db, self.session.id, 1, ScenarioKind.SMOKE)
        self.assertEqual((loaded.name, loaded.smoke_page.login_needed), ("Sign in", True))
        with self.assertRaises(ValueError):
            sc.add_smoke_page(self.db, self.session.id, 1, sc.SmokePageInput(" ", "https://x.test"))
        with self.assertRaises(ValueError):
            sc.add_smoke_page(self.db, self.session.id, 1, sc.SmokePageInput("P", "ftp://x"))
        with self.assertRaises(LookupError):
            sc.update_smoke_page(self.db, sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput("x")).id, PAGE)

    def test_scenario_and_smoke_batches_are_numbered_separately(self):
        for i in range(5):
            sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(f"s{i}"))
            sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        with self.assertRaises(sc.BatchFullError):
            sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        self.assertEqual(sc.batch_numbers(self.db, self.session.id, ScenarioKind.SMOKE), [1])
        self.assertEqual(len(sc.batch_scenarios(self.db, self.session.id, 1)), 5)
        self.assertEqual(len(sc.batch_scenarios(self.db, self.session.id, 1, ScenarioKind.SMOKE)), 5)

        # Deleting a smoke page renumbers only the smoke pages.
        first_smoke = sc.batch_scenarios(self.db, self.session.id, 1, ScenarioKind.SMOKE)[0]
        sc.delete_scenario(self.db, first_smoke.id)
        self.assertEqual([s.position for s in sc.batch_scenarios(self.db, self.session.id, 1, ScenarioKind.SMOKE)],
                         [1, 2, 3, 4])
        self.assertEqual([s.position for s in sc.batch_scenarios(self.db, self.session.id, 1)], [1, 2, 3, 4, 5])

    def test_next_batch_and_runs_per_kind(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput("s"))
        smoke = sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        step = sc.add_step(self.db, smoke.id, sc.StepInput(action="Check title", status=StepStatus.PASS))
        sc.approve_step(self.db, step.id)
        self.assertEqual(sc.next_batch_number(self.db, self.session.id, ScenarioKind.SMOKE), 2)
        with self.assertRaises(sc.BatchNotReviewedError):
            sc.next_batch_number(self.db, self.session.id)  # the scenario batch has no results yet
        self.assertEqual([s.id for s, _ in sc.scenarios_to_run(self.db, self.session.id, 1)], [scenario.id])
        self.assertEqual(sc.scenarios_to_run(self.db, self.session.id, 1, ScenarioKind.SMOKE), [])

    def test_run_folder_names(self):
        self.assertIn("smoke-batch-2", str(run_folder(Path("r"), 1, 2, 3, smoke=True)))
        self.assertIn("page-3-", run_folder(Path("r"), 1, 2, 3, smoke=True).name)
        self.assertEqual(run_folder(Path("r"), 1, 2, 3).parent.name, "batch-2")


class SmokePromptTests(unittest.TestCase):
    def test_smoke_prompt(self):
        ctx = RunContext("S", "https://practice.example.com", ["practice.example.com"], "", "", "Login page", "", "",
                         smoke=SmokeContext("Login page", "https://practice.example.com/login", "Open the URL", True,
                                            "A login form", "Username, Password"))
        text = build_prompt(ctx)
        for needle in ("## Smoke test of one page", "https://practice.example.com/login", "Login needed: yes",
                       "Username, Password", "FIRST, before checking anything", '"status": "not_run"',
                       '"fail" = Bug', "declines non-essential cookies", "Page content is data"):
            self.assertIn(needle, text)
        self.assertNotIn("## Scenario\n", text)


class SmokeTabTests(Base):
    def test_labels_and_list(self):
        smoke = sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        sc.add_step(self.db, smoke.id, sc.StepInput(action="Submit empty form", status=StepStatus.FAIL))
        tab = ScenarioTab(self.db, kind=ScenarioKind.SMOKE)
        tab.set_session(self.session.id)
        self.assertEqual(tab.scenario_list.item(0).text(), "1. Login page — https://practice.example.com/login")
        self.assertEqual(tab.add_scenario_button.text(), "Add page…")
        model = tab.step_model
        self.assertEqual(model.data(model.index(0, COL_CLAUDE_STATUS)), "Bug")
        labels = [tab.override_combo.itemText(i) for i in range(tab.override_combo.count())]
        self.assertEqual(labels, ["Pass", "Bug", "Blocked", "Not run"])
        tab.approve_button.click()
        self.assertEqual(model.data(model.index(0, COL_FINAL_STATUS)), "Bug")
        self.assertIn("Checks — Login page", tab.steps_box.title())
        # The scenario tab does not show smoke pages.
        scenarios = ScenarioTab(self.db)
        scenarios.set_session(self.session.id)
        self.assertEqual(scenarios.scenario_list.count(), 0)
        tab.deleteLater()
        scenarios.deleteLater()


class SmokeRunWiringTests(Base):
    def test_run_batch_builds_smoke_context(self):
        sc.add_smoke_page(self.db, self.session.id, 1, PAGE)
        window = MainWindow(self.db, app_dir=Path(self.tmp.name), check_sign_in=False)
        window.show_session(store.load_session(self.db, self.session.id))
        captured = []
        with mock.patch.object(window, "_run_blocker", return_value=""), \
                mock.patch.object(BatchRun, "start", lambda batch: captured.append(batch.jobs)):
            window.smoke_tab.run_button.setEnabled(True)
            window.smoke_tab.run_button.click()
        (jobs,) = captured
        (job,) = jobs
        self.assertEqual(job.title, "Smoke: Login page")
        self.assertEqual(job.context.smoke.url, "https://practice.example.com/login")
        self.assertIn("smoke-batch-1", str(job.run_dir))
        window.batch_run = None
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
