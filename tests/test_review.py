"""Phase 13: the review experience (bug edits, badges, summary filter, screenshot and bug cards, keys)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem  # noqa: E402

from wta.db import Database, site_notes, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Bug, ScenarioKind, Screenshot, StepStatus  # noqa: E402
from wta.review_widgets import ScreenshotViewer  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402
from wta.step_table import COL_FINAL_STATUS, StatusBadgeDelegate, StepTableModel, effective_status  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
LOGO = Path(__file__).resolve().parents[1] / "src" / "wta" / "assets" / "logo-64.png"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://app.example"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def step(self, status=StepStatus.PASS, action="do it", bug=False, screenshot=False):
        step = sc.add_step(self.db, self.scenario.id, sc.StepInput(action=action, status=status, reasoning="why"))
        with self.db.Session.begin() as orm:
            if bug:
                orm.add(Bug(step_id=step.id, title="Pay fails", severity="2 - High", priority=1,
                            repro_steps="1. Pay", expected="Order placed", actual="500", report_text="Full"))
            if screenshot:
                shot = Path(self.tmp.name) / f"step-{step.id}.png"
                shutil.copy(LOGO, shot)
                orm.add(Screenshot(step_id=step.id, file_path=str(shot)))
        return step

    def steps(self):
        return sc.scenario_steps(self.db, self.scenario.id)


class BugEditTests(Fixture):
    def test_first_edit_keeps_claudes_original(self):
        self.step(StepStatus.FAIL, bug=True)
        bug = self.steps()[0].bugs[0]
        self.assertIsNone(sc.claude_original(bug))

        sc.update_bug(self.db, bug.id, sc.BugEdit("  Payment returns 500 ", "1 - Critical", 1, actual="HTTP 500"))
        sc.update_bug(self.db, bug.id, sc.BugEdit("Payment returns 500", "1 - Critical", 2))
        bug = self.steps()[0].bugs[0]
        self.assertEqual((bug.title, bug.severity, bug.priority), ("Payment returns 500", "1 - Critical", 2))
        original = sc.claude_original(bug)
        self.assertEqual(original["title"], "Pay fails")  # the second edit did not overwrite it
        self.assertEqual(original["severity"], "2 - High")
        self.assertEqual(original["actual"], "500")

    def test_invalid_edits_are_refused(self):
        self.step(StepStatus.FAIL, bug=True)
        bug_id = self.steps()[0].bugs[0].id
        for edit in (sc.BugEdit(" ", "2 - High", 1), sc.BugEdit("t", "High", 1), sc.BugEdit("t", "2 - High", 7)):
            with self.assertRaises(ValueError):
                sc.update_bug(self.db, bug_id, edit)
        self.assertIsNone(sc.claude_original(self.steps()[0].bugs[0]))


class BadgeTests(Fixture):
    def test_effective_status_and_painting(self):
        reviewed, pending = self.step(StepStatus.FAIL), self.step(StepStatus.PASS)
        sc.override_step(self.db, reviewed.id, StepStatus.BLOCKED)
        steps = self.steps()
        self.assertEqual(effective_status(steps[0]), StepStatus.BLOCKED)
        self.assertEqual(effective_status(steps[1]), StepStatus.PASS)

        model = StepTableModel()
        model.set_steps(steps)
        delegate = StatusBadgeDelegate(model)
        for row in range(2):  # a filled pill (reviewed) and an outlined one (pending)
            image = QImage(200, 30, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            option = QStyleOptionViewItem()
            option.rect = image.rect()
            delegate.paint(painter, option, model.index(row, COL_FINAL_STATUS))
            painter.end()
            drawn = sum(image.pixel(x, y) != 0xFFFFFFFF for x in range(200) for y in range(30))
            self.assertGreater(drawn, 50)


class ReviewPanelTests(Fixture):
    def setUp(self):
        super().setUp()
        self.passed = self.step(StepStatus.PASS, "open cart")
        self.failed = self.step(StepStatus.FAIL, "pay", bug=True, screenshot=True)
        self.blocked = self.step(StepStatus.BLOCKED, "track order", screenshot=True)
        self.tab = ScenarioTab(self.db)
        self.tab.set_session(self.session.id, "app.example")
        self.tab.resize(1200, 900)

    def tearDown(self):
        self.tab.deleteLater()
        super().tearDown()

    def select(self, step):
        self.tab._select_row(self.tab.step_model.row_of(step.id))

    def current(self):
        return self.tab._current_step()

    def press(self, widget, key, text=""):
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text))

    def test_summary_counts_and_filter(self):
        bar = self.tab.filter_bar
        self.assertEqual(bar.counts["all"], 3)
        self.assertEqual(bar.counts["review"], 3)
        self.assertEqual(bar.buttons["fail"].text(), "Fail 1")
        self.assertTrue(bar.buttons["not_run"].isHidden())  # no Not run steps: no button

        self.select(self.blocked)
        bar.buttons["fail"].click()
        self.assertEqual(self.tab._visible_rows(), [1])
        self.assertEqual(self.current().id, self.failed.id)  # selection moved to a visible step

        self.tab.approve()  # the only Fail is now reviewed
        bar.buttons["review"].click()
        self.assertEqual(bar.counts["review"], 2)
        self.assertEqual(self.tab._visible_rows(), [0, 2])
        bar.buttons["all"].click()
        self.assertEqual(self.tab._visible_rows(), [0, 1, 2])

    def test_smoke_tab_calls_failures_bugs(self):
        tab = ScenarioTab(self.db, kind=ScenarioKind.SMOKE)
        self.assertEqual(tab.filter_bar.buttons["all"].text(), "All checks 0")
        tab.filter_bar.set_steps(self.steps())
        self.assertEqual(tab.filter_bar.buttons["fail"].text(), "Bug 1")
        tab.deleteLater()

    def test_cards_follow_the_selected_step(self):
        self.select(self.passed)
        self.assertTrue(self.tab.cards_area.isHidden())
        self.assertIn("Not reviewed yet", self.tab.step_details.toPlainText())

        self.select(self.failed)
        self.assertFalse(self.tab.cards_area.isHidden())
        self.assertFalse(self.tab.screenshot_card.isHidden())
        self.assertFalse(self.tab.screenshot_card.thumbnail.pixmap().isNull())
        self.assertEqual(self.tab.bug_card.title.text(), "Pay fails")
        self.assertEqual(self.tab.bug_card.severity.currentText(), "2 - High")
        self.assertTrue(self.tab.bug_card.revert_button.isHidden())

        self.select(self.blocked)
        self.assertFalse(self.tab.screenshot_card.isHidden())
        self.assertTrue(self.tab.bug_card.isHidden())

    def test_screenshot_opens_full_size(self):
        self.select(self.failed)
        shown = []
        with mock.patch.object(ScreenshotViewer, "exec", lambda viewer: shown.append(viewer.windowTitle())):
            self.tab.screenshot_card.thumbnail.clicked.emit()
        self.assertEqual(shown, ["Step 2 screenshot"])

    def test_missing_screenshot_file_shows_no_card(self):
        self.select(self.blocked)
        Path(self.blocked_path()).unlink()
        self.tab.refresh()
        self.assertTrue(self.tab.screenshot_card.isHidden())

    def blocked_path(self):
        return next(s for s in self.steps() if s.id == self.blocked.id).screenshots[0].file_path

    def test_edit_bug_then_use_claudes_version(self):
        self.select(self.failed)
        card = self.tab.bug_card
        card.title.setText("Payment returns HTTP 500")
        card.severity.setCurrentText("1 - Critical")
        card.save_button.click()

        bug = next(s for s in self.steps() if s.id == self.failed.id).bugs[0]
        self.assertEqual((bug.title, bug.severity), ("Payment returns HTTP 500", "1 - Critical"))
        self.assertEqual(self.current().id, self.failed.id)
        self.assertFalse(card.revert_button.isHidden())
        self.assertIn("Claude's original is kept", card.edited_note.text())

        card.revert_button.click()
        self.assertEqual(card.title.text(), "Pay fails")  # in the fields; saved on Save bug
        card.save_button.click()
        bug = next(s for s in self.steps() if s.id == self.failed.id).bugs[0]
        self.assertEqual(bug.title, "Pay fails")

    def test_empty_bug_title_is_refused(self):
        self.select(self.failed)
        self.tab.bug_card.title.setText("")
        with mock.patch.object(QMessageBox, "information") as info:
            self.tab.bug_card.save_button.click()
        info.assert_called_once()
        self.assertEqual(next(s for s in self.steps() if s.id == self.failed.id).bugs[0].title, "Pay fails")

    def test_save_comment_as_site_note(self):
        saved = []
        self.tab.site_note_saved.connect(lambda: saved.append(True))
        with mock.patch.object(QMessageBox, "information") as info:
            self.tab.site_note_button.click()  # empty comment
        info.assert_called_once()
        self.tab.comment.setPlainText("The coupon field needs a second click")
        with mock.patch("wta.scenario_tab.snackbar.show") as snack:
            self.tab.site_note_button.click()
        self.assertEqual(snack.call_args.args[1], "Site note saved for app.example.")
        self.assertEqual([n.note for n in site_notes.list_notes(self.db, "app.example")],
                         ["The coupon field needs a second click"])
        self.assertEqual(saved, [True])

    def test_site_note_needs_a_site(self):
        self.tab.set_session(self.session.id)
        self.assertFalse(self.tab.site_note_button.isEnabled())

    def test_keys_review_and_move(self):
        self.select(self.passed)
        self.press(self.tab.step_table, Qt.Key.Key_A, "a")
        self.assertEqual(self.steps()[0].final_status, StepStatus.PASS)

        self.press(self.tab, Qt.Key.Key_Down)
        self.assertEqual(self.current().id, self.failed.id)
        self.press(self.tab.step_table, Qt.Key.Key_R, "r")
        self.assertIn("Re-run requested", self.tab.step_details.toPlainText())

        self.press(self.tab, Qt.Key.Key_Down)
        self.tab.override_combo.setCurrentIndex(self.tab.override_combo.findData(StepStatus.PASS))
        self.press(self.tab, Qt.Key.Key_O, "o")
        self.assertEqual(self.steps()[2].final_status, StepStatus.PASS)
        self.press(self.tab, Qt.Key.Key_Up)
        self.assertEqual(self.current().id, self.failed.id)

    def test_keys_do_nothing_while_typing(self):
        self.select(self.passed)
        self.press(self.tab.comment, Qt.Key.Key_A, "a")
        self.assertEqual(self.tab.comment.toPlainText(), "a")
        self.assertIsNone(self.steps()[0].final_status)
        QApplication.sendEvent(self.tab.step_table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                                                              Qt.KeyboardModifier.ControlModifier, "a"))
        self.assertIsNone(self.steps()[0].final_status)


if __name__ == "__main__":
    unittest.main()
