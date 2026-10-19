"""The Test Case Design settings dialog: rules, tags, output presets, model, product context."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QDialog, QInputDialog  # noqa: E402

from wta import launchpad as modes  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, designer_settings  # noqa: E402
from wta.designer.settings import PER_STEP_EXPECTED, PINNED, DesignerSettings  # noqa: E402
from wta.designer.settings_dialog import DesignerSettingsDialog  # noqa: E402

try:
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = DesignerSettingsDialog(DesignerSettings())

    def tearDown(self):
        self.dialog.deleteLater()
        app.processEvents()

    def test_defaults_and_builtin_preset(self):
        d = self.dialog
        self.assertTrue(d.merge_check.isChecked())
        self.assertEqual(d.merge_steps_spin.value(), 15)
        self.assertEqual(d.vocabulary_edit.text(), "smoke, regression")
        self.assertEqual(d.language_label.text(), "English")
        self.assertTrue(d.latest_radio.isChecked())
        self.assertFalse(d.preset_fields.isEnabled())  # built-in Default is read-only
        self.assertFalse(d.delete_preset_button.isEnabled())
        self.assertIn("TC-003-02", d.id_example.text())
        self.assertEqual(d.settings().to_json(), DesignerSettings().to_json())

    def test_new_preset_is_editable_and_kept(self):
        d = self.dialog
        with mock.patch.object(QInputDialog, "getText", return_value=("Jira import", True)):
            d.save_preset_button.click()
        self.assertEqual(d.preset_combo.currentData(), "Jira import")
        self.assertTrue(d.preset_fields.isEnabled())
        d.prefix_edit.setText("PD")
        d.backlog_pad_spin.setValue(0)
        d.seq_pad_spin.setValue(0)
        d.priority_combo.setCurrentIndex(1)
        d.steps_combo.setCurrentIndex(d.steps_combo.findData(PER_STEP_EXPECTED))
        d.field_checks["basis"].setChecked(False)
        self.assertIn("PD-3-2", d.id_example.text())
        d.preset_combo.setCurrentIndex(0)  # switch away and back: the edits stay
        d.preset_combo.setCurrentIndex(1)
        self.assertEqual(d.prefix_edit.text(), "PD")
        settings = d.settings()
        preset = settings.preset()
        self.assertEqual((settings.output_preset, preset.id_prefix, preset.backlog_pad), ("Jira import", "PD", 0))
        self.assertEqual((preset.priority_labels, preset.steps_style), (("P1", "P2", "P3"), PER_STEP_EXPECTED))
        self.assertFalse(preset.includes("basis"))
        self.assertEqual(settings.output_presets[0].id_prefix, "TC")  # Default untouched
        d.delete_preset_button.click()
        self.assertEqual([p.name for p in d.settings().output_presets], ["Default"])

    def test_rules_model_and_validation(self):
        d = self.dialog
        d.merge_check.setChecked(False)
        self.assertFalse(d.merge_steps_spin.isEnabled())
        d.heavy_check.setChecked(False)
        d.ui_combo.setCurrentIndex(d.ui_combo.findData("include_ui"))
        d.vocabulary_edit.setText("smoke, Regression, permissions")
        d.pinned_radio.setChecked(True)
        d.pinned_edit.setText("claude-opus-5-5")
        d.context_edit.setPlainText("a CRM app")
        s = d.settings()
        self.assertEqual((s.merge_same_flow, s.avoid_heavy_and_performance, s.ui_testing_policy),
                         (False, False, "include_ui"))
        self.assertEqual(s.tag_vocabulary, ["smoke", "regression", "permissions"])
        self.assertEqual((s.model_mode, s.cli_model(), s.product_context), (PINNED, "claude-opus-5-5", "a CRM app"))
        d.vocabulary_edit.setText("permissions")
        d._accept()
        self.assertTrue(d.error.isVisibleTo(d))
        self.assertIn("smoke", d.error.text())
        self.assertNotEqual(d.result(), QDialog.DialogCode.Accepted)


class SettingsFromWindowTests(unittest.TestCase):
    def test_saved_from_the_setup_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "wta.sqlite3")
            window = MainWindow(db, app_dir=Path(tmp), check_sign_in=False)
            try:
                window.set_mode(modes.DESIGN)

                def change(dialog):
                    dialog.merge_steps_spin.setValue(10)
                    return QDialog.DialogCode.Accepted

                with mock.patch.object(DesignerSettingsDialog, "exec", change):
                    window.designer_setup.settings_button.click()
                self.assertEqual(designer_settings.load(db).merge_max_steps, 10)
            finally:
                window.deleteLater()
                app.processEvents()
                db.close()


if __name__ == "__main__":
    unittest.main()
