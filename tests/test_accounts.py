"""Phase 16: several test accounts (roles) per session, and the fixtures folder setting."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Bug, Step, StepStatus  # noqa: E402
from wta.export import export_report  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402
from wta.runner import REDACTED, ScenarioJob, ScenarioRun  # noqa: E402
from wta.scenario_dialogs import ScenarioDialog, SmokePageDialog  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.session_dialogs import SessionDialog  # noqa: E402

app = QApplication.instance() or QApplication([])
Account = store.AccountDetails


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def session(self, accounts=(), **kwargs):
        return store.create_session(self.db, store.SessionDetails(
            name="S", target_url="https://shop.test", test_account_email="qa@shop.test",
            test_account_password="main-pw-1", other_accounts=list(accounts), **kwargs))


class StoreTests(Fixture):
    def test_accounts_are_saved_and_edited(self):
        session = self.session([Account(" Sales manager ", "sales@shop.test", "sales-pw-2"),
                                Account("Team B", "teamb@shop.test", "teamb-pw-3")])
        self.assertEqual([(a.role, a.email) for a in session.other_accounts],
                         [("Sales manager", "sales@shop.test"), ("Team B", "teamb@shop.test")])
        details = store.details_of(session)
        sales, team_b = details.other_accounts
        scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Price list"))
        sc.set_account(self.db, scenario.id, team_b.id)

        # Rename one, drop the other, add a third: ids are kept for the renamed one.
        details.other_accounts = [Account("Sales lead", sales.email, sales.password, sales.id),
                                  Account("Read-only", "ro@shop.test", "ro-pw-4")]
        session = store.update_session(self.db, session.id, details)
        self.assertEqual([a.role for a in session.other_accounts], ["Sales lead", "Read-only"])
        self.assertEqual(session.other_accounts[0].id, sales.id)
        self.assertIsNone(store.load_session(self.db, session.id).scenarios[0].account_id)  # Team B is gone

    def test_validation(self):
        cases = [
            [Account("Sales", "")],
            [Account("", "a@shop.test")],
            [Account("Sales", "a@shop.test"), Account("sales", "b@shop.test")],
            [Account("Main account", "a@shop.test")],
            [Account(f"Role {i}", "a@shop.test") for i in range(store.MAX_OTHER_ACCOUNTS + 1)],
        ]
        for accounts in cases:
            with self.assertRaises(ValueError):
                self.session(accounts)

    def test_account_for_choices_and_passwords(self):
        session = self.session([Account("Sales", "sales@shop.test", "sales-pw-2")])
        sales = session.other_accounts[0]
        self.assertEqual(store.account_for(session, None), store.Account("Main account", "qa@shop.test", "main-pw-1"))
        self.assertEqual(store.account_for(session, sales.id).password, "sales-pw-2")
        self.assertEqual(store.account_choices(session),
                         [(None, "Main account (qa@shop.test)"), (sales.id, "Sales (sales@shop.test)")])
        self.assertEqual(store.session_passwords(session), ["main-pw-1", "sales-pw-2"])

    def test_account_must_belong_to_the_session(self):
        one = self.session([Account("Sales", "sales@shop.test", "x")])
        other = self.session()
        scenario = sc.add_scenario(self.db, other.id, 1, sc.ScenarioInput(name="N"))
        with self.assertRaises(ValueError):
            sc.set_account(self.db, scenario.id, one.other_accounts[0].id)

    def test_fixtures_folder(self):
        with self.assertRaises(ValueError):
            self.session(fixtures_dir=str(self.base / "missing"))
        (self.base / "files").mkdir()
        session = self.session(fixtures_dir=str(self.base / "files"))
        self.assertEqual(Path(session.fixtures_dir), (self.base / "files").resolve())
        self.assertEqual(store.details_of(session).fixtures_dir, session.fixtures_dir)


class PromptAndRedactionTests(Fixture):
    def test_role_in_prompt(self):
        ctx = RunContext("S", "https://shop.test", ["shop.test"], "sales@shop.test", "pw", "N", "Open", "",
                         test_account_role="Sales manager")
        self.assertIn("- Role: Sales manager", build_prompt(ctx))
        ctx.test_account_role = ""
        self.assertNotIn("- Role:", build_prompt(ctx))

    def test_runner_redacts_every_password_of_the_session(self):
        session = self.session([Account("Sales", "sales@shop.test", "sales-pw-2")])
        scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="N"))
        job = ScenarioJob(scenario.id, "N", RunContext("S", "https://shop.test", [], "qa@shop.test", "main-pw-1",
                                                       "N", "", ""), self.base / "run", secrets=("sales-pw-2",))
        run = ScenarioRun(self.db, job, 1, self.base / "usage.json")
        self.assertEqual(run.redact("main-pw-1 / sales-pw-2"), f"{REDACTED} / {REDACTED}")

    def test_exports_drop_other_passwords_and_name_the_role(self):
        session = self.session([Account("Sales", "sales@shop.test", "sales-pw-2")])
        scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Price list", steps="Open"))
        sc.set_account(self.db, scenario.id, session.other_accounts[0].id)
        with self.db.Session.begin() as orm:
            orm.add(Step(scenario_id=scenario.id, order=1, action="Sign in with sales-pw-2", status=StepStatus.FAIL,
                         bugs=[Bug(title="Typed sales-pw-2 by mistake")]))
        export_report(self.db, session.id, self.base / "report.md")
        text = "".join(p.read_text("utf-8") for p in self.base.rglob("*.md"))  # every Markdown file written
        self.assertNotIn("sales-pw-2", text)
        self.assertIn("**Run as:** Sales", text)


class DialogTests(Fixture):
    def test_session_dialog_keeps_accounts_and_fixtures(self):
        (self.base / "files").mkdir()
        session = self.session([Account("Sales", "sales@shop.test", "sales-pw-2")], fixtures_dir=str(self.base / "files"))
        dialog = SessionDialog(details=store.details_of(session))
        self.assertEqual(dialog.accounts.table.rowCount(), 1)
        self.assertEqual(dialog.accounts.table.item(0, 2).text(), "••••••")  # never the password itself
        self.assertNotIn("sales-pw-2", "".join(dialog.accounts.table.item(0, c).text() for c in range(3)))
        dialog.accounts.table.selectRow(0)
        dialog.accounts.remove()
        dialog.accept()
        self.assertEqual(dialog.details().other_accounts, [])
        self.assertEqual(dialog.details().fixtures_dir, str((self.base / "files").resolve()))
        dialog.deleteLater()

    def test_run_as_shown_only_with_several_accounts(self):
        single = ScenarioDialog(accounts=[(None, "Main account")])
        self.assertTrue(single.run_as.isHidden())
        self.assertIsNone(single.account_id())
        several = SmokePageDialog(accounts=[(None, "Main account"), (7, "Sales (s@x)")], account_id=7)
        self.assertFalse(several.run_as.isHidden())
        self.assertEqual(several.account_id(), 7)
        single.deleteLater()
        several.deleteLater()

    def test_scenario_list_shows_the_role(self):
        session = self.session([Account("Sales manager", "sales@shop.test", "x")])
        scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Price list"))
        sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Checkout"))
        sc.set_account(self.db, scenario.id, session.other_accounts[0].id)
        tab = ScenarioTab(self.db)
        tab.set_session(session.id)
        self.assertEqual(tab.scenario_list.item(0).text(), "1. Price list — as Sales manager")
        self.assertEqual(tab.scenario_list.item(1).text(), "2. Checkout")
        tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
