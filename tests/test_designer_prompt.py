"""Test Case Designer settings, output presets and the prompt builder (spec 4.6, 5.3, 5.4, 7.1-7.3, 8.3)."""

import base64
import json
import re
import tempfile
import unittest
from pathlib import Path

from wta.db import Database
from wta.db import designer_settings
from wta.designer import prompt_builder as pb
from wta.designer.settings import (
    LATEST_OPUS,
    PER_STEP_EXPECTED,
    PINNED,
    DesignerSettings,
    OutputPreset,
    snapshot,
)

SPEC = Path(__file__).resolve().parent.parent / "docs" / "test-case-designer" / "TEST_CASE_DESIGNER_SPEC.md"


def with_preset(**changes) -> DesignerSettings:
    settings = DesignerSettings()
    preset = OutputPreset(name="Custom", **changes)
    settings.output_presets.append(preset)
    settings.output_preset = "Custom"
    return settings


def code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:text|json)\n(.*?)\n```", text, re.S)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_spec_8_3(self):
        self.assertEqual(DesignerSettings().to_json(), {
            "merge_same_flow": True,
            "merge_max_steps": 15,
            "avoid_heavy_and_performance": True,
            "ui_testing_policy": "exclude_visual_only",
            "output_language": "English",
            "tags": {"suggest": True, "vocabulary": ["smoke", "regression"]},
            "output_preset": "Default",
            "output_presets": [
                {"name": "Default", "builtin": True,
                 "optional_fields": {"tags": True, "preconditions": True, "basis": True, "source_reference": True},
                 "id_pattern": {"prefix": "TC", "backlog_pad": 3, "seq_pad": 2},
                 "priority_labels": ["High", "Medium", "Low"],
                 "steps_style": "steps_with_single_expected",
                 "title_prefix": "Verify that"}],
            "model": {"mode": "latest_opus", "pinned_id": None},
            "product_context": "an ERP 'Print' app (print designs, print settings, print editor, templates, "
                               "data fields, header/footer, preview, print command, permissions)",
        })

    def test_bad_values_fall_back_to_defaults(self):
        loaded = DesignerSettings.from_json({
            "merge_max_steps": 999, "ui_testing_policy": "pixels", "merge_same_flow": False,
            "tags": {"suggest": True, "vocabulary": ["Permissions", "bad tag!", "permissions"]},
            "output_presets": [{"name": "Default", "builtin": True, "title_prefix": "Hacked"},
                               {"name": "Jira", "id_pattern": {"prefix": "PD", "backlog_pad": 0, "seq_pad": 9},
                                "priority_labels": ["P1", "P2", "P3"], "steps_style": "nonsense",
                                "title_prefix": 'Say "hi"'},
                               {"name": "Jira"}],
            "output_preset": "Missing",
            "model": {"mode": "pinned", "pinned_id": "bad id with spaces"},
            "product_context": "  ",
        })
        default = DesignerSettings()
        self.assertFalse(loaded.merge_same_flow)
        self.assertEqual(loaded.merge_max_steps, 15)
        self.assertEqual(loaded.ui_testing_policy, "exclude_visual_only")
        self.assertEqual(loaded.tag_vocabulary, ["smoke", "regression", "permissions"])  # rule tags kept first
        self.assertEqual([p.name for p in loaded.output_presets], ["Default", "Jira"])
        self.assertEqual(loaded.output_presets[0].title_prefix, "Verify that")  # built-in comes from the code
        jira = loaded.output_presets[1]
        self.assertEqual((jira.id_prefix, jira.backlog_pad, jira.seq_pad), ("PD", 0, 2))
        self.assertEqual(jira.priority_labels, ("P1", "P2", "P3"))
        self.assertEqual((jira.steps_style, jira.title_prefix), (default.preset().steps_style, "Verify that"))
        self.assertEqual(loaded.output_preset, "Default")
        self.assertEqual((loaded.model_mode, loaded.pinned_model_id), (LATEST_OPUS, None))
        self.assertEqual(loaded.product_context, default.product_context)
        self.assertEqual(DesignerSettings.from_json("not a dict").to_json(), default.to_json())

    def test_problems_and_model(self):
        settings = DesignerSettings(tag_vocabulary=["smoke"], product_context=" ", model_mode=PINNED,
                                    pinned_model_id=None)
        text = " ".join(settings.problems())
        for expected in ("product context", "\"smoke\" and \"regression\"", "model id"):
            self.assertIn(expected, text)
        self.assertEqual(DesignerSettings().cli_model(), "opus")
        self.assertEqual(DesignerSettings(model_mode=PINNED, pinned_model_id="claude-opus-5-5").cli_model(),
                         "claude-opus-5-5")
        self.assertEqual(DesignerSettings().problems(), [])

    def test_saved_in_the_database_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "wta.sqlite3")
            try:
                self.assertEqual(designer_settings.load(db).to_json(), DesignerSettings().to_json())
                settings = with_preset(id_prefix="PD", backlog_pad=0, seq_pad=0)
                settings.merge_same_flow = False
                designer_settings.save(db, settings)
                loaded = designer_settings.load(db)
                self.assertFalse(loaded.merge_same_flow)
                self.assertEqual(loaded.preset().id_prefix, "PD")
                bad = DesignerSettings(product_context="")
                with self.assertRaises(ValueError):
                    designer_settings.save(db, bad)
                snap = snapshot(loaded)
                self.assertEqual(snap["selected_preset"]["name"], "Custom")
            finally:
                db.close()


class RuleBlockTests(unittest.TestCase):
    """One test per setting of spec 7.2: the setting picks exactly its block."""

    def blocks(self, **changes) -> list[str]:
        settings = DesignerSettings(**changes)
        return pb.rule_blocks(settings)

    def test_merging(self):
        on = self.blocks()[0]
        self.assertTrue(on.startswith("MERGING (on): "))
        self.assertIn("Keep a merged test case to about 15 steps", on)
        self.assertIn("about 9 steps", self.blocks(merge_max_steps=9)[0])
        self.assertEqual(self.blocks(merge_same_flow=False)[0], "MERGING (off): do not combine checks. Write one "
                         "test case per check, even when the flows are identical.")

    def test_heavy(self):
        self.assertTrue(self.blocks()[1].startswith("HEAVY (avoid): do NOT write performance"))
        self.assertTrue(self.blocks(avoid_heavy_and_performance=False)[1].startswith("HEAVY (allowed): "))

    def test_ui_policies(self):
        self.assertIn("do NOT test colors, positions", self.blocks()[2])
        self.assertIn("do NOT write assertions about UI elements themselves",
                      self.blocks(ui_testing_policy="exclude_all_ui_elements")[2])
        self.assertIn("tests of UI elements are allowed", self.blocks(ui_testing_policy="include_ui")[2])

    def test_tags(self):
        on = self.blocks(tag_vocabulary=["smoke", "regression", "permissions"])[3]
        self.assertIn('vocabulary: ["smoke", "regression", "permissions"]', on)
        self.assertIn('priority is "High"', on)
        self.assertEqual(self.blocks(tag_suggestions=False)[3], "TAGS: always return an empty tags array.")
        self.assertIn('priority is "P1"', pb.rule_blocks(with_preset(priority_labels=("P1", "P2", "P3")))[3])
        # No tags field in the preset: no tags block at all.
        self.assertEqual(len(pb.rule_blocks(with_preset(optional_fields={"tags": False}))), 3)

    @unittest.skipUnless(SPEC.is_file(), "spec not present")
    def test_blocks_are_verbatim_spec_7_2(self):
        spec = SPEC.read_text("utf-8")
        section = spec[spec.index("### 7.2"):spec.index("### 7.3")]
        expected = re.findall(r"^- [^:]*?: `(.*)`$", section, re.M)
        self.assertEqual(len(expected), 9)
        ours = [*self.blocks()[:1], self.blocks(merge_same_flow=False)[0],
                self.blocks()[1], self.blocks(avoid_heavy_and_performance=False)[1],
                self.blocks()[2], self.blocks(ui_testing_policy="exclude_all_ui_elements")[2],
                self.blocks(ui_testing_policy="include_ui")[2],
                self.blocks()[3], self.blocks(tag_suggestions=False)[3]]
        expected[0] = expected[0].replace("{{merge_max_steps}}", "15")
        expected[7] = expected[7].replace("{{tag_vocabulary}}", '["smoke", "regression"]')
        self.assertEqual(ours, expected)


class SystemPromptTests(unittest.TestCase):
    def test_skeleton(self):
        prompt = pb.system_prompt(DesignerSettings())
        self.assertNotIn("{{", prompt)
        # The owner's first instruction keeps its trailing space (verbatim, spec 2.2).
        self.assertIn("when i asking by backlog number \n-you can combine", prompt)
        self.assertIn("ACTIVE SETTINGS\nMERGING (on)", prompt)
        schema_text = prompt.split("JSON SCHEMA OF YOUR OUTPUT\n", 1)[1]
        self.assertEqual(json.loads(schema_text), pb.response_schema(DesignerSettings().preset()))
        self.assertIn("product backlogs of an ERP 'Print' app", prompt)
        self.assertEqual(pb.PROMPT_VERSION, "v1")

    def test_default_format_rules(self):
        rules = pb.format_rules(DesignerSettings().preset())
        self.assertEqual(rules[2], "- ID: TC-<backlog number padded to 3 digits>-<sequence padded to 2 digits>, "
                                   "starting at 01, in output order.")
        self.assertIn('Title: "Verify that ..."', rules[1])
        self.assertIn('every check starts with "Assert that"', rules[1])
        self.assertTrue(rules[3].startswith('- type: "happy_path" or "corner_case". priority: "High"'))
        self.assertEqual(len(rules), 8)

    def test_preset_changes_format_rules(self):
        preset = with_preset(id_prefix="PD", backlog_pad=0, seq_pad=0, priority_labels=("P1", "P2", "P3"),
                             title_prefix="Check that", steps_style=PER_STEP_EXPECTED,
                             optional_fields={"basis": False, "source_reference": False}).preset()
        rules = pb.format_rules(preset)
        self.assertEqual(rules[2], "- ID: PD-<backlog number>-<sequence>, starting at 1, in output order.")
        self.assertIn('Title: "Check that ..."', rules[1])
        self.assertIn('"action" is an imperative sentence', rules[1])
        self.assertIn('priority: "P1"', rules[3])
        self.assertFalse(any(r.startswith(("- basis", "- source_reference")) for r in rules))
        self.assertEqual(len(rules), 6)

    @unittest.skipUnless(SPEC.is_file(), "spec not present")
    def test_default_prompt_is_verbatim_spec_7_1(self):
        settings = DesignerSettings()
        skeleton = next(b for b in code_blocks(SPEC.read_text("utf-8")) if b.startswith('You are "Test Case Designer"'))
        expected = (skeleton.replace("{{product_context}}", settings.product_context)
                    .replace("{{rule_blocks}}", "\n".join(pb.rule_blocks(settings)))
                    .replace("{{response_schema}}", json.dumps(pb.response_schema(settings.preset()), indent=2)))
        self.assertEqual(pb.system_prompt(settings).rstrip("\n"), expected)


class SchemaTests(unittest.TestCase):
    @unittest.skipUnless(SPEC.is_file(), "spec not present")
    def test_default_schemas_are_spec_5_3(self):
        blocks = code_blocks(SPEC.read_text("utf-8"))
        full = json.loads(next(b for b in blocks if '"$id": "test-case-designer-response"' in b))
        api = json.loads(next(b for b in blocks if b.startswith('{\n  "type": "object"')))
        preset = DesignerSettings().preset()
        self.assertEqual(pb.response_schema(preset), full)
        self.assertEqual(pb.api_schema(preset), api)

    def test_default_schema_rules(self):
        schema = pb.response_schema(DesignerSettings().preset())
        case = schema["$defs"]["test_case"]
        self.assertEqual(case["required"], ["id", "backlog_number", "title", "type", "priority", "tags",
                                            "preconditions", "steps", "expected_result", "basis", "source_reference"])
        pattern = re.compile(case["properties"]["id"]["pattern"])
        self.assertTrue(pattern.match("TC-003-02"))
        self.assertFalse(pattern.match("TC-3-2"))
        self.assertEqual(case["properties"]["steps"]["minItems"], 2)
        api_case = pb.api_schema(DesignerSettings().preset())["$defs"]["test_case"]
        flat = json.dumps(api_case)
        for unsupported in ("minLength", "maxLength", "minimum", "pattern", "uniqueItems", "minItems"):
            self.assertNotIn(unsupported, flat)

    def test_preset_options_shape_both_schemas(self):
        preset = with_preset(id_prefix="PD", backlog_pad=0, seq_pad=0, priority_labels=("P1", "P2", "P3"),
                             title_prefix="Check (UI) that", steps_style=PER_STEP_EXPECTED,
                             optional_fields={"tags": False, "preconditions": False}).preset()
        for schema in (pb.response_schema(preset), pb.api_schema(preset)):
            case = schema["$defs"]["test_case"]
            self.assertNotIn("tags", case["properties"])
            self.assertNotIn("preconditions", case["required"])
            self.assertIn("basis", case["required"])
            self.assertEqual(case["properties"]["priority"], {"enum": ["P1", "P2", "P3"]})
            step = case["properties"]["steps"]["items"]
            self.assertEqual((step["type"], step["required"]), ("object", ["action", "expected"]))
        props = pb.response_schema(preset)["$defs"]["test_case"]["properties"]
        self.assertTrue(re.match(props["id"]["pattern"], "PD-3-12"))
        title = re.compile(props["title"]["pattern"])
        self.assertTrue(title.match("Check (UI) that the list is empty"))
        self.assertFalse(title.match("Verify that the list is empty"))


class UserMessageTests(unittest.TestCase):
    def setUp(self):
        self.images = [pb.PromptImage("IMG-04", "C114", "image/png", b"\x89PNG-one"),
                       pb.PromptImage("IMG-13", "C512", "image/png", b"", transcription="Same screen as IMG-04.")]
        self.row = pb.backlog_row_data("9", "Order export", "Main", 431, 546,
                                       [("D432", "The user exports an order."), ("D509", "Which formats?")],
                                       self.images, ["Exports are available to managers only."], status_code="wt")

    def test_backlog_row_data_shape(self):
        self.assertEqual(list(self.row), ["backlog_number", "status_code", "title", "source", "description_blocks",
                                          "domain_notes", "images"])
        self.assertEqual(self.row["backlog_number"], 9)
        self.assertEqual(self.row["source"], {"sheet": "Main", "first_row": 431, "last_row": 546})
        self.assertEqual(self.row["images"], [{"image_id": "IMG-04", "anchor_cell": "C114"},
                                              {"image_id": "IMG-13", "anchor_cell": "C512",
                                               "transcription": "Same screen as IMG-04."}])
        self.assertEqual(pb.backlog_number_value("BL-5"), "BL-5")

    def test_text_and_content_blocks(self):
        text = pb.user_message_text(self.row, ["Exports are available to managers only."], ["Backlog 3 defines the formats."],
                                    "Focus on permissions", [{"id": "TC-009-01"}])
        self.assertTrue(text.startswith('Backlog number: 9\nSource: sheet "Main", rows 431-546\n'))
        self.assertIn('"backlog_number": 9', text)
        self.assertIn("Notes from the user (may be empty):\n- Exports are available to managers only.\n", text)
        self.assertIn("other backlogs or files (may be empty):\n- Backlog 3 defines the formats.\n", text)
        self.assertIn("Additional guidance from the user (may be empty): Focus on permissions\n", text)
        self.assertIn('only add what is missing): [{"id": "TC-009-01"}]', text)
        self.assertTrue(text.rstrip().endswith("Return the JSON object only."))
        empty = pb.user_message_text(self.row, [])
        self.assertIn("Notes from the user (may be empty):\n\n", empty)
        self.assertIn("only add what is missing): \n", empty)

        blocks = pb.user_content(self.images, text)
        self.assertEqual([b["type"] for b in blocks], ["text", "image", "text"])  # IMG-13 has no picture data
        self.assertEqual(blocks[0]["text"], "Image IMG-04 (anchor C114):")
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]), b"\x89PNG-one")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        self.assertEqual(blocks[-1]["text"], text)

    def test_fill_is_single_pass(self):
        self.assertEqual(pb.fill("{{a}} {{b}}", a="{{b}}", b="x"), "{{b}} x")
        with self.assertRaises(KeyError):
            pb.fill("{{missing}}")


if __name__ == "__main__":
    unittest.main()
