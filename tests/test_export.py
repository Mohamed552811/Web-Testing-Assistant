import os
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import settings as app_settings  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Bug, Screenshot, Step, StepStatus  # noqa: E402
from wta.export import build_report, export_report  # noqa: E402
from wta.export.xlsx import png_size  # noqa: E402
from wta.prompt import RunContext, SmokeContext, build_prompt  # noqa: E402
from wta.results import StepResult, screenshot_rule  # noqa: E402
from wta.settings_dialog import SettingsDialog  # noqa: E402

app = QApplication.instance() or QApplication([])
PASSWORD = "Secret-Pw-123"


def make_png(path: Path, width=200, height=120, color="red") -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path), "PNG")
    return path


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.db = Database(cls.base / "wta.sqlite3")
        session = store.create_session(cls.db, store.SessionDetails(
            name="Shop | checkout", target_url="https://shop.example.com", test_account_email="qa@shop.example.com",
            test_account_password=PASSWORD))
        cls.session_id = session.id
        bug_png = make_png(cls.base / "bug.png")
        blocked_png = make_png(cls.base / "blocked.png", color="yellow")

        scenario = sc.add_scenario(cls.db, session.id, 1, sc.ScenarioInput("Checkout"))
        smoke = sc.add_smoke_page(cls.db, session.id, 1, sc.SmokePageInput("Cart page", "https://shop.example.com/cart"))
        with cls.db.Session.begin() as orm:
            steps = [
                Step(scenario_id=scenario.id, order=1, action="Open shop", status=StepStatus.PASS,
                     actual_result="ok | fine", reasoning="Loaded"),
                Step(scenario_id=scenario.id, order=2, action="Pay", status=StepStatus.FAIL, actual_result="500 error",
                     bugs=[Bug(title="Payment fails", severity="2 - High", priority=1,
                               repro_steps="1. Sign in as QA@shop.example.com\n2. Add item\n3. Pay",
                               expected="Order placed", actual="500 error",
                               report_text=f"Full report (typed {PASSWORD} by mistake)")],
                     screenshots=[Screenshot(file_path=str(bug_png))]),
                Step(scenario_id=scenario.id, order=3, action="Coupon", status=StepStatus.FAIL,
                     bugs=[Bug(title="False alarm", severity="4 - Low", priority=4)]),
                Step(scenario_id=scenario.id, order=4, action="Track order", status=StepStatus.BLOCKED,
                     screenshots=[Screenshot(file_path=str(blocked_png))]),
                Step(scenario_id=smoke.id, order=1, action="Empty cart message", status=StepStatus.FAIL,
                     bugs=[Bug(title="No empty-cart message", severity="3 - Medium", priority=2)]),
            ]
            orm.add_all(steps)
        ids = [s.id for s in steps]
        sc.approve_step(cls.db, ids[0], "fine")
        sc.approve_step(cls.db, ids[1], "real bug")
        sc.override_step(cls.db, ids[2], StepStatus.PASS, "coupon works, Claude misread")
        cls.report = build_report(cls.db, session.id)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    # model

    def test_report_model(self):
        r = self.report
        self.assertEqual([(row.kind, row.order) for row in r.rows],
                         [("Scenario", 1), ("Scenario", 2), ("Scenario", 3), ("Scenario", 4), ("Smoke test", 1)])
        self.assertEqual(r.rows[2].claude_status, "Fail")
        self.assertEqual(r.rows[2].final_status, "Pass")  # the override wins, Claude's finding is kept
        self.assertEqual(r.rows[4].claude_status, "Bug")  # smoke label
        self.assertEqual(r.rows[3].final_status, "Pending review")
        self.assertEqual(r.counts["Scenario"]["Pass"], 2)
        self.assertEqual(r.counts["Scenario"]["Fail / Bug"], 1)
        self.assertEqual(r.counts["Scenario"]["Pending review"], 1)
        # Overridden-to-Pass bug left out; unreviewed smoke bug kept.
        self.assertEqual([(b.title, b.review_state) for b in r.bugs],
                         [("Payment fails", "Confirmed"), ("No empty-cart message", "Not reviewed yet")])
        self.assertIn("Expected: Order placed", r.bugs[0].tfs_repro_steps)
        # Test-account details never leave the machine in a report (case-insensitive).
        self.assertIn("1. Sign in as the test account", r.bugs[0].repro_steps)
        self.assertEqual(r.bugs[0].report_text, "Full report (typed the test password by mistake)")
        self.assertEqual(len(r.bugs[0].screenshots), 1)

    def no_secrets(self, data: bytes, what: str):
        self.assertNotIn(PASSWORD.encode(), data, what)
        self.assertNotIn(b"qa@shop.example.com", data, what)

    # formats

    def test_xlsx(self):
        from openpyxl import load_workbook
        path = export_report(self.db, self.session_id, self.base / "out" / "report.xlsx")
        wb = load_workbook(path)
        self.assertEqual(wb.sheetnames, ["Summary", "Results", "Bugs"])
        bugs = wb["Bugs"]
        headers = [c.value for c in bugs[1]]
        for name in ("Work Item Type", "Title", "Repro Steps", "Severity", "Priority", "Tags"):
            self.assertIn(name, headers)
        self.assertEqual(bugs.max_row, 3)
        self.assertEqual(bugs["A2"].value, "Bug")
        self.assertEqual(bugs["D2"].value, "2 - High")
        self.assertEqual(wb["Results"].max_row, 6)
        with zipfile.ZipFile(path) as z:
            media = [n for n in z.namelist() if n.startswith("xl/media/")]
            self.assertEqual(len(media), 3)  # bug + blocked in Results, bug again in Bugs
            self.assertEqual(z.read(media[0])[:8], b"\x89PNG\r\n\x1a\n")
            for name in z.namelist():
                self.no_secrets(z.read(name), name)

    def test_docx(self):
        from docx import Document
        path = export_report(self.db, self.session_id, self.base / "out" / "report", "docx")
        self.assertEqual(path.suffix, ".docx")
        doc = Document(str(path))
        self.assertEqual(len(doc.inline_shapes), 2)  # bug screenshot + blocked screenshot
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("1. Payment fails", text)
        self.assertIn("Other screenshots", text)
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                self.no_secrets(z.read(name), name)

    def test_pdf(self):
        from PySide6.QtPdf import QPdfDocument
        path = export_report(self.db, self.session_id, self.base / "out" / "report.pdf")
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 5000)
        document = QPdfDocument()
        self.assertEqual(document.load(str(path)), QPdfDocument.Error.None_)
        self.assertGreaterEqual(document.pageCount(), 1)
        text = "".join(document.getAllText(i).text() for i in range(document.pageCount()))
        self.assertIn("Payment fails", text)
        self.assertNotIn(PASSWORD, text)
        document.close()

    def test_markdown(self):
        path = export_report(self.db, self.session_id, self.base / "out" / "report.md")
        text = path.read_text("utf-8")
        self.assertTrue(text.startswith("# Test report — Shop | checkout\n"))  # headings need no escaping
        self.assertIn("| ok \\| fine |", text)  # a pipe inside a table cell is escaped
        self.assertIn("### 1. Payment fails", text)
        links = [line.split("](")[1].rstrip(")") for line in text.splitlines() if line.startswith("![")]
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertTrue((path.parent / link).is_file(), link)  # copied next to the report
        self.no_secrets(text.encode(), "markdown")

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            export_report(self.db, self.session_id, self.base / "x.txt")

    def test_png_size(self):
        self.assertEqual(png_size(make_png(self.base / "size.png", 321, 123)), (321, 123))
        (self.base / "not.png").write_bytes(b"GIF89a....................")
        self.assertIsNone(png_size(self.base / "not.png"))


class ScreenshotTimingTests(unittest.TestCase):
    def step(self, order, status):
        return StepResult(order, "", "", "a", "", "", status, "", "", [], [], [], None, None)

    def test_rule(self):
        default = app_settings.SCREENSHOTS_BUG_BLOCKED
        every = app_settings.SCREENSHOTS_EVERY_STEP
        once = app_settings.SCREENSHOTS_ONCE_PER_PAGE
        self.assertEqual(screenshot_rule(default, False, self.step(1, StepStatus.PASS)), (False, False))
        self.assertEqual(screenshot_rule(default, False, self.step(2, StepStatus.FAIL)), (True, True))
        self.assertEqual(screenshot_rule(every, False, self.step(2, StepStatus.PASS)), (True, True))
        self.assertEqual(screenshot_rule(once, True, self.step(1, StepStatus.PASS)), (True, True))
        self.assertEqual(screenshot_rule(once, True, self.step(2, StepStatus.PASS)), (False, False))
        self.assertEqual(screenshot_rule(once, False, self.step(1, StepStatus.PASS)), (False, False))

    def test_prompt_follows_timing(self):
        ctx = RunContext("S", "https://a.example.com", ["a.example.com"], "", "", "N", "G", "")
        self.assertIn('Screenshots ONLY for "fail" and "blocked"', build_prompt(ctx))
        ctx.screenshot_timing = app_settings.SCREENSHOTS_EVERY_STEP
        self.assertIn("Screenshots for EVERY step", build_prompt(ctx))
        ctx.screenshot_timing = app_settings.SCREENSHOTS_ONCE_PER_PAGE
        self.assertIn('Screenshots ONLY for "fail"', build_prompt(ctx))  # scenarios: like the default
        ctx.smoke = SmokeContext("P", "https://a.example.com/p")
        self.assertIn("Screenshots for step 1 (one view of the page)", build_prompt(ctx))


class SettingsTests(unittest.TestCase):
    def test_load_save_and_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = app_settings.load(path)
            self.assertEqual((s.screenshot_timing, s.export_format, s.confirm_risky_actions),
                             ("bug_blocked", "xlsx", False))
            app_settings.save(path, app_settings.Settings("every_step", "pdf", True))
            self.assertEqual(app_settings.load(path), app_settings.Settings("every_step", "pdf", True))
            path.write_text('{"screenshot_timing": "sometimes", "export_format": "odt"}', "utf-8")
            self.assertEqual(app_settings.load(path), app_settings.Settings())

    def test_dialog_round_trip(self):
        dialog = SettingsDialog(app_settings.Settings("once_per_smoke_page", "docx", True))
        self.assertEqual(dialog.settings(), app_settings.Settings("once_per_smoke_page", "docx", True))
        dialog.export_format.setCurrentIndex(dialog.export_format.findData("md"))
        self.assertEqual(dialog.settings().export_format, "md")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
