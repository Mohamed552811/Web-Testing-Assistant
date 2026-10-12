import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.exc import IntegrityError, StatementError

from wta.db import Database
from wta.db import store
from wta.db.models import (
    ActionLog,
    Bug,
    CaptureLog,
    Review,
    ReviewDecision,
    RunTrace,
    Scenario,
    ScenarioKind,
    Screenshot,
    SiteNote,
    SmokePage,
    Step,
    StepStatus,
    TestSession,
    TraceKind,
)
from wta.paths import data_dir


def _details(**overrides) -> store.SessionDetails:
    values = dict(
        name="Shop checkout",
        target_url="shop.test/login",
        allowed_domains=["https://shop.test", "cdn.shop.test, auth.shop.test"],
        viewport_width=1440,
        viewport_height=900,
        test_account_email="qa@shop.test",
        test_account_password="secret-test-pw",
    )
    values.update(overrides)
    return store.SessionDetails(**values)


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wta.sqlite3"
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reopen(self) -> Database:
        self.db.close()
        self.db = Database(self.path)
        return self.db


class SessionRoundTripTests(DbTestCase):
    def test_session_with_everything_survives_reopen(self):
        created = store.create_session(self.db, _details(), browser_version="Chromium 140")

        with self.db.Session.begin() as orm:
            scenario = Scenario(
                session_id=created.id, batch_number=1, position=1, name="Buy a book",
                goal="Checkout works", test_data="ISBN 123",
            )
            smoke = Scenario(session_id=created.id, batch_number=1, position=2, kind=ScenarioKind.SMOKE, name="Cart page")
            smoke.smoke_page = SmokePage(page_name="Cart", url="http://shop.test/cart", login_needed=True)
            step = Step(order=1, screen_name="Login", element="Email", action="type qa@shop.test",
                        expected_result="Accepted", actual_result="Error shown", status=StepStatus.FAIL,
                        reasoning="Login form rejected valid account")
            step.screenshots.append(Screenshot(file_path="shots/1.png"))
            step.capture_logs.append(CaptureLog(console_errors=["TypeError: x"], failed_requests=["POST /login 500"],
                                                page_url="http://shop.test/login"))
            step.action_logs.append(ActionLog(action="type", target="#email", outcome="ok"))
            step.bugs.append(Bug(title="Login fails with valid account", severity="2 - High", priority=1,
                                 repro_steps="1. Open login", expected="Logged in", actual="500 error"))
            step.reviews.append(Review(decision=ReviewDecision.OVERRIDDEN, final_status=StepStatus.BLOCKED,
                                       comment="Env was down"))
            scenario.steps.append(step)
            scenario.steps.append(Step(order=2, action="click Buy"))
            scenario.traces.append(RunTrace(kind=TraceKind.NOTE, text="Starting login"))
            orm.add_all([scenario, smoke, SiteNote(site="shop.test", note="Cookie banner first")])

        db = self.reopen()
        session = store.load_session(db, created.id)

        self.assertEqual(session.name, "Shop checkout")
        self.assertEqual(session.target_url, "http://shop.test/login")
        self.assertEqual(session.allowed_domains, ["shop.test", "cdn.shop.test", "auth.shop.test"])
        self.assertEqual((session.viewport_width, session.viewport_height), (1440, 900))
        self.assertEqual(session.browser_version, "Chromium 140")
        self.assertEqual(session.test_account_email, "qa@shop.test")
        self.assertEqual(session.test_account_password, "secret-test-pw")
        self.assertIsNotNone(session.created_at)

        first, second = session.scenarios
        self.assertEqual(first.kind, ScenarioKind.SCENARIO)
        self.assertEqual(second.kind, ScenarioKind.SMOKE)
        self.assertTrue(second.smoke_page.login_needed)
        self.assertEqual([s.order for s in first.steps], [1, 2])

        step = first.steps[0]
        # Claude's finding and the override are both kept.
        self.assertEqual(step.status, StepStatus.FAIL)
        self.assertEqual(step.final_status, StepStatus.BLOCKED)
        self.assertEqual(step.latest_review.comment, "Env was down")
        self.assertEqual(step.bugs[0].severity, "2 - High")
        self.assertEqual(step.capture_logs[0].failed_requests, ["POST /login 500"])
        self.assertEqual(step.screenshots[0].file_path, "shots/1.png")
        self.assertEqual(step.action_logs[0].target, "#email")
        self.assertEqual(first.traces[0].kind, TraceKind.NOTE)
        self.assertIsNone(first.steps[1].final_status)
        self.assertEqual(first.steps[1].status, StepStatus.NOT_RUN)

    def test_update_and_list(self):
        a = store.create_session(self.db, _details(name="A"))
        b = store.create_session(self.db, _details(name="B"))
        store.update_session(self.db, a.id, _details(name="A2", target_url="https://other.example/x",
                                                     allowed_domains=[]))
        db = self.reopen()
        self.assertEqual([s.name for s in store.list_sessions(db)], ["B", "A2"])
        a2 = store.load_session(db, a.id)
        self.assertEqual(a2.allowed_domains, ["other.example"])
        self.assertEqual(store.details_of(a2).target_url, "https://other.example/x")
        self.assertNotEqual(b.id, a.id)

    def test_deleting_session_cascades(self):
        s = store.create_session(self.db, _details())
        with self.db.Session.begin() as orm:
            sc = Scenario(session_id=s.id, batch_number=1, position=1, name="x")
            sc.steps.append(Step(order=1, reviews=[Review(decision=ReviewDecision.APPROVED,
                                                          final_status=StepStatus.PASS)]))
            orm.add(sc)
        with self.db.Session.begin() as orm:
            orm.delete(orm.get(TestSession, s.id))
        with self.db.Session() as orm:
            self.assertEqual(orm.query(Step).count(), 0)
            self.assertEqual(orm.query(Review).count(), 0)

    def test_missing_session(self):
        with self.assertRaises(LookupError):
            store.load_session(self.db, 999)


class ConstraintTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.session = store.create_session(self.db, _details())

    def _add(self, obj):
        with self.db.Session.begin() as orm:
            orm.add(obj)

    def test_position_limited_to_five(self):
        with self.assertRaises(IntegrityError):
            self._add(Scenario(session_id=self.session.id, batch_number=1, position=6, name="x"))

    def test_slot_is_unique(self):
        self._add(Scenario(session_id=self.session.id, batch_number=1, position=1, name="x"))
        with self.assertRaises(IntegrityError):
            self._add(Scenario(session_id=self.session.id, batch_number=1, position=1, name="y"))
        self._add(Scenario(session_id=self.session.id, batch_number=2, position=1, name="z"))

    def test_foreign_keys_enforced(self):
        with self.assertRaises(IntegrityError):
            self._add(Scenario(session_id=12345, batch_number=1, position=1, name="x"))

    def test_bug_severity_and_priority(self):
        sc = Scenario(session_id=self.session.id, batch_number=1, position=1, name="x")
        sc.steps.append(Step(order=1))
        self._add(sc)
        step_id = sc.steps[0].id
        with self.assertRaises(IntegrityError):
            self._add(Bug(step_id=step_id, title="t", severity="Severe"))
        with self.assertRaises(IntegrityError):
            self._add(Bug(step_id=step_id, title="t", priority=9))

    def test_invalid_status_rejected(self):
        with self.assertRaises((StatementError, LookupError, ValueError)):
            self._add(Step(scenario_id=1, order=1, status="maybe"))


class ValidationTests(unittest.TestCase):
    def test_parse_domains(self):
        self.assertEqual(
            store.parse_domains("https://A.example.com/path, b.example.com:8080\nlocalhost:3000 a.example.com"),
            ["a.example.com", "b.example.com", "localhost"],
        )
        self.assertEqual(store.parse_domains(""), [])

    def test_validate_errors(self):
        with self.assertRaises(ValueError):
            store.validate(_details(name="  "))
        with self.assertRaises(ValueError):
            store.validate(_details(target_url="ftp://x"))
        with self.assertRaises(ValueError):
            store.validate(_details(viewport_width=0))

    def test_target_host_always_allowed(self):
        cleaned = store.validate(_details(target_url="https://app.example.com", allowed_domains=["api.example.com"]))
        self.assertEqual(cleaned.allowed_domains, ["app.example.com", "api.example.com"])

    def test_smoke_labels(self):
        self.assertEqual(StepStatus.FAIL.smoke_label, "Bug")
        self.assertEqual(StepStatus.PASS.smoke_label, "Pass")


class PathsTests(unittest.TestCase):
    def test_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "data"
            old = os.environ.get("WTA_DATA_DIR")
            os.environ["WTA_DATA_DIR"] = str(target)
            try:
                self.assertEqual(data_dir(), target)
                self.assertTrue(target.is_dir())
            finally:
                if old is None:
                    del os.environ["WTA_DATA_DIR"]
                else:
                    os.environ["WTA_DATA_DIR"] = old


if __name__ == "__main__":
    unittest.main()
