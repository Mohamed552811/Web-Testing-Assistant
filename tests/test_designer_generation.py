"""Generation (spec 5.3, 7.3): validation, retry, continuation, the claude -p call and stored runs. No live calls."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402

from wta import claude_launcher  # noqa: E402
from wta.db import Database  # noqa: E402
from wta.db import generations  # noqa: E402
from wta.db import workbooks as wb  # noqa: E402
from wta.designer import validation  # noqa: E402
from wta.designer.generation import (  # noqa: E402
    CallResult,
    CallSpec,
    ClaudeCall,
    GenerationRequest,
    GenerationRun,
    Planner,
    preflight,
)
from wta.designer.prompt_builder import PromptImage  # noqa: E402
from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings, OutputPreset  # noqa: E402

try:
    from designer_xlsx import Pic, SheetSpec, build, png
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import Pic, SheetSpec, build, png
    from tests.qt_app import app  # noqa: E402


def case(seq: int, kind: str = "happy_path", priority: str = "High", tags=None, number=7, **extra) -> dict:
    data = {
        "id": f"TC-{number:03d}-{seq:02d}" if isinstance(number, int) else f"TC-005-{seq:02d}",
        "backlog_number": number,
        "title": f"Verify that flow number {seq} works",
        "type": kind,
        "priority": priority,
        "tags": tags if tags is not None else (["smoke", "regression"] if kind == "happy_path" and priority == "High"
                                               else ["regression"]),
        "preconditions": ["User is logged in"],
        "steps": ["Open the \"Orders\" page.", "Assert that the list is shown."],
        "expected_result": "The flow works.",
        "basis": "specified",
        "source_reference": "D4 + IMG-01",
    }
    data.update(extra)
    return data


def response(*cases, number=7, status="ok", questions=()) -> dict:
    return {"backlog_number": number, "backlog_title": "Orders", "status": status,
            "test_cases": list(cases), "open_questions": list(questions)}


def request(**changes) -> GenerationRequest:
    values = dict(backlog_key="7", title="Orders", sheet="Main", first_row=3, last_row=9,
                  text_blocks=[("D4", "The user exports orders.")],
                  images=[PromptImage("IMG-01", "D5", "image/png", png(seed=1))], settings=DesignerSettings())
    values.update(changes)
    return GenerationRequest(**values)


class ValidationTests(unittest.TestCase):
    def check(self, data, key="7", settings=None):
        return validation.validate(data, settings or DesignerSettings(), key)

    def test_valid_response_and_enum_capitalization(self):
        data = response(case(1), case(2, "corner_case"))
        data["test_cases"][0]["type"] = "HAPPY_PATH"
        data["test_cases"][0]["priority"] = "high"
        data["test_cases"][1]["basis"] = "Specified"
        data["test_cases"][1]["tags"] = ["Regression"]
        result = self.check(data)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.response["test_cases"][0]["type"], "happy_path")
        self.assertEqual(result.response["test_cases"][1]["tags"], ["regression"])

    def test_schema_errors(self):
        bad = case(1, title="Check that it works")
        bad["extra"] = 1
        errors = self.check(response(bad)).errors
        self.assertTrue(any("does not match" in e for e in errors), errors)
        self.assertTrue(any("Additional properties" in e for e in errors), errors)
        self.assertTrue(self.check([1, 2]).errors)

    def test_cross_field_rules(self):
        cases = [
            case(1, "corner_case"),
            case(3, "happy_path", backlog_number=8),  # wrong number, wrong sequence, after a corner case
            case(3, "happy_path", priority="Medium", tags=["smoke"]),  # no regression tag
            case(4, "corner_case", tags=["regression", "nightly"],
                 steps=["Open it.", "Check it."], title="Verify that flow number 1 works"),
            case(5, "happy_path", tags=["regression"]),  # High happy path without smoke
        ]
        cases[1]["title"] = "Verify that flow number 2 works"
        cases[2]["title"] = "Verify that flow number 3 works"
        text = "\n".join(self.check(response(*cases)).errors)
        for expected in ("backlog_number must be 7", "the id must be TC-007-02", "must all come before",
                         "needs the \"regression\" tag", "not in the vocabulary", "must start with \"Assert that\"",
                         "used twice", "needs the \"smoke\" tag"):
            self.assertIn(expected, text)

    def test_status_and_happy_path_rules(self):
        self.assertIn("test_cases must be empty",
                      " ".join(self.check(response(case(1), status="insufficient_information")).errors))
        self.assertEqual(self.check(response(status="backlog_not_found", questions=["Why?"])).errors, [])
        self.assertIn("At least one happy_path", " ".join(self.check(response(case(1, "corner_case"))).errors))
        self.assertIn("but backlog 7 was requested", " ".join(self.check(response(case(1, number=8), number=8)).errors))

    def test_alphanumeric_key(self):
        data = response(case(1, number="BL-5"), number="BL-5")
        self.assertEqual(self.check(data, key="BL-5").errors, [])
        self.assertTrue(self.check(response(case(1)), key="BL-5").errors)  # integer where a string is expected

    def test_preset_rules(self):
        settings = DesignerSettings(tag_suggestions=False)
        preset = OutputPreset(name="P", id_prefix="PD", backlog_pad=0, seq_pad=0, steps_style=PER_STEP_EXPECTED,
                              optional_fields={"basis": False})
        settings.output_presets.append(preset)
        settings.output_preset = "P"
        item = case(1, tags=[], id="PD-7-1",
                    steps=[{"action": "Open the page.", "expected": ""}, {"action": "Click Save.", "expected": ""}])
        del item["basis"]
        errors = self.check(response(item), settings=settings).errors
        self.assertEqual(errors, ["test_cases[0] (PD-7-1): at least one step needs an \"expected\" check."])
        item["steps"][1]["expected"] = "Assert that it is saved."
        item["tags"] = ["regression"]
        self.assertIn("tags must be empty", " ".join(self.check(response(item), settings=settings).errors))

    def test_feedback_and_salvage(self):
        text = validation.feedback([f"problem {i}" for i in range(30)])
        self.assertTrue(text.startswith("Your previous answer failed validation."))
        self.assertIn("- problem 24", text)
        self.assertIn("and 5 more", text)
        full = json.dumps(response(case(1), case(2, "corner_case"), case(3, "corner_case")))
        cut = full[:full.index('"TC-007-03"') + 20]
        self.assertEqual([c["id"] for c in validation.salvage_test_cases(cut)], ["TC-007-01", "TC-007-02"])
        self.assertEqual(validation.salvage_test_cases("no json"), [])


class PlannerTests(unittest.TestCase):
    def text_of(self, spec: CallSpec) -> str:
        return spec.content[-1]["text"]

    def test_first_call(self):
        planner = Planner(request())
        spec = planner.first_call()
        self.assertEqual((spec.kind, spec.model), ("first", "opus"))
        self.assertEqual([b["type"] for b in spec.content], ["text", "image", "text"])
        self.assertIn("Backlog number: 7", self.text_of(spec))
        self.assertIn("ACTIVE SETTINGS", spec.system_prompt)
        self.assertEqual(json.loads(spec.json_schema)["properties"]["backlog_number"], {"type": "integer"})

    def test_valid_answer(self):
        planner = Planner(request())
        planner.first_call()
        outcome = planner.on_result("first", CallResult(structured=response(case(1)), model="claude-opus-5-5",
                                                        subtype="success", stop_reason="tool_use"))
        self.assertTrue(outcome.ok)
        self.assertEqual((outcome.model_id, outcome.prompt_version, outcome.status), ("claude-opus-5-5", "v1", "ok"))
        self.assertIn("latest Opus", outcome.model_resolution)
        self.assertEqual(outcome.settings_snapshot["selected_preset"]["name"], "Default")
        self.assertEqual(len(outcome.attempts), 1)

    def test_one_retry_with_the_validation_errors(self):
        planner = Planner(request())
        planner.first_call()
        bad = response(case(2))
        retry = planner.on_result("first", CallResult(structured=bad, subtype="success"))
        self.assertEqual(retry.kind, "retry")
        self.assertIn("Your previous answer failed validation", self.text_of(retry))
        self.assertIn("TC-007-01", self.text_of(retry))
        outcome = planner.on_result("retry", CallResult(structured=bad, subtype="success"))
        self.assertEqual(outcome.status, "invalid")
        self.assertTrue(outcome.validation_errors)
        self.assertEqual(outcome.response["test_cases"][0]["id"], "TC-007-02")  # kept for the user to see

    def test_continuation_after_a_cut_off_answer(self):
        planner = Planner(request())
        planner.first_call()
        full = json.dumps(response(case(1), case(2, "corner_case"), case(3, "corner_case")))
        cut = full[:full.index('"TC-007-03"') + 10]
        spec = planner.on_result("first", CallResult(subtype="error_during_execution", stop_reason="max_tokens",
                                                     is_error=True, partial_text=cut))
        self.assertEqual(spec.kind, "continuation")
        self.assertIn('Test cases to keep unchanged (may be empty; do not repeat or renumber them, only add what is '
                      'missing): [{"id": "TC-007-01"', self.text_of(spec))
        outcome = planner.on_result("continuation", CallResult(structured=response(case(3, "corner_case")),
                                                               subtype="success"))
        self.assertTrue(outcome.ok, outcome.validation_errors)
        self.assertEqual([c["id"] for c in outcome.response["test_cases"]], ["TC-007-01", "TC-007-02", "TC-007-03"])
        self.assertEqual(planner.attempts[0]["salvaged"], 2)

    def test_failures(self):
        planner = Planner(request())
        cut = planner.on_result("first", CallResult(stop_reason="max_tokens", partial_text="{\"test_cases\": [{\"id"))
        self.assertEqual(cut.status, "error")
        self.assertIn("cut off", cut.message)
        crashed = Planner(request()).on_result("first", CallResult(process_error="Claude Code could not be started."))
        self.assertEqual((crashed.status, crashed.message), ("error", "Claude Code could not be started."))
        auth = Planner(request()).on_result("first", CallResult(is_error=True, subtype="success", api_error_status=401,
                                                                 result_text="Invalid API key"))
        self.assertIn("not signed in", auth.message)
        limited = Planner(request()).on_result("first", CallResult(is_error=True, result_text="Usage limit reached"))
        self.assertIn("usage limit", limited.message)
        # A success without structured output gets one retry, then fails.
        planner = Planner(request())
        retry = planner.on_result("first", CallResult(subtype="success", result_text="Here you go"))
        self.assertEqual(retry.kind, "retry")
        failed = planner.on_result("retry", CallResult(subtype="error_max_structured_output_retries", is_error=True))
        self.assertIn("could not produce output matching the schema", failed.message)

    def test_preflight_insufficient_information(self):
        empty = request(text_blocks=[], images=[])
        self.assertEqual(preflight(empty), "insufficient_information")
        self.assertIsNone(preflight(request(text_blocks=[], images=[], domain_notes=["It exports orders."])))
        self.assertIsNone(preflight(request(text_blocks=[])))  # pictures alone are enough
        outcome = Planner(empty).insufficient()
        self.assertEqual((outcome.status, outcome.response["status"]), ("insufficient_information",
                                                                         "insufficient_information"))
        self.assertEqual(validation.validate(outcome.response, DesignerSettings(), "7").errors, [])


class ClaudeCallTests(unittest.TestCase):
    def test_stream_lines(self):
        call = ClaudeCall(Path(tempfile.gettempdir()))
        structured = response(case(1))
        lines = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": ["StructuredOutput"]},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "StructuredOutput",
                                                           "input": structured}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "", "stop_reason": "tool_use",
             "structured_output": structured, "usage": {"input_tokens": 10, "output_tokens": 20}, "duration_ms": 5},
        ]
        for line in lines:
            call.feed(json.dumps(line))
        result = call._result
        self.assertEqual((result.model, result.subtype, result.stop_reason), ("claude-opus-5-5", "success", "tool_use"))
        self.assertEqual(result.structured, structured)
        self.assertEqual((result.input_tokens, result.output_tokens, result.is_error), (10, 20, False))
        self.assertIn("TC-007-01", result.partial_text)
        call.feed(json.dumps({"type": "system", "subtype": "api_retry", "error": "max_output_tokens"}))
        self.assertTrue(result.truncated)
        call.feed("not json")

    def test_launcher_arguments(self):
        args = claude_launcher.designer_args("opus", Path("p.txt"), '{"type":"object"}')
        for flag in ("--input-format", "--json-schema", "--system-prompt-file", "--no-session-persistence",
                     "--restricted", "--tools="):
            self.assertIn(flag, args)
        self.assertEqual(args[:2], ["--model", "opus"])
        env = claude_launcher.designer_environment({"ANTHROPIC_API_KEY": "x", "PATH": "p"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "128000")
        message = json.loads(claude_launcher.stream_json_user_message([{"type": "text", "text": "hi"}]))
        self.assertEqual(message, {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                                   "parent_tool_use_id": None})


class FakeCall(QObject):
    finished = Signal(object)

    def __init__(self, results: list, specs: list, work_dir: Path):
        super().__init__()
        self.results, self.specs, self.work_dir = results, specs, work_dir

    def start(self, spec):
        self.specs.append(spec)
        QTimer.singleShot(0, lambda: self.finished.emit(self.results.pop(0)))

    def cancel(self):
        pass


class GenerationRunTests(unittest.TestCase):
    def run_with(self, results: list, req=None):
        specs, outcomes, progress = [], [], []
        with tempfile.TemporaryDirectory() as tmp:
            run = GenerationRun(req or request(), Path(tmp), call_factory=lambda d: FakeCall(results, specs, d))
            run.finished.connect(outcomes.append)
            run.progress.connect(progress.append)
            run.start()
            for _ in range(200):
                if outcomes:
                    break
                app.processEvents()
            leftovers = [p for p in Path(tmp).iterdir()]
        return outcomes, specs, progress, leftovers

    def test_retry_then_success(self):
        outcomes, specs, progress, leftovers = self.run_with([
            CallResult(structured=response(case(2)), subtype="success"),
            CallResult(structured=response(case(1)), subtype="success", model="claude-opus-5-5"),
        ])
        self.assertEqual([s.kind for s in specs], ["first", "retry"])
        self.assertTrue(outcomes[0].ok)
        self.assertIn("Answer failed validation; asking Claude to fix it…", progress)
        self.assertEqual(leftovers, [])  # the prompt folder is removed

    def test_no_call_without_content(self):
        outcomes, specs, _progress, _left = self.run_with([], request(text_blocks=[], images=[]))
        self.assertEqual(specs, [])
        self.assertEqual(outcomes[0].status, "insufficient_information")


class StoredRunTests(unittest.TestCase):
    def test_request_from_a_stored_backlog_and_saved_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "wta.sqlite3")
            try:
                path = Path(tmp) / "B.xlsx"
                path.write_bytes(build([SheetSpec("Main", {"A1": "wt", "B1": 7, "C1": "Orders", "C2": "Export them.",
                                                           "B5": 8, "C5": "Other"},
                                                  [Pic("C3", png(seed=1)), Pic("C4", png(seed=2))])]))
                view = wb.load_workbook(db, wb.import_workbook(db, path).workbook_id)
                backlog = view.sheets[0].backlogs[0]
                req = generations.request_for_backlog(db, backlog.id, DesignerSettings(), ["A note."],
                                                      transcriptions={"IMG-02": "A list."})
                self.assertEqual((req.backlog_key, req.title, req.sheet, req.status_code), ("7", "Orders", "Main", "wt"))
                self.assertEqual([(i.image_id, i.anchor_cell) for i in req.images], [("IMG-01", "C3"), ("IMG-02", "C4")])
                self.assertEqual(req.images[0].data, png(seed=1))
                self.assertEqual(req.images[1].transcription, "A list.")
                self.assertEqual(req.text_blocks, [("C2", "Export them.")])

                planner = Planner(req)
                ok = planner.on_result("first", CallResult(structured=response(case(1), questions=["Which format?"]),
                                                           subtype="success", model="claude-opus-5-5"))
                run_id = generations.save_outcome(db, backlog.id, ok)
                bad = copy.deepcopy(ok)
                bad.ok, bad.status, bad.validation_errors = False, "invalid", ["x"]
                generations.save_outcome(db, backlog.id, bad)
                stored = generations.load_run(db, run_id)
                self.assertEqual((stored.status, stored.schema_valid, stored.prompt_version), ("ok", True, "v1"))
                self.assertEqual([c["id"] for c in stored.test_cases], ["TC-007-01"])
                self.assertEqual(stored.open_questions, ["Which format?"])
                runs = generations.list_runs(db, backlog.id)
                self.assertEqual([r.status for r in runs], ["invalid", "ok"])
                self.assertEqual(runs[0].test_cases, [])  # invalid answers keep only the raw response
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
