"""Phase 17: run history, the scenario library, comparing runs and importing test cases."""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from wta import compare  # noqa: E402
from wta.db import Database, library, store  # noqa: E402
from wta.db import runs  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Run, RunOutcomeKind, ScenarioKind, StepStatus, utcnow  # noqa: E402
from wta.import_dialog import import_into_library  # noqa: E402
from wta.library_dialog import LibraryDialog  # noqa: E402
from wta.runs_tab import RunsTab  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.test_case_import import import_file, parse_rows  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


def step(order, action, status, bug=None):
    return {"order": order, "action": action, "status": status,
            "bug": {"title": bug, "severity": "2 - High"} if bug else None}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        self.session = self.new_session("Build 41")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def new_session(self, name):
        return store.create_session(self.db, store.SessionDetails(
            name=name, target_url="https://shop.test", test_account_password="pw-secret-7"))

    def finished_run(self, scenario_id, steps, minutes_ago=0):
        run_id = runs.start_run(self.db, scenario_id, self.base / f"run-{scenario_id}-{minutes_ago}")
        runs.finish_run(self.db, run_id, runs.RunEnd(outcome=RunOutcomeKind.FINISHED))
        with self.db.Session.begin() as orm:
            orm.get(Run, run_id).started_at = utcnow() - timedelta(minutes=minutes_ago)
        runs.save_run_steps(self.db, run_id, steps, lambda t: t.replace("pw-secret-7", "[test password]"))
        return run_id


class RunStepTests(Fixture):
    def test_each_run_keeps_its_own_steps(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout"))
        first = self.finished_run(scenario.id, [step(1, "Open cart", "pass"), step(2, "Pay", "bug", "Pay fails")], 10)
        second = self.finished_run(scenario.id, [step(1, "Open cart", "pass"), step(2, "Pay", "pass")], 0)
        self.assertEqual([s.status for s in runs.run_steps(self.db, first)], [StepStatus.PASS, StepStatus.FAIL])
        self.assertEqual(runs.run_steps(self.db, first)[1].bug_title, "Pay fails")
        self.assertEqual([s.status for s in runs.run_steps(self.db, second)], [StepStatus.PASS, StepStatus.PASS])

    def test_steps_are_redacted(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login"))
        run_id = self.finished_run(scenario.id, [step(1, "Typed pw-secret-7", "pass")])
        self.assertEqual(runs.run_steps(self.db, run_id)[0].action, "Typed [test password]")

    def test_backfill_from_run_folders(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Old"))
        folder = self.base / "old-run"
        folder.mkdir()
        (folder / "results.json").write_text(json.dumps({"steps": [step(1, "Sign in with pw-secret-7", "Passed"),
                                                                   step(2, "Pay", "blocked")]}), "utf-8")
        run_id = runs.start_run(self.db, scenario.id, folder)
        runs.finish_run(self.db, run_id, runs.RunEnd(outcome=RunOutcomeKind.FINISHED))
        self.assertEqual(runs.backfill_run_steps(self.db), 1)
        kept = runs.run_steps(self.db, run_id)
        self.assertEqual([(s.status, s.action) for s in kept],
                         [(StepStatus.PASS, "Sign in with [test password]"), (StepStatus.BLOCKED, "Pay")])
        self.assertEqual(runs.backfill_run_steps(self.db), 0)  # only once


class CompareTests(Fixture):
    def test_compare_steps(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout"))
        before = self.finished_run(scenario.id, [
            step(1, "Open cart", "pass"), step(2, "Apply coupon", "pass"), step(3, "Pay", "fail", "Pay fails"),
            step(4, "Track order", "blocked"), step(5, "Print invoice", "pass")], 10)
        after = self.finished_run(scenario.id, [
            step(1, "Open cart", "pass"), step(2, "Apply  coupon!", "fail", "Coupon ignored"), step(3, "Pay", "pass"),
            step(4, "Track order", "not_run"), step(5, "Rate the order", "pass")], 0)
        result = compare.compare_steps(runs.run_steps(self.db, before), runs.run_steps(self.db, after))
        # "Apply  coupon!" matches "Apply coupon" by its words; "Rate the order" has no match by text, so it is
        # matched by position with "Print invoice" (both Pass: the same).
        self.assertEqual([(c.kind, c.action, c.before, c.after) for c in result.changes], [
            (compare.REGRESSION, "Apply  coupon!", StepStatus.PASS, StepStatus.FAIL),
            (compare.FIXED, "Pay", StepStatus.FAIL, StepStatus.PASS),
            (compare.CHANGED, "Track order", StepStatus.BLOCKED, StepStatus.NOT_RUN),
        ])
        self.assertEqual(result.unchanged, 2)
        self.assertEqual(result.changes[0].bug, "Coupon ignored")
        self.assertEqual(result.changes[1].bug, "Pay fails")
        self.assertEqual(result.count(compare.REGRESSION), 1)
        self.assertEqual(result.count(compare.FIXED), 1)
        self.assertIn("1 regression, 1 fixed", result.summary())

    def test_new_and_gone_steps(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="N"))
        before = self.finished_run(scenario.id, [step(1, "A", "pass"), step(2, "B", "pass")], 10)
        after = self.finished_run(scenario.id, [step(1, "A", "pass"), step(2, "B", "pass"), step(3, "C", "pass")], 0)
        result = compare.compare_steps(runs.run_steps(self.db, before), runs.run_steps(self.db, after))
        self.assertEqual([(c.kind, c.action) for c in result.changes], [(compare.NEW, "C")])
        result = compare.compare_steps(runs.run_steps(self.db, after), runs.run_steps(self.db, before))
        self.assertEqual([(c.kind, c.action) for c in result.changes], [(compare.GONE, "C")])
        self.assertEqual(result.summary(), "1 no longer run; 2 the same")

    def test_previous_run_of_the_same_test_across_sessions(self):
        item, = library.add_items(self.db, [library.ItemInput(scenario=sc.ScenarioInput("Checkout", "Open\nPay"))])
        old, = library.add_to_batch(self.db, self.session.id, 1, [item.id], ScenarioKind.SCENARIO)
        older_run = self.finished_run(old.id, [step(1, "Open", "pass"), step(2, "Pay", "pass")], 60)
        build_42 = self.new_session("Build 42")
        new, = library.add_to_batch(self.db, build_42.id, 1, [item.id], ScenarioKind.SCENARIO)
        new_run = self.finished_run(new.id, [step(1, "Open", "pass"), step(2, "Pay", "fail", "500 on pay")], 0)
        other = sc.add_scenario(self.db, build_42.id, 1, sc.ScenarioInput(name="Unrelated"))
        self.finished_run(other.id, [step(1, "Open", "fail")], 30)

        previous, comparison = compare.compare_with_previous(self.db, new_run)
        self.assertEqual(previous.id, older_run)
        self.assertEqual(previous.scenario.session.name, "Build 41")
        self.assertEqual(comparison.count(compare.REGRESSION), 1)
        self.assertEqual(compare.compare_with_previous(self.db, older_run), (None, None))
        html = compare.comparison_html(previous, comparison, lambda t: "then")
        self.assertIn("session “Build 41”", html)
        self.assertIn("Regression", html)

    def test_runs_tab_compare_button(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout"))
        self.finished_run(scenario.id, [step(1, "Pay", "pass")], 10)
        self.finished_run(scenario.id, [step(1, "Pay", "fail", "Pay fails")], 0)
        tab = RunsTab(self.db)
        tab.set_session(self.session.id, "shop.test")
        self.assertEqual(tab.steps_table.item(0, 1).text(), "fail")  # from the kept run steps
        with mock.patch.object(QDialog, "exec", return_value=0):
            tab.compare_button.click()
        self.assertIn("1 regression", tab._comparison_dialog.findChild(type(tab.findings)).toPlainText())
        tab.runs_table.selectRow(1)  # the older run has nothing before it
        with mock.patch.object(QMessageBox, "information") as info:
            tab.compare_button.click()
        info.assert_called_once()
        tab.deleteLater()


class LibraryTests(Fixture):
    def test_save_update_and_add_to_batch(self):
        scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput("Checkout", "Open\nPay", "Paid"))
        item, created = library.save_scenario(self.db, scenario.id)
        self.assertTrue(created)
        self.assertEqual((item.name, item.test_steps, item.expected_result), ("Checkout", "1. Open\n2. Pay", "Paid"))
        sc.update_scenario(self.db, scenario.id, sc.ScenarioInput("Checkout v2", "Open\nPay\nTrack", "Paid"))
        again, created = library.save_scenario(self.db, scenario.id)
        self.assertFalse(created)
        self.assertEqual((again.id, again.name), (item.id, "Checkout v2"))

        build_42 = self.new_session("Build 42")
        added = library.add_to_batch(self.db, build_42.id, 1, [item.id], ScenarioKind.SCENARIO)
        self.assertEqual(added[0].template_id, item.id)
        self.assertEqual(added[0].test_steps, "1. Open\n2. Pay\n3. Track")
        with self.assertRaises(ValueError):  # smoke pages belong on the smoke tab
            library.add_to_batch(self.db, build_42.id, 1, [item.id], ScenarioKind.SMOKE)

    def test_smoke_pages(self):
        page = sc.add_smoke_page(self.db, self.session.id, 1, sc.SmokePageInput("Home", "https://shop.test/",
                                                                                login_needed=True))
        item, _ = library.save_scenario(self.db, page.id)
        self.assertEqual((item.kind, item.url, item.login_needed), (ScenarioKind.SMOKE, "https://shop.test/", True))
        added, = library.add_to_batch(self.db, self.new_session("B").id, 1, [item.id], ScenarioKind.SMOKE)
        loaded = sc.batch_scenarios(self.db, added.session_id, 1, ScenarioKind.SMOKE)[0]
        self.assertEqual((loaded.smoke_page.url, loaded.smoke_page.login_needed), ("https://shop.test/", True))

    def test_batch_limit_and_delete(self):
        items = library.add_items(self.db, [library.ItemInput(scenario=sc.ScenarioInput(f"Case {i}"))
                                            for i in range(6)])
        with self.assertRaises(sc.BatchFullError):
            library.add_to_batch(self.db, self.session.id, 1, [i.id for i in items], ScenarioKind.SCENARIO)
        added = library.add_to_batch(self.db, self.session.id, 2, [items[0].id], ScenarioKind.SCENARIO)
        library.delete_item(self.db, items[0].id)
        kept = store.load_session(self.db, self.session.id).scenarios
        self.assertEqual([(s.id, s.template_id) for s in kept if s.id == added[0].id], [(added[0].id, None)])
        self.assertEqual([i.name for i in library.list_items(self.db, search="case 5")], ["Case 5"])

    def test_library_dialog_and_tab(self):
        library.add_items(self.db, [library.ItemInput(scenario=sc.ScenarioInput(f"Case {i}", "Do it"))
                                    for i in range(3)])
        dialog = LibraryDialog(self.db, ScenarioKind.SCENARIO, room=2)
        self.assertEqual(dialog.items.count(), 3)
        self.assertFalse(dialog.add_button.isEnabled())
        for row in range(3):
            dialog.items.item(row).setCheckState(Qt.CheckState.Checked)
        self.assertFalse(dialog.add_button.isEnabled())  # 3 chosen, room for 2
        dialog.items.item(2).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(dialog.add_button.isEnabled())
        self.assertEqual(len(dialog.chosen_ids()), 2)
        dialog.deleteLater()

        tab = ScenarioTab(self.db)
        tab.set_session(self.session.id)
        chosen = [i.id for i in library.list_items(self.db)][:2]
        with mock.patch.object(LibraryDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch.object(LibraryDialog, "chosen_ids", return_value=chosen):
            tab.from_library_button.click()
        self.assertEqual([tab.scenario_list.item(i).text() for i in range(2)], ["1. Case 0", "2. Case 1"])
        tab.deleteLater()


class ImportTests(Fixture):
    def test_one_row_per_case(self):
        result = parse_rows([
            ["Title", "Steps", "Expected Result", "Test Data"],
            ["Log in", "Open the login page\nEnter the account\nPress Sign in", "Home page shows", "user A"],
            ["", "", "", ""],
            ["Log out", "Click Sign out", "Login page shows", ""],
        ])
        self.assertEqual(result.warnings, [])
        self.assertEqual([c.name for c in result.cases], ["Log in", "Log out"])
        self.assertEqual(result.cases[0].steps, "1. Open the login page\n2. Enter the account\n3. Press Sign in")
        self.assertEqual((result.cases[0].expected_result, result.cases[0].test_data), ("Home page shows", "user A"))

    def test_azure_devops_export(self):
        result = parse_rows([
            ["ID", "Work Item Type", "Title", "Test Step", "Step Action", "Step Expected", "State"],
            ["101", "Test Case", "Apply price list", "", "", "", "Design"],
            ["", "", "", "1", "<DIV><P>Open a sales order</P></DIV>", "", ""],
            ["", "", "", "2", "<P>Choose price list &amp; save</P>", "Price list applied", ""],
            ["102", "Test Case", "Cancel order", "", "", "", "Design"],
            ["", "", "", "1", "Open the order", "", ""],
            ["", "", "", "2", "Click Cancel", "Order is cancelled", ""],
        ])
        self.assertEqual([c.name for c in result.cases], ["Apply price list", "Cancel order"])
        self.assertEqual(result.cases[0].steps, "1. Open a sales order\n2. Choose price list & save")
        self.assertEqual(result.cases[0].expected_result, "Price list applied")

    def test_files_and_errors(self):
        csv_path = self.base / "cases.csv"
        csv_path.write_text('Name;Steps;Expected\n;Stray step;\nSearch;"Type a word\nPress Enter";Results show\n',
                            "utf-8")
        result = import_file(csv_path)
        self.assertEqual([c.name for c in result.cases], ["Search"])
        self.assertEqual(result.cases[0].steps, "1. Type a word\n2. Press Enter")  # a quoted multi-line cell
        self.assertIn("Row 2 has a step but no test case above it", result.warnings[0])

        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["Test Case Title", "Test Steps", "Expected Results"])
        book.active.append(["Export report", "Open session\nExport", "File saved"])
        xlsx = self.base / "cases.xlsx"
        book.save(xlsx)
        self.assertEqual(import_file(xlsx).cases[0].steps, "1. Open session\n2. Export")

        self.assertIn("No Name or Title column", parse_rows([["Foo"], ["bar"]]).warnings[0])
        self.assertIn("empty", parse_rows([]).warnings[0])
        broken = self.base / "broken.xlsx"
        broken.write_bytes(b"not a workbook")
        self.assertIn("Could not read broken.xlsx", import_file(broken).warnings[0])

    def test_import_into_library(self):
        path = self.base / "cases.csv"
        path.write_text("Title,Steps,Expected Result\nLog in,Open the page,Home shows\n", "utf-8")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            added = import_into_library(self.db, None, str(path))
        self.assertEqual([(i.name, i.source) for i in added], [("Log in", "Imported from cases.csv")])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertEqual(import_into_library(self.db, None, str(path)), [])
        self.assertEqual(len(library.list_items(self.db)), 1)


if __name__ == "__main__":
    unittest.main()
