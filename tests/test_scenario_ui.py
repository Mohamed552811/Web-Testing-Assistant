import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import StepStatus  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.step_table import COL_CLAUDE_STATUS, COL_FINAL_STATUS, COLUMNS, PENDING, RERUN, StepTableModel  # noqa: E402

app = QApplication.instance() or QApplication([])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://app.example"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Login"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def step(self, status=StepStatus.FAIL, action="click Sign in"):
        return sc.add_step(self.db, self.scenario.id, sc.StepInput(action=action, status=status, reasoning="why"))

    def steps(self):
        return sc.scenario_steps(self.db, self.scenario.id)


class StepTableModelTests(Fixture):
    def cell(self, model, row, col, role=Qt.ItemDataRole.DisplayRole):
        return model.data(model.index(row, col), role)

    def test_shows_claude_and_final_status_separately(self):
        first = self.step(StepStatus.FAIL)
        second = self.step(StepStatus.PASS)
        third = self.step(StepStatus.PASS)
        sc.override_step(self.db, first.id, StepStatus.BLOCKED, "env down")
        sc.request_rerun(self.db, third.id)

        model = StepTableModel()
        model.set_steps(self.steps())
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.columnCount(), len(COLUMNS))
        self.assertEqual(self.cell(model, 0, COL_CLAUDE_STATUS), "Fail")
        self.assertEqual(self.cell(model, 0, COL_FINAL_STATUS), "Blocked (override)")
        self.assertEqual(self.cell(model, 0, COLUMNS.index("Review comment")), "env down")
        self.assertEqual(self.cell(model, 1, COL_FINAL_STATUS), PENDING)
        self.assertEqual(self.cell(model, 2, COL_FINAL_STATUS), RERUN)
        self.assertTrue(self.cell(model, 1, COL_FINAL_STATUS, Qt.ItemDataRole.FontRole).italic())
        self.assertIsNone(self.cell(model, 1, COL_FINAL_STATUS, Qt.ItemDataRole.BackgroundRole))
        self.assertIsNotNone(self.cell(model, 0, COL_FINAL_STATUS, Qt.ItemDataRole.BackgroundRole))
        self.assertEqual(model.row_of(third.id), 2)
        self.assertIsNone(model.step_at(5))


class ScenarioTabTests(Fixture):
    def setUp(self):
        super().setUp()
        self.tab = ScenarioTab(self.db)

    def tearDown(self):
        self.tab.deleteLater()
        super().tearDown()

    def test_disabled_without_session(self):
        self.assertFalse(self.tab.isEnabled())
        self.tab.set_session(self.session.id)
        self.assertTrue(self.tab.isEnabled())
        self.assertEqual(self.tab.scenario_list.count(), 1)
        self.assertEqual(self.tab.scenario_count.text(), "1 / 5")

    def test_review_buttons(self):
        step = self.step(StepStatus.FAIL)
        self.tab.set_session(self.session.id)
        self.assertEqual(self.tab.step_model.rowCount(), 1)
        self.assertIn("Not reviewed yet", self.tab.step_details.toPlainText())

        self.tab.comment.setPlainText("confirmed bug")
        self.tab.approve_button.click()
        (loaded,) = self.steps()
        self.assertEqual(loaded.final_status, StepStatus.FAIL)
        self.assertEqual(loaded.latest_review.comment, "confirmed bug")
        self.assertEqual(self.tab.comment.toPlainText(), "")

        self.tab.override_combo.setCurrentIndex(self.tab.override_combo.findData(StepStatus.BLOCKED))
        self.tab.override_button.click()
        self.tab.rerun_button.click()
        (loaded,) = self.steps()
        self.assertEqual(loaded.status, StepStatus.FAIL)  # Claude's finding untouched
        self.assertIsNone(loaded.final_status)
        self.assertEqual(len(loaded.reviews), 3)
        self.assertIn("Re-run requested", self.tab.step_details.toPlainText())
        self.assertEqual(self.tab.step_model.row_of(step.id), 0)

    def test_override_to_same_status_is_refused(self):
        self.step(StepStatus.PASS)
        self.tab.set_session(self.session.id)
        self.tab.override_combo.setCurrentIndex(self.tab.override_combo.findData(StepStatus.PASS))
        with mock.patch.object(QMessageBox, "information") as info:
            self.tab.override_button.click()
        info.assert_called_once()
        self.assertEqual(self.steps()[0].reviews, [])

    def test_new_batch_needs_review_then_confirmation(self):
        step = self.step(StepStatus.PASS)
        self.tab.set_session(self.session.id)

        with mock.patch.object(QMessageBox, "information") as info:
            self.tab.new_batch_button.click()
        info.assert_called_once()
        self.assertEqual(self.tab.batch_combo.count(), 1)

        sc.approve_step(self.db, step.id)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
            self.tab.new_batch_button.click()
        ask.assert_called_once()
        self.assertEqual(self.tab.batch_combo.count(), 1)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.tab.new_batch_button.click()
        self.assertEqual(self.tab.batch, 2)
        self.assertEqual(self.tab.batch_combo.count(), 2)
        self.assertEqual(self.tab.scenario_list.count(), 0)
        self.assertFalse(self.tab.steps_box.isEnabled())

        # Switching back shows batch 1 again.
        self.tab.batch_combo.setCurrentIndex(0)
        self.assertEqual(self.tab.scenario_list.count(), 1)


if __name__ == "__main__":
    unittest.main()


class ScenarioDialogTests(unittest.TestCase):
    """Phase 11: the scenario window has Steps and Expected result."""

    def test_new_scenario_numbers_steps(self):
        from wta.scenario_dialogs import ScenarioDialog
        dialog = ScenarioDialog()
        self.assertFalse(hasattr(dialog, "goal"))
        dialog.name.setText("Price list")
        dialog.steps.setPlainText("Open Orders\n\nClick + Order")
        dialog.expected_result.setPlainText("Rate shows 4.30")
        dialog.accept()
        self.assertEqual(dialog.data(), sc.ScenarioInput("Price list", "1. Open Orders\n2. Click + Order", "Rate shows 4.30", ""))
        edit = ScenarioDialog(None, dialog.data())
        self.assertEqual(edit.steps.toPlainText(), "1. Open Orders\n2. Click + Order")
        self.assertEqual(edit.expected_result.toPlainText(), "Rate shows 4.30")
        dialog.deleteLater()
        edit.deleteLater()
