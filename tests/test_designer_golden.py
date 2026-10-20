"""Golden fixtures of spec 6 (backlogs 1, 9 and 3 of the reference workbook).

Offline (when the git-ignored spec and workbook are present):
- the spec's reference outputs pass our validator with the default settings;
- the request built from the uploaded workbook matches the fixture inputs (cells, rows, pictures).

Live (set WTA_LIVE_CLAUDE=1; uses your Claude subscription): generate each fixture with Claude Code and
check structure and rules only (spec 6): schema and cross-field rules, required coverage, no forbidden topics.
Answers are saved to tests/fixtures/designer/live/ for reading.
"""

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wta.db import Database  # noqa: E402
from wta.db import generations  # noqa: E402
from wta.db import workbooks as wb  # noqa: E402
from wta.designer import validation  # noqa: E402
from wta.designer.generation import GenerationRun, Planner  # noqa: E402
from wta.designer.settings import DesignerSettings  # noqa: E402

try:
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "test-case-designer" / "TEST_CASE_DESIGNER_SPEC.md"
WORKBOOK = ROOT / "docs" / "test-case-designer" / "Print_designer_6.xlsx"
LIVE_DIR = Path(__file__).resolve().parent / "fixtures" / "designer" / "live"
LIVE = os.environ.get("WTA_LIVE_CLAUDE") == "1"
LIVE_TIMEOUT_S = 20 * 60

# Required coverage per fixture: each entry is a set of alternatives; some test case must mention one of them.
# Owner decision (M3): the prompt stays verbatim spec 7.1, which sends undefined behavior to open_questions, so
# permission coverage is also met by an open question about permissions or roles.
COVERAGE = {
    1: [("permission", "access", "role")],
    9: [("download pdf",), ("print format",), ("permission", "role"), ("customize",)],
    3: [("mandatory", "required"), ("discard",), ("discard and continue", "unsaved")],
}
QUESTION_MAY_COVER = {("permission", "access", "role"), ("permission", "role")}
# Never wanted with the default settings (heavy tests, visual-only checks).
FORBIDDEN = [r"\bperformance\b", r"\bload test", r"response time", r"\bstress test", r"\bpixel", r"\bcolou?r\b",
             r"thousands of records", r"\bbulk (import|export)"]


def fixtures() -> dict[int, tuple[dict, dict]]:
    text = SPEC.read_text("utf-8")
    section = text[text.index("## 6. Golden Examples"):text.index("## 7. Prompt Template")]
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", section, re.S)]
    return {blocks[i]["backlog_number"]: (blocks[i], blocks[i + 1]) for i in range(0, len(blocks), 2)}


def coverage_problems(number: int, response: dict) -> list[str]:
    """Structure-and-rules checks of spec 6 that wording may not decide: coverage and forbidden topics."""
    cases = response["test_cases"]
    texts = [case_text(c) for c in cases]
    questions = " ".join(response["open_questions"]).lower()
    problems = []
    for alternatives in COVERAGE[number]:
        covered = any(any(a in t for a in alternatives) for t in texts)
        if not covered and alternatives in QUESTION_MAY_COVER:
            covered = any(a in questions for a in alternatives)
        if not covered:
            problems.append(f"no test case covers {alternatives}")
    for pattern in FORBIDDEN:
        hits = [c["id"] for c, t in zip(cases, texts) if re.search(pattern, t)]
        if hits:
            problems.append(f"forbidden topic {pattern!r} in {hits}")
    if number == 9 and any(re.search(r"\b(delivery|invoice) command", t) for t in texts):
        problems.append("test cases for other commands visible in the screenshot (rule 7)")
    return problems


def case_text(case: dict) -> str:
    parts = [case["title"], case["expected_result"], *case.get("preconditions", [])]
    parts += [s if isinstance(s, str) else f"{s['action']} {s['expected']}" for s in case["steps"]]
    return " ".join(parts).lower()


@unittest.skipUnless(SPEC.is_file() and WORKBOOK.is_file(), "spec or reference workbook not present")
class GoldenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = fixtures()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls.tmp.name) / "wta.sqlite3")
        view = wb.load_workbook(cls.db, wb.import_workbook(cls.db, WORKBOOK).workbook_id)
        cls.sheets = {s.name: s for s in view.sheets}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def request_for(self, number: int, settings: DesignerSettings | None = None):
        given, _expected = self.fixtures[number]
        sheet = self.sheets[given["source"]["sheet"]]
        backlog = next(b for b in sheet.backlogs if b.number == str(number))
        return backlog, generations.request_for_backlog(self.db, backlog.id, settings or DesignerSettings(),
                                                        given["domain_notes"])

    def test_three_fixtures_present(self):
        self.assertEqual(sorted(self.fixtures), [1, 3, 9])

    def test_reference_outputs_pass_validation(self):
        for number, (_given, expected) in self.fixtures.items():
            with self.subTest(backlog=number):
                result = validation.validate(expected, DesignerSettings(), str(number))
                self.assertEqual(result.errors, [])
                self.assertEqual(coverage_problems(number, expected), [])  # the checks accept the spec's own answers

    def test_request_matches_the_fixture_inputs(self):
        for number, (given, _expected) in self.fixtures.items():
            with self.subTest(backlog=number):
                _backlog, request = self.request_for(number)
                row = Planner(request).row_data
                for key in ("backlog_number", "status_code", "title", "source", "description_blocks", "domain_notes"):
                    self.assertEqual(row[key], given[key], key)
                self.assertEqual([(i["image_id"], i["anchor_cell"]) for i in row["images"]],
                                 [(i["image_id"], i["anchor_cell"]) for i in given["images"]])
                self.assertTrue(all(image.data and image.mime == "image/png" for image in request.images))

    @unittest.skipUnless(LIVE, "set WTA_LIVE_CLAUDE=1 to generate with Claude Code (uses your subscription)")
    def test_live_generation(self):
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        for number in (1, 9, 3):
            with self.subTest(backlog=number):
                backlog, request = self.request_for(number)
                outcomes = []
                run = GenerationRun(request, Path(self.tmp.name) / "runs")
                run.finished.connect(outcomes.append)
                run.start()
                deadline = time.monotonic() + LIVE_TIMEOUT_S
                while not outcomes and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.05)
                self.assertTrue(outcomes, "no answer within the time limit")
                outcome = outcomes[0]
                (LIVE_DIR / f"backlog-{number}.json").write_text(json.dumps(
                    {"status": outcome.status, "message": outcome.message, "model": outcome.model_id,
                     "attempts": outcome.attempts, "validation_errors": outcome.validation_errors,
                     "response": outcome.response}, indent=2, ensure_ascii=False), encoding="utf-8")
                generations.save_outcome(self.db, backlog.id, outcome)
                self.assertTrue(outcome.ok, f"{outcome.message} {outcome.validation_errors}")
                self.assertTrue(outcome.model_id.startswith("claude-opus"), outcome.model_id)
                self.assertEqual(coverage_problems(number, outcome.response), [])

    @unittest.skipUnless(LIVE_DIR.is_dir(), "no saved live answers")
    def test_saved_live_answers(self):
        """The answers of the last live run (git-ignored) still pass the checks."""
        for path in sorted(LIVE_DIR.glob("backlog-*.json")):
            number = int(path.stem.split("-")[1])
            with self.subTest(backlog=number):
                saved = json.loads(path.read_text("utf-8"))
                self.assertEqual(saved["status"], "ok", saved["message"])
                self.assertEqual(validation.validate(saved["response"], DesignerSettings(), str(number)).errors, [])
                self.assertEqual(coverage_problems(number, saved["response"]), [])


if __name__ == "__main__":
    unittest.main()
