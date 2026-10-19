"""Upload-time workbook analysis (spec 3.1–3.7): reading, layout detection, backlog map, pictures."""

import json
import unittest
from pathlib import Path

from wta.designer.layout import BLOCK, TABLE, UNKNOWN, Mapping, analyze_workbook, number_key
from wta.designer.xlsx_reader import WorkbookError, col_number, image_info, read_workbook

try:
    from designer_xlsx import Pic, SheetSpec, build, png
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import Pic, SheetSpec, build, png

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs" / "test-case-designer" / "Print_designer_6.xlsx"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "designer"
# The expected map of spec 3.3 lives next to the git-ignored fixtures, with the workbook it describes.
EXPECTED = FIXTURES / "backlog_map.json"


def summary(sheet):
    return [(b.number, b.title, b.first_row, b.last_row, [t.cell for t in b.text_blocks],
             [(sheet.pictures[i].label, sheet.pictures[i].picture.anchor_cell) for i in b.picture_indexes])
            for b in sheet.backlogs]


@unittest.skipUnless(REFERENCE.is_file() and EXPECTED.is_file(), "reference workbook or expected map not present")
class ReferenceWorkbookTests(unittest.TestCase):
    """The reference workbook reproduces the backlog map of spec 3.3."""

    @classmethod
    def setUpClass(cls):
        cls.analysis = analyze_workbook(read_workbook(REFERENCE))
        cls.expected = json.loads(EXPECTED.read_text("utf-8"))

    def test_backlog_map_of_spec_3_3(self):
        self.assertEqual(len(self.analysis.sheets), len(self.expected["sheets"]))
        for sheet, expected in zip(self.analysis.sheets, self.expected["sheets"]):
            self.assertEqual(sheet.name, expected["name"])
            self.assertEqual(sheet.layout, BLOCK)
            self.assertEqual((sheet.mapping.number_col, sheet.mapping.status_col),
                             (expected["number_col"], expected["status_col"]))
            if "confidence" in expected:
                self.assertEqual(sheet.confidence, expected["confidence"])
            self.assertEqual(json.loads(json.dumps(summary(sheet))), expected["backlogs"])
            unfinished = [b.number for b in sheet.backlogs if b.unfinished]
            self.assertEqual(unfinished, expected.get("unfinished", []))

    def test_attachments_and_reused_picture(self):
        attachments = sum(len(b.picture_indexes) for s in self.analysis.sheets for b in s.backlogs)
        self.assertEqual(attachments, self.expected["attachments"])
        md5 = {p.label: p.picture.md5 for s in self.analysis.sheets for p in s.pictures}
        reused = self.expected["reused"]
        self.assertEqual(md5[reused["image"]], md5[reused["same_as"]])
        self.assertTrue(md5[reused["same_as"]].startswith(reused["md5_prefix"]))
        self.assertEqual(len(set(md5.values())), self.expected["distinct_files"])

    def test_status_is_stored_and_formulas_are_not_data(self):
        statuses = {b.status_raw for s in self.analysis.sheets for b in s.backlogs}
        self.assertEqual(statuses, set(self.expected["status_values"]))
        texts = [t.text for s in self.analysis.sheets for b in s.backlogs for t in b.text_blocks]
        for value in self.expected["formula_values"]:
            self.assertNotIn(value, texts)

    def test_warnings(self):
        for sheet in self.analysis.sheets:
            for fragment in self.expected["warnings"].get(sheet.name, []):
                self.assertTrue(any(fragment in w for w in sheet.warnings), (sheet.name, fragment))

    @unittest.skipUnless((FIXTURES / "manifest.json").is_file(), "fixtures not extracted")
    def test_fixtures_match_the_workbook(self):
        manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
        pictures = {p.label: p.picture for s in self.analysis.sheets for p in s.pictures}
        self.assertEqual(len(manifest), len(pictures))
        for entry in manifest:
            picture = pictures[entry["image_id"]]
            self.assertEqual((entry["anchor_cell"], entry["md5"], entry["width"], entry["height"]),
                             (picture.anchor_cell, picture.md5, picture.width, picture.height))
            self.assertEqual((FIXTURES / entry["file"]).read_bytes(), picture.data)


class ReaderTests(unittest.TestCase):
    def test_not_a_workbook(self):
        with self.assertRaises(WorkbookError):
            read_workbook(b"plain text")

    def test_image_info_and_columns(self):
        self.assertEqual(image_info(png(7, 3)), ("image/png", 7, 3))
        self.assertEqual(image_info(b"nothing"), ("", 0, 0))
        self.assertEqual((col_number("A"), col_number("AA"), col_number("d")), (1, 27, 4))

    def test_unhandled_pictures_are_warned(self):
        data = build([SheetSpec("S", {"A1": 1, "B1": "Title"},
                                [Pic("B3", png(seed=1)), Pic("B4", png(seed=2), "absolute"),
                                 Pic("B5", png(seed=3), "oneCell")], shapes=2)], rich_data=True)
        raw = read_workbook(data)
        self.assertTrue(any("placed in cells" in w for w in raw.warnings))
        sheet = raw.sheets[0]
        self.assertEqual([p.anchor_cell for p in sheet.pictures], ["B3", "B5"])  # the absolute one is skipped
        self.assertTrue(any("without a cell anchor" in w for w in sheet.warnings))
        self.assertTrue(any("2 shapes" in w for w in sheet.warnings))

    def test_number_keys(self):
        self.assertEqual([number_key(v) for v in (5, 5.0, "7", "BL-5", "us 12", "#5", 5.5, -1, True, "text")],
                         ["5", "5", "7", "BL-5", "us 12", None, None, None, None, None])


class LayoutTests(unittest.TestCase):
    def analyze(self, *sheets, mappings=None):
        return analyze_workbook(read_workbook(build(list(sheets))), mappings)

    def test_block_layout_in_other_columns(self):
        """Nothing is hard-coded: numbers in B, titles in C, text in C and E, images anchored anywhere."""
        cells = {"B1": ("=COUNTA(B2:B99)", 3), "C1": ("=\"x\"", "3 Waiting"),
                 "B4": 10, "C4": "First", "C5": "Line one.", "E6": "Line two.",
                 "B9": 11, "C9": "Second", "C10": "Only line.",
                 "B12": 12, "C12": "Third", "C13": "Last line."}
        sheet = self.analyze(SheetSpec("Data", cells, [Pic("A5", png(seed=1)), Pic("F11", png(seed=2)),
                                                       Pic("C30", png(seed=3))])).sheets[0]
        self.assertEqual(sheet.layout, BLOCK)
        self.assertEqual(sheet.mapping.number_col, 2)
        self.assertIsNone(sheet.mapping.status_col)
        self.assertEqual(summary(sheet), [
            ("10", "First", 4, 8, ["C5", "E6"], [("IMG-01", "A5")]),
            ("11", "Second", 9, 11, ["C10"], [("IMG-02", "F11")]),
            ("12", "Third", 12, 13, ["C13"], [("IMG-03", "C30")]),  # below the last row: last backlog
        ])

    def test_alphanumeric_keys(self):
        cells = {"A2": "BL-1", "B2": "Login", "B3": "Text.", "A5": "BL-2", "B5": "Logout", "B6": "Text."}
        sheet = self.analyze(SheetSpec("S", cells)).sheets[0]
        self.assertEqual([b.number for b in sheet.backlogs], ["BL-1", "BL-2"])

    def test_table_layout(self):
        cells = {"A1": "ID", "B1": "Title", "C1": "Description", "D1": "Status", "E1": "Notes",
                 "A2": 1, "B2": "Login", "C2": "User signs in.", "D2": "done", "E2": "Extra.",
                 "A3": 2, "B3": "Logout", "C3": "User signs out.", "D3": "open"}
        sheet = self.analyze(SheetSpec("T", cells, [Pic("F2", png(seed=1)), Pic("F4", png(seed=2))])).sheets[0]
        self.assertEqual(sheet.layout, TABLE)
        self.assertEqual((sheet.mapping.header_row, sheet.mapping.title_col, sheet.mapping.status_col), (1, 2, 4))
        self.assertEqual(summary(sheet), [
            ("1", "Login", 2, 2, ["C2", "E2"], [("IMG-01", "F2")]),
            ("2", "Logout", 3, 3, ["C3"], [("IMG-02", "F4")]),  # nearest backlog row at or above
        ])
        self.assertEqual([b.status_raw for b in sheet.backlogs], ["done", "open"])

    def test_duplicate_missing_title_and_empty_backlogs(self):
        cells = {"A1": 1, "B1": "One", "B2": "Text.", "A3": 2, "C4": "Sentence used as title.",
                 "A5": 1, "B5": "One again", "A7": 3, "B7": "Nothing"}
        sheet = self.analyze(SheetSpec("S", cells)).sheets[0]
        numbers = [(b.number, b.occurrence) for b in sheet.backlogs]
        self.assertEqual(numbers, [("1", 1), ("2", 1), ("1", 2), ("3", 1)])
        two = sheet.backlogs[1]
        self.assertTrue(two.title_missing)
        self.assertEqual(two.title, "Sentence used as title.")
        warnings = " ".join(sheet.warnings)
        self.assertIn("backlog 1 appears more than once", warnings)
        self.assertIn("backlog 2 has no title", warnings)
        self.assertIn("backlog 3 has no description and no pictures", warnings)

    def test_no_backlogs(self):
        sheet = self.analyze(SheetSpec("Notes", {"A1": "Just", "A2": "text"})).sheets[0]
        self.assertEqual((sheet.layout, sheet.backlogs), (UNKNOWN, []))
        self.assertTrue(any("no backlog numbers" in w for w in sheet.warnings))

    def test_manual_mapping(self):
        cells = {"A1": "wt", "C1": 5, "D1": "Title", "D2": "Kept.", "F2": "Skipped column.",
                 "C9": 6, "D9": "Outside the range"}
        mapping = Mapping(BLOCK, number_col=3, title_col=4, description_cols=[4], status_col=1, last_row=8)
        sheet = self.analyze(SheetSpec("S", cells), mappings={"S": mapping}).sheets[0]
        self.assertTrue(sheet.manual)
        self.assertEqual(sheet.confidence, 1.0)
        self.assertEqual(summary(sheet), [("5", "Title", 1, 2, ["D2"], [])])  # ends at the last filled row in range
        self.assertEqual(Mapping.from_json(mapping.to_json()), mapping)
        self.assertIn("number in column C", mapping.describe())

    def test_image_labels_across_sheets(self):
        analysis = self.analyze(SheetSpec("Main", {"A1": 1, "B1": "T"}, [Pic("B2", png(seed=1)), Pic("B3", png(seed=2))]),
                                SheetSpec("Two", {"A1": 1, "B1": "T"}, [Pic("B2", png(seed=3))]),
                                SheetSpec("Three", {"A1": 1, "B1": "T"}, [Pic("B2", png(seed=4))]))
        self.assertEqual([[p.label for p in s.pictures] for s in analysis.sheets],
                         [["IMG-01", "IMG-02"], ["IMG-S1"], ["IMG-S2"]])


if __name__ == "__main__":
    unittest.main()
