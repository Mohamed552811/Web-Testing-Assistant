"""Phase 17: test data variables, filled in fresh for each run."""

import os
import re
import unittest
from datetime import datetime
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wta import test_data  # noqa: E402
from wta.scenario_dialogs import ScenarioDialog  # noqa: E402

app = QApplication.instance() or QApplication([])


class FillerTests(unittest.TestCase):
    def test_same_value_within_a_run_and_fresh_for_the_next(self):
        filler = test_data.Filler(datetime(2026, 10, 18, 9, 5))
        steps = filler.fill("Sign up with {{random_email}} on {{ today }}")
        expected = filler.fill("The welcome mail goes to {{random_email}}")
        email = filler.values["random_email"]
        self.assertRegex(email, r"^wta\.[a-z]{6}\d{3}@example\.com$")
        self.assertEqual(steps, f"Sign up with {email} on 2026-10-18")
        self.assertEqual(expected, f"The welcome mail goes to {email}")
        self.assertNotEqual(test_data.Filler().fill("{{uuid}}"), test_data.Filler().fill("{{uuid}}"))

    def test_all_variables(self):
        filler = test_data.Filler(datetime(2026, 12, 31, 23, 59, 58))
        text = filler.fill(" ".join("{{" + name + "}}" for name in test_data.GENERATORS))
        self.assertNotIn("{{", text)
        self.assertEqual(filler.values["tomorrow"], "2027-01-01")
        self.assertEqual(filler.values["yesterday"], "2026-12-30")
        self.assertEqual(filler.values["now"], "2026-12-31 23:59")
        self.assertEqual(filler.values["timestamp"], "20261231235958")
        self.assertTrue(re.fullmatch(r"\d{6}", filler.values["random_number"]))

    def test_unknown_names_stay_as_typed(self):
        filler = test_data.Filler()
        self.assertEqual(filler.fill("Use {{coupon_code}}"), "Use {{coupon_code}}")
        self.assertEqual(filler.unknown, {"coupon_code"})
        self.assertEqual(test_data.used_variables("a {{today}}", None, "{{x}}"), {"today", "x"})

    def test_dialog_refuses_unknown_variables(self):
        dialog = ScenarioDialog()
        dialog.name.setText("Sign up")
        dialog.steps.setPlainText("Sign up with {{random_mail}}")
        with mock.patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        self.assertIn("{{random_mail}}", warning.call_args.args[2])
        self.assertIsNone(dialog.data())
        dialog.steps.setPlainText("Sign up with {{random_email}}")
        dialog.accept()
        self.assertEqual(dialog.data().steps, "1. Sign up with {{random_email}}")  # filled at run time, not now
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
