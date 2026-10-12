import tempfile
import unittest
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from wta.db import Database, store
from wta.db import scenarios as sc
from wta.db.models import Review, ReviewDecision, StepStatus


class ScenarioStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://app.example"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _scenario(self, name="Login", batch=1):
        return sc.add_scenario(self.db, self.session.id, batch, sc.ScenarioInput(name=name))

    def _step(self, scenario_id, status=StepStatus.PASS, action="click"):
        return sc.add_step(self.db, scenario_id, sc.StepInput(action=action, status=status))

    # batches and scenarios

    def test_positions_fill_and_batch_limit(self):
        added = [self._scenario(f"s{i}") for i in range(5)]
        self.assertEqual([s.position for s in added], [1, 2, 3, 4, 5])
        with self.assertRaises(sc.BatchFullError):
            self._scenario("sixth")

    def test_delete_scenario_closes_gap(self):
        a, b, c = (self._scenario(n) for n in "abc")
        sc.delete_scenario(self.db, a.id)
        remaining = sc.batch_scenarios(self.db, self.session.id, 1)
        self.assertEqual([(s.name, s.position) for s in remaining], [("b", 1), ("c", 2)])
        self.assertEqual(self._scenario("d").position, 3)

    def test_scenario_validation_and_update(self):
        with self.assertRaises(ValueError):
            self._scenario("   ")
        s = self._scenario("old")
        sc.update_scenario(self.db, s.id, sc.ScenarioInput(name=" new ", goal=" g ", test_data="d"))
        (loaded,) = sc.batch_scenarios(self.db, self.session.id, 1)
        self.assertEqual((loaded.name, loaded.goal, loaded.test_data), ("new", "g", "d"))

    def test_next_batch_requires_full_review(self):
        self.assertEqual(sc.next_batch_number(self.db, self.session.id), 1)
        s = self._scenario()
        # A scenario with no steps has not been run, so the batch is not reviewed.
        with self.assertRaises(sc.BatchNotReviewedError):
            sc.next_batch_number(self.db, self.session.id)
        step1 = self._step(s.id)
        step2 = self._step(s.id, StepStatus.FAIL)
        sc.approve_step(self.db, step1.id)
        with self.assertRaises(sc.BatchNotReviewedError):
            sc.next_batch_number(self.db, self.session.id)
        sc.override_step(self.db, step2.id, StepStatus.BLOCKED, "env down")
        self.assertEqual(sc.next_batch_number(self.db, self.session.id), 2)

        self._scenario("second batch", batch=2)
        self.assertEqual(sc.batch_numbers(self.db, self.session.id), [1, 2])
        with self.assertRaises(sc.BatchNotReviewedError):
            sc.next_batch_number(self.db, self.session.id)

    # steps

    def test_step_order_and_delete(self):
        s = self._scenario()
        steps = [self._step(s.id, action=f"a{i}") for i in range(3)]
        self.assertEqual([st.order for st in steps], [1, 2, 3])
        sc.delete_step(self.db, steps[0].id)
        self.assertEqual([(st.action, st.order) for st in sc.scenario_steps(self.db, s.id)], [("a1", 1), ("a2", 2)])
        self.assertEqual(self._step(s.id).order, 3)

    def test_step_validation(self):
        s = self._scenario()
        with self.assertRaises(ValueError):
            sc.add_step(self.db, s.id, sc.StepInput(action="  "))
        with self.assertRaises(ValueError):
            sc.add_step(self.db, s.id, sc.StepInput(action="x", status="maybe"))

    def test_update_step_keeps_reviews(self):
        s = self._scenario()
        step = self._step(s.id)
        sc.approve_step(self.db, step.id)
        sc.update_step(self.db, step.id, sc.StepInput(action="changed", status=StepStatus.FAIL))
        (loaded,) = sc.scenario_steps(self.db, s.id)
        self.assertEqual((loaded.action, loaded.status), ("changed", StepStatus.FAIL))
        self.assertEqual(len(loaded.reviews), 1)

    # reviews

    def test_override_keeps_claude_status(self):
        s = self._scenario()
        step = self._step(s.id, StepStatus.FAIL)
        sc.override_step(self.db, step.id, StepStatus.PASS, "false alarm")
        (loaded,) = sc.scenario_steps(self.db, s.id)
        self.assertEqual(loaded.status, StepStatus.FAIL)
        self.assertEqual(loaded.final_status, StepStatus.PASS)
        self.assertEqual(loaded.latest_review.decision, ReviewDecision.OVERRIDDEN)
        self.assertEqual(loaded.latest_review.comment, "false alarm")

    def test_override_to_same_status_rejected(self):
        step = self._step(self._scenario().id, StepStatus.PASS)
        with self.assertRaises(ValueError):
            sc.override_step(self.db, step.id, StepStatus.PASS)

    def test_rerun_makes_step_not_final_and_keeps_history(self):
        s = self._scenario()
        step = self._step(s.id)
        sc.approve_step(self.db, step.id, "looks right")
        sc.request_rerun(self.db, step.id, "flaky")
        (loaded,) = sc.scenario_steps(self.db, s.id)
        self.assertIsNone(loaded.final_status)
        self.assertTrue(loaded.rerun_requested)
        self.assertEqual([r.decision for r in loaded.reviews],
                         [ReviewDecision.APPROVED, ReviewDecision.RERUN_REQUESTED])
        sc.approve_step(self.db, step.id)
        (loaded,) = sc.scenario_steps(self.db, s.id)
        self.assertEqual(loaded.final_status, StepStatus.PASS)
        self.assertFalse(loaded.rerun_requested)

    def test_review_check_constraint(self):
        step = self._step(self._scenario().id)
        for decision, final in ((ReviewDecision.APPROVED, None), (ReviewDecision.RERUN_REQUESTED, StepStatus.PASS)):
            with self.assertRaises(IntegrityError), self.db.Session.begin() as orm:
                orm.add(Review(step_id=step.id, decision=decision, final_status=final))

    def test_missing_ids(self):
        with self.assertRaises(LookupError):
            sc.approve_step(self.db, 999)
        with self.assertRaises(LookupError):
            sc.add_step(self.db, 999, sc.StepInput(action="x"))


if __name__ == "__main__":
    unittest.main()
