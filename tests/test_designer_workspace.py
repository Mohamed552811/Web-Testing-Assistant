"""M4: backlog search (spec 3.4, 3.5), notes and context, pictures, results editing, open questions, and the
workspace that ties them together. Generation is faked; nothing calls Claude here."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QObject, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from wta.db import Database  # noqa: E402
from wta.db import designer_content as content  # noqa: E402
from wta.db import designer_settings, generations  # noqa: E402
from wta.db import workbooks as wb  # noqa: E402
from wta.designer import workspace as workspace_module  # noqa: E402
from wta.designer.generation import CallResult, Planner  # noqa: E402
from wta.designer.search import content_problem, find, parse_request  # noqa: E402
from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings, OutputPreset  # noqa: E402
from wta.designer.validation import case_warnings  # noqa: E402
from wta.designer.workspace import ALL_SHEETS, DesignerWorkspace  # noqa: E402

try:
    from designer_xlsx import Pic, SheetSpec, build, png
    from qt_app import app  # noqa: E402
    from test_designer_generation import case, response
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import Pic, SheetSpec, build, png
    from tests.qt_app import app  # noqa: E402
    from tests.test_designer_generation import case, response


def workbook_sheets() -> list[SheetSpec]:
    return [
        SheetSpec("Main", {"A1": 1, "B1": "Login", "B2": "The user signs in.",
                           "A4": 7, "B4": "Orders", "B5": "Orders can be exported.",
                           "A7": 10, "B7": "Empty one",
                           "A9": 7, "B9": "Orders again", "B10": "Second block?"},
                  [Pic("B3", png(seed=1)), Pic("B6", png(seed=2))]),
        SheetSpec("Extra", {"A1": "BL-7", "B1": "Other orders", "B2": "Text."}),
    ]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = Database(self.dir / "wta.sqlite3")
        path = self.dir / "Backlogs.xlsx"
        path.write_bytes(build(workbook_sheets()))
        self.view = wb.load_workbook(self.db, wb.import_workbook(self.db, path).workbook_id)
        self.main, self.extra = self.view.sheets
        self.orders = self.main.backlogs[1]

    def tearDown(self):
        app.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def generate_saved(self, backlog, *cases, questions=("Which formats?",)) -> int:
        """Store a valid generation for a backlog, as a real run would."""
        request = generations.request_for_backlog(self.db, backlog.id, DesignerSettings())
        outcome = Planner(request).on_result("first", CallResult(
            structured=response(*cases, questions=list(questions)), subtype="success", model="claude-opus-5-5"))
        self.assertTrue(outcome.ok, outcome.validation_errors)
        return generations.save_outcome(self.db, backlog.id, outcome)


class SearchTests(Fixture):
    def test_request_forms(self):
        for text in ("backlog 5", "5", "#5", "  Backlog #5 ", "backlog no. 5", "05"):
            self.assertEqual(parse_request(text), "5", text)
        self.assertEqual(parse_request("BL-5"), "BL-5")
        self.assertEqual(parse_request("backlog bl-5"), "bl-5")
        for text in ("", "five", "5x", "backlog"):
            self.assertIsNone(parse_request(text), text)

    def test_exact_match_scoped_to_a_sheet(self):
        self.assertEqual([m.backlog.number for m in find(self.view.sheets, "1", self.main.id).matches], ["1"])
        self.assertEqual(find(self.view.sheets, "#10", self.main.id).unique.backlog.title, "Empty one")
        missing = find(self.view.sheets, "backlog 12", self.main.id)
        self.assertEqual(missing.matches, [])
        self.assertEqual(missing.message, "Backlog 12 not found in sheet “Main”. Backlogs on this sheet: 1, 7, 10, 7.")
        self.assertIn("not a backlog number", find(self.view.sheets, "hello", self.main.id).message)

    def test_duplicates_and_all_sheets(self):
        twice = find(self.view.sheets, "7", self.main.id)
        self.assertEqual([m.backlog.occurrence for m in twice.matches], [1, 2])
        self.assertIsNone(twice.unique)
        self.assertIn("occurrence 2", twice.matches[1].label)
        self.assertEqual(find(self.view.sheets, "bl-7").unique.sheet.name, "Extra")  # case-insensitive key
        self.assertIn("any sheet", find(self.view.sheets, "99").message)

    def test_content_problems(self):
        empty = self.main.backlogs[2]
        self.assertIn("Add notes", content_problem(empty, has_notes=False))
        self.assertIn("from your notes only", content_problem(empty, has_notes=True))
        self.assertEqual(content_problem(self.orders, has_notes=False), "")


class ContentTests(Fixture):
    def test_notes_and_context(self):
        first = content.add_context(self.db, self.orders.id, content.DOMAIN_NOTE, " Managers only. ")
        content.add_context(self.db, self.orders.id, content.SUPPLEMENTARY, "Formats: CSV.", "pasted text")
        self.assertEqual(content.texts(self.db, self.orders.id, content.DOMAIN_NOTE), ["Managers only."])
        content.update_context(self.db, first, "Managers and admins.")
        self.assertEqual(content.texts(self.db, self.orders.id, content.DOMAIN_NOTE), ["Managers and admins."])
        content.delete_context(self.db, first)
        self.assertEqual([c.kind for c in content.list_context(self.db, self.orders.id)], [content.SUPPLEMENTARY])
        with self.assertRaises(ValueError):
            content.add_context(self.db, self.orders.id, content.DOMAIN_NOTE, "  ")
        text, source = content.backlog_as_context(self.db, self.main.backlogs[0].id)
        self.assertIn("Backlog 1 \"Login\"", text)
        self.assertIn("B2: The user signs in.", text)
        self.assertIn("IMG-01", text)
        self.assertTrue(source.endswith("Main · #1"))

    def test_open_question_answers_become_notes(self):
        self.assertEqual(content.list_questions(self.db, self.orders.id), [])
        self.generate_saved(self.orders, case(1))
        [question] = content.list_questions(self.db, self.orders.id)
        content.answer_question(self.db, question.id, "CSV and PDF.")
        [answered] = content.list_questions(self.db, self.orders.id)
        self.assertEqual((answered.answered, answered.answer), (True, "CSV and PDF."))
        self.assertEqual(content.texts(self.db, self.orders.id, content.DOMAIN_NOTE),
                         ["Which formats? Answer: CSV and PDF."])

    def test_case_edits(self):
        self.generate_saved(self.orders, case(1), case(2, "corner_case"), case(3, "corner_case"))
        cases = content.current_cases(self.db, self.orders.id)
        self.assertEqual([c.data["id"] for c in cases], ["TC-007-01", "TC-007-02", "TC-007-03"])
        first = cases[0]
        changed = dict(first.data, title="Verify that the export works")
        content.update_case(self.db, first.row_id, changed)
        content.set_locked(self.db, first.row_id, True)
        edited = content.current_cases(self.db, self.orders.id)[0]
        self.assertEqual((edited.data["title"], edited.edited, edited.locked), ("Verify that the export works", True, True))
        copy_id = content.duplicate_case(self.db, cases[1].row_id)
        content.delete_case(self.db, cases[2].row_id)
        content.move_case(self.db, copy_id, -5)
        order = content.current_cases(self.db, self.orders.id)
        self.assertEqual([c.row_id for c in order][0], copy_id)
        self.assertEqual([c.seq for c in order], [1, 2, 3])
        self.assertTrue(order[0].data["title"].endswith("(copy)"))
        new_id = content.add_case(self.db, self.orders.id, content.blank_case(DesignerSettings(), "7", 4))
        self.assertEqual(content.current_cases(self.db, self.orders.id)[-1].row_id, new_id)
        content.renumber(self.db, self.orders.id, DesignerSettings(), "7")
        self.assertEqual([c.data["id"] for c in content.current_cases(self.db, self.orders.id)],
                         ["TC-007-01", "TC-007-02", "TC-007-03", "TC-007-04"])
        with self.assertRaises(LookupError):
            content.add_case(self.db, self.main.backlogs[0].id, {})  # no generation yet

    def test_blank_case_follows_the_preset(self):
        settings = DesignerSettings()
        settings.output_presets.append(OutputPreset(name="P", id_prefix="PD", backlog_pad=0, seq_pad=0,
                                                    steps_style=PER_STEP_EXPECTED,
                                                    optional_fields={"basis": False}))
        settings.output_preset = "P"
        blank = content.blank_case(settings, "BL-7", 3)
        self.assertEqual((blank["id"], blank["backlog_number"]), ("PD-7-3", "BL-7"))
        self.assertNotIn("basis", blank)
        self.assertIsInstance(blank["steps"][0], dict)

    def test_case_warnings(self):
        cases = [case(1, "corner_case"), case(2, title="Check it", steps=["Open it."], expected_result="",
                                                 tags=["regression", "nightly"]), case(4)]
        cases[2]["title"] = cases[0]["title"]
        warnings = case_warnings(cases, DesignerSettings(), "7")
        self.assertIn("Another test case has the same title.", warnings[0])
        text = " ".join(warnings[1])
        for expected in ("should start with \"Verify that\"", "No step starts with", "Expected result is empty",
                         "should come before", "nightly"):
            self.assertIn(expected, text)
        self.assertIn("ID is out of sequence (use Renumber).", warnings[2])


class FakeRun(QObject):
    """Stands in for GenerationRun: answers with a prepared outcome."""

    progress = Signal(str)
    finished = Signal(object)
    created: list = []
    answer = None

    def __init__(self, request, work_root, usage_path=None, parent=None):
        super().__init__(parent)
        self.request = request
        FakeRun.created.append(self)

    def start(self):
        planner = Planner(self.request)
        self.progress.emit("Asking Claude for test cases…")
        QTimer.singleShot(0, lambda: self.finished.emit(planner.on_result("first", FakeRun.answer)))

    def cancel(self):
        pass


class WorkspaceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.workspace = DesignerWorkspace(self.db, self.dir / "runs", None)
        self.remembered = {}
        self.workspace.remember = self.remembered.__setitem__
        self.workspace.recall = self.remembered.get
        self.workspace.resize(1400, 900)
        self.workspace.show()
        self.workspace.set_workbook(self.view.id, "Backlogs.xlsx (v1)")
        FakeRun.created = []

    def tearDown(self):
        self.workspace.deleteLater()
        super().tearDown()

    def wait(self, condition, tries=200):
        for _ in range(tries):
            if condition():
                return
            app.processEvents()

    def test_sheets_list_and_search(self):
        w = self.workspace
        self.assertEqual([w.sheet_combo.itemData(i) for i in range(w.sheet_combo.count())],
                         [self.main.id, self.extra.id, ALL_SHEETS])
        self.assertEqual(w.backlogs.count(), 4)
        self.assertIn("⚠ no content", w.backlogs.item(2).text())
        w.search.setText("#10")
        w.find_backlog()
        self.assertEqual(w.backlog_panel.backlog.title, "Empty one")
        self.assertFalse(w.backlog_panel.generate_button.isEnabled())  # nothing to send: no model call
        self.assertIn("Add notes", w.backlog_panel.notice.text())
        w.search.setText("backlog 12")
        w.find_backlog()
        self.assertTrue(w.search_message.isVisibleTo(w))
        self.assertIn("Backlogs on this sheet: 1, 7, 10, 7", w.search_message.text())
        w.search.setText("7")
        with mock.patch.object(QInputDialog, "getItem", side_effect=lambda *a, **k: (a[3][1], True)) as pick:
            w.find_backlog()
        pick.assert_called_once()
        self.assertEqual((w.backlog_panel.backlog.title, w.backlog_panel.backlog.occurrence), ("Orders again", 2))
        self.assertIn("unfinished", w.backlog_panel.notice.text())
        w.sheet_combo.setCurrentIndex(1)
        self.assertEqual(self.remembered[f"designer_sheet_{self.view.id}"], self.extra.id)
        w.search.setText("1")
        w.find_backlog()  # backlog 1 is on another sheet: not found on "Extra"
        self.assertIn("not found in sheet “Extra”", w.search_message.text())
        w.select_backlog(self.main.backlogs[0].id)  # choosing it anyway switches to all sheets
        self.assertEqual(w.sheet_combo.currentData(), ALL_SHEETS)
        self.assertEqual(w.backlog_panel.backlog.number, "1")

    def test_saved_changes_show_a_snackbar(self):
        w = self.workspace
        w.select_backlog(self.main.backlogs[2].id)
        panel = w.backlog_panel
        with mock.patch("wta.designer.backlog_panel.snackbar.show") as snack:
            with mock.patch.object(QInputDialog, "getMultiLineText", return_value=("It lists nothing yet.", True)):
                panel.add_note()
            self.assertEqual(snack.call_args.args[1], "Note added.")
            panel.notes.setCurrentRow(0)
            panel._remove(panel.notes)
            self.assertEqual(snack.call_args.args[1], "Note removed.")
            with mock.patch.object(QInputDialog, "getMultiLineText", return_value=("", False)):
                panel.add_note()  # cancelled: nothing saved, no snackbar
            self.assertEqual(snack.call_count, 2)

    def test_notes_context_and_pictures(self):
        w = self.workspace
        w.select_backlog(self.main.backlogs[2].id)  # the empty one
        panel = w.backlog_panel
        with mock.patch.object(QInputDialog, "getMultiLineText", return_value=("It lists nothing yet.", True)):
            panel.add_note()
        self.assertEqual(panel.notes.count(), 1)
        self.assertTrue(panel.generate_button.isEnabled())  # notes alone are enough
        self.assertIn("from your notes only", panel.notice.text())
        with mock.patch.object(QInputDialog, "getItem", side_effect=lambda *a, **k: (a[3][0], True)):
            panel.attach_picture()  # IMG-01 from backlog 1
            panel.add_from_backlog()
        self.assertEqual(panel.pictures.count(), 1)
        self.assertIn("attached by hand", panel.pictures.item(0).text())
        self.assertEqual(panel.context.count(), 1)
        self.assertIn("Backlog 1", panel.context.item(0).text())
        with mock.patch.object(QInputDialog, "getItem", side_effect=lambda *a, **k: (a[3][0], True)):
            panel.attach_picture()
        panel.pictures.setCurrentRow(1)
        panel.move_picture(-1)
        self.assertTrue(panel.pictures.item(0).text().startswith("IMG-02"))
        panel.detach_picture()
        self.assertEqual(panel.pictures.count(), 1)
        self.assertTrue(panel.pictures.item(0).text().startswith("IMG-01"))
        # Still attached to its own backlog too (many-to-many).
        self.assertEqual([i.label for i in wb.load_backlog(self.db, self.main.backlogs[0].id).images], ["IMG-01"])

    def test_generate_edit_and_answer(self):
        w = self.workspace
        w.select_backlog(self.orders.id)
        content.add_context(self.db, self.orders.id, content.DOMAIN_NOTE, "Managers only.")
        FakeRun.answer = CallResult(structured=response(case(1), case(2, "corner_case"), questions=["Which formats?"]),
                                    subtype="success", model="claude-opus-5-5")
        notified = []
        w.generation_finished.connect(lambda *args: notified.append(args))
        with mock.patch.object(workspace_module, "GenerationRun", FakeRun):
            w.backlog_panel.generate_button.click()
            self.wait(lambda: w.run is None)
        self.assertEqual(notified, [("Test cases ready", "Backlog 7: 2 test case(s), 1 open question(s).", False)])
        [run] = FakeRun.created
        self.assertEqual(run.request.domain_notes, ["Managers only."])
        self.assertEqual([i.image_id for i in run.request.images], ["IMG-02"])
        self.assertIs(w.tabs.currentWidget(), w.results_panel)
        self.assertEqual(w.tabs.tabText(1), "Test cases (2)")
        self.assertEqual(w.tabs.tabText(2), "Open questions (1)")
        model = w.results_panel.model
        self.assertEqual([c.data["id"] for c in model.cases], ["TC-007-01", "TC-007-02"])

        title = next(i for i, c in enumerate(model.columns) if c[0] == "title")
        steps = next(i for i, c in enumerate(model.columns) if c[0] == "steps")
        locked = next(i for i, c in enumerate(model.columns) if c[0] == "_locked")
        self.assertTrue(model.setData(model.index(0, title), "Verify that  the export\nworks"))
        self.assertTrue(model.setData(model.index(0, steps), "Open \"Orders\".\n\nAssert that the file is saved."))
        model.setData(model.index(1, locked), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
        self.wait(lambda: model.cases and model.cases[0].edited)
        first = model.cases[0]
        self.assertEqual(first.data["title"], "Verify that the export works")
        self.assertEqual(first.data["steps"], ["Open \"Orders\".", "Assert that the file is saved."])
        self.assertEqual(model.data(model.index(0, steps)), "1. Open \"Orders\".\n2. Assert that the file is saved.")
        self.assertTrue(model.cases[1].locked)
        id_column = next(i for i, c in enumerate(model.columns) if c[0] == "id")
        self.assertIn("✎", model.data(model.index(0, id_column)))

        results = w.results_panel
        results.table.selectRow(1)
        results.duplicate_button.click()
        self.assertEqual(len(model.cases), 3)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            results.delete_button.click()
        self.assertEqual(len(model.cases), 2)
        results.renumber_button.click()
        self.assertEqual([c.data["id"] for c in model.cases], ["TC-007-01", "TC-007-02"])

        questions = w.questions_panel
        questions.list.setCurrentRow(0)
        questions.answer.setPlainText("CSV only.")
        with mock.patch("wta.designer.questions_panel.snackbar.show") as snack:
            questions.answer_button.click()
        self.assertEqual(snack.call_args.args[1], "Answer added to the backlog's notes.")
        self.assertEqual(w.tabs.tabText(2), "Open questions")
        self.assertEqual(w.backlog_panel.notes.count(), 2)

    def test_generation_blocked_and_failures(self):
        w = self.workspace
        w.select_backlog(self.orders.id)
        w.generation_blocker = lambda: "Claude Code must be installed and signed in."
        with mock.patch.object(QMessageBox, "warning") as warn, \
                mock.patch.object(workspace_module, "GenerationRun", FakeRun):
            w.generate()
        warn.assert_called_once()
        self.assertEqual(FakeRun.created, [])
        w.generation_blocker = lambda: ""
        FakeRun.answer = CallResult(process_error="Claude Code could not be started.")
        notified = []
        w.generation_finished.connect(lambda *args: notified.append(args))
        with mock.patch.object(QMessageBox, "warning") as warn, \
                mock.patch.object(workspace_module, "GenerationRun", FakeRun):
            w.generate()
            self.wait(lambda: w.run is None)
        self.assertIn("could not be started", warn.call_args.args[2])
        self.assertEqual(notified[0][0], "Generation needs your attention")
        self.assertTrue(notified[0][2])
        self.assertEqual(generations.list_runs(self.db, self.orders.id)[0].status, "error")
        self.assertEqual(w.results_panel.model.cases, [])

    def test_per_step_preset_editing(self):
        settings = DesignerSettings()
        settings.output_presets.append(OutputPreset(name="P", steps_style=PER_STEP_EXPECTED))
        settings.output_preset = "P"
        designer_settings.save(self.db, settings)
        item = case(1, steps=[{"action": "Open it.", "expected": ""},
                              {"action": "Save.", "expected": "Assert that it is saved."}])
        request = generations.request_for_backlog(self.db, self.orders.id, settings)
        outcome = Planner(request).on_result("first", CallResult(structured=response(item), subtype="success"))
        self.assertTrue(outcome.ok, outcome.validation_errors)
        generations.save_outcome(self.db, self.orders.id, outcome)
        self.workspace.select_backlog(self.orders.id)
        model = self.workspace.results_panel.model
        steps = next(i for i, c in enumerate(model.columns) if c[0] == "steps")
        self.assertEqual(model.data(model.index(0, steps), Qt.ItemDataRole.EditRole),
                         "Open it. => \nSave. => Assert that it is saved.")
        model.setData(model.index(0, steps), "Open it. => Assert that it opens.\nSave. =>")
        self.wait(lambda: model.cases and model.cases[0].edited)
        self.assertEqual(model.cases[0].data["steps"], [{"action": "Open it.", "expected": "Assert that it opens."},
                                                        {"action": "Save.", "expected": ""}])


if __name__ == "__main__":
    unittest.main()
