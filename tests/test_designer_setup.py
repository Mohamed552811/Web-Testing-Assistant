"""The Test Case Design setup step: upload, analysis summary, mapping override (spec 3.7 steps 6-7)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from wta import launchpad as modes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database  # noqa: E402
from wta.db import workbooks as wb  # noqa: E402
from wta.designer.layout import BLOCK, TABLE, Mapping  # noqa: E402
from wta.designer.mapping_dialog import MappingDialog  # noqa: E402

try:
    from designer_xlsx import Pic, SheetSpec, build, png
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import Pic, SheetSpec, build, png
    from tests.qt_app import app  # noqa: E402

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def sheets(title: str = "Login") -> list[SheetSpec]:
    return [SheetSpec("Main", {"A1": 1, "B1": title, "B2": "Sign in.", "A4": 2, "B4": "Logout", "B5": "Out."},
                      [Pic("B3", png(seed=1))]),
            SheetSpec("Single", {"A1": "wt", "C1": 7, "D1": "Only one", "D2": "Text?"})]


class DesignerSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = Database(self.dir / "wta.sqlite3")
        self.window = MainWindow(self.db, app_dir=self.dir, check_sign_in=False)
        self.window.set_mode(modes.DESIGN)

    def tearDown(self):
        self.window.deleteLater()
        app.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def write(self, name: str, title: str = "Login") -> Path:
        path = self.dir / name
        path.write_bytes(build(sheets(title)))
        return path

    def test_upload_shows_the_summary(self):
        with mock.patch.object(QMessageBox, "question", return_value=NO) as ask:
            self.window.upload_workbook(self.write("Backlogs.xlsx"))
        ask.assert_called_once()  # "Single" has one backlog only: low confidence, check the mapping?
        self.assertIn("Single", ask.call_args.args[2])
        setup = self.window.designer_setup
        self.assertEqual(setup.workbooks.count(), 1)
        self.assertIn("Backlogs.xlsx", setup.workbooks.item(0).text())
        self.assertIn("3 backlog(s)", setup.workbooks.item(0).text())
        text = setup.summary.toPlainText()
        for expected in ("Backlogs.xlsx", "Sheet “Main”", "block layout", "Login", "IMG-01", "unfinished text"):
            self.assertIn(expected, text)
        self.assertTrue(setup.enter_button.isEnabled() and setup.mapping_button.isEnabled())
        setup.enter_button.click()
        self.assertIs(self.window.pages.currentWidget(), self.window.designer_workspace)
        self.assertIn("Backlogs.xlsx (v1)", self.window.designer_workspace.title.text())

    def test_bad_file_and_repeated_upload(self):
        bad = self.dir / "bad.xlsx"
        bad.write_bytes(b"nope")
        with mock.patch.object(QMessageBox, "warning") as warn:
            self.window.upload_workbook(bad)
        warn.assert_called_once()
        with mock.patch.object(QMessageBox, "question", return_value=NO) as ask:
            self.window.upload_workbook(self.write("A.xlsx"))
            self.window.upload_workbook(self.write("A copy.xlsx"))
        self.assertEqual(ask.call_count, 1)  # the second upload is the same file: no new questions
        self.assertEqual(self.window.designer_setup.workbooks.count(), 1)

    def test_new_version_reuses_a_hand_set_mapping_and_lists_changes(self):
        with mock.patch.object(QMessageBox, "question", return_value=NO):
            self.window.upload_workbook(self.write("Backlogs v1.xlsx"))
        first = wb.list_workbooks(self.db)[0]
        single = wb.load_workbook(self.db, first.id).sheets[1]
        wb.apply_mapping(self.db, single.id, Mapping(BLOCK, number_col=3, title_col=4, status_col=1))
        with mock.patch.object(QMessageBox, "question", return_value=YES) as ask:
            self.window.upload_workbook(self.write("Backlogs v2.xlsx", title="Sign-in"))
        self.assertIn("Reuse the mapping", ask.call_args_list[0].args[2])
        second = wb.load_workbook(self.db, wb.list_workbooks(self.db)[0].id)
        self.assertEqual(second.version_label, "v2")
        self.assertTrue(second.sheets[1].manual)
        self.assertIn("Changed: Main #1", self.window.designer_setup.summary.toPlainText())

    def test_edit_mapping(self):
        with mock.patch.object(QMessageBox, "question", return_value=NO):
            self.window.upload_workbook(self.write("B.xlsx"))
        workbook_id = self.window.designer_setup.selected_id()
        main = wb.load_workbook(self.db, workbook_id).sheets[0]

        def set_rows(dialog):
            dialog.last_spin.setValue(3)
            return QDialog.DialogCode.Accepted

        with mock.patch.object(MappingDialog, "exec", set_rows):  # a plain function: called as a method
            self.window.edit_workbook_mapping(workbook_id, main.id)
        changed = wb.load_workbook(self.db, workbook_id).sheets[0]
        self.assertTrue(changed.manual)
        self.assertEqual([b.number for b in changed.backlogs], ["1"])
        self.assertIn("set by hand", self.window.designer_setup.summary.toPlainText())

    def test_mapping_dialog_form(self):
        with mock.patch.object(QMessageBox, "question", return_value=NO):
            self.window.upload_workbook(self.write("B.xlsx"))
        view = wb.load_workbook(self.db, self.window.designer_setup.selected_id())
        dialog = MappingDialog(view.sheets, view.sheets[1].id)
        self.assertEqual(dialog.number_edit.text(), "C")
        self.assertEqual(dialog.status_edit.text(), "A")
        dialog.description_edit.setText("d; F")
        self.assertEqual(dialog.mapping(), Mapping(BLOCK, 3, None, [4, 6], 1, None, None, None))
        dialog.number_edit.setText("3")
        with self.assertRaisesRegex(ValueError, "letters"):
            dialog.mapping()
        dialog.number_edit.setText("C")
        dialog.layout_combo.setCurrentIndex(1)
        with self.assertRaisesRegex(ValueError, "header row"):
            dialog.mapping()
        dialog.header_spin.setValue(1)
        self.assertEqual(dialog.mapping().layout, TABLE)
        dialog._accept_reset()
        self.assertIsNone(dialog.result_mapping())
        dialog.deleteLater()

    def test_last_workbook_is_selected_again(self):
        with mock.patch.object(QMessageBox, "question", return_value=NO):
            self.window.upload_workbook(self.write("One.xlsx"))
            self.window.upload_workbook(self.write("Two.xlsx", title="Other"))
        setup = self.window.designer_setup
        setup.workbooks.setCurrentRow(1)  # the older one
        chosen = setup.selected_id()
        other = MainWindow(self.db, app_dir=self.dir, check_sign_in=False)
        try:
            other.continue_last_mode()
            self.assertIs(other.pages.currentWidget(), other.designer_setup)
            self.assertEqual(other.designer_setup.selected_id(), chosen)
        finally:
            other.deleteLater()


if __name__ == "__main__":
    unittest.main()
