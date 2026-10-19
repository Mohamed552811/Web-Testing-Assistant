"""Stored workbooks: import, versions and changed backlogs, mapping override, many-to-many pictures."""

import json
import tempfile
import unittest
from pathlib import Path

from wta.db import Database
from wta.db import workbooks as wb
from wta.designer.layout import BLOCK, Mapping
from wta.designer.xlsx_reader import WorkbookError

try:
    from designer_xlsx import Pic, SheetSpec, build, png
except ImportError:  # run as tests.<module>
    from tests.designer_xlsx import Pic, SheetSpec, build, png

REFERENCE = Path(__file__).resolve().parent.parent / "docs" / "test-case-designer" / "Print_designer_6.xlsx"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "designer" / "backlog_map.json"


def sheet(title_two: str = "Logout", text_three: str = "Help text.", second_sheet: bool = True) -> list[SheetSpec]:
    shared = png(seed=9)
    sheets = [SheetSpec("Main", {"A1": 1, "B1": "Login", "B2": "Sign in.",
                                 "A4": 2, "B4": title_two, "B5": "Sign out.",
                                 "A7": 3, "B7": "Help", "B8": text_three},
                        [Pic("B3", png(seed=1)), Pic("B6", shared), Pic("B9", shared)])]
    if second_sheet:
        sheets.append(SheetSpec("Extra", {"A1": "wt", "C1": 2, "D1": "Other", "D2": "More?"}, [Pic("D3", png(seed=4))]))
    return sheets


class WorkbookStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = Database(self.dir / "wta.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def write(self, name: str, sheets: list[SheetSpec]) -> Path:
        path = self.dir / name
        path.write_bytes(build(sheets))
        return path

    def test_import_and_load(self):
        result = wb.import_workbook(self.db, self.write("Backlogs.xlsx", sheet()))
        self.assertFalse(result.already_uploaded)
        view = wb.load_workbook(self.db, result.workbook_id)
        self.assertEqual((view.file_name, view.version_label, view.backlog_count), ("Backlogs.xlsx", "v1", 4))
        main, extra = view.sheets
        self.assertEqual([(b.number, b.title, [i.label for i in b.images]) for b in main.backlogs],
                         [("1", "Login", ["IMG-01"]), ("2", "Logout", ["IMG-02"]), ("3", "Help", ["IMG-03"])])
        self.assertEqual(main.backlogs[1].images[0].md5, main.backlogs[2].images[0].md5)  # same file, two attachments
        self.assertEqual(extra.backlogs[0].status_raw, "wt")
        self.assertTrue(extra.backlogs[0].unfinished)
        self.assertEqual(wb.image_data(self.db, main.backlogs[0].images[0].md5), png(seed=1))
        entry = wb.list_workbooks(self.db)[0]
        self.assertEqual((entry.sheet_count, entry.backlog_count), (2, 4))
        # The same file again is not stored twice.
        again = wb.import_workbook(self.db, self.write("Copy.xlsx", sheet()))
        self.assertEqual((again.workbook_id, again.already_uploaded), (result.workbook_id, True))

    def test_bad_file(self):
        bad = self.dir / "bad.xlsx"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(WorkbookError):
            wb.import_workbook(self.db, bad)

    def test_new_version_shows_changed_backlogs_and_offers_mappings(self):
        first = wb.import_workbook(self.db, self.write("Backlogs v1.xlsx", sheet()))
        extra = wb.load_workbook(self.db, first.workbook_id).sheets[1]
        wb.apply_mapping(self.db, extra.id, Mapping(BLOCK, number_col=3, title_col=4, status_col=1))
        second = wb.import_workbook(self.db, self.write("Backlogs v2.xlsx", sheet(title_two="Log out", second_sheet=True)))
        self.assertEqual(second.previous_id, first.workbook_id)
        self.assertEqual(set(second.reusable_mappings), {"Extra"})
        view = wb.load_workbook(self.db, second.workbook_id)
        self.assertEqual(view.version_label, "v2")
        self.assertEqual(view.changes, {"added": [], "removed": [], "changed": ["Main|2|1"]})
        third = wb.import_workbook(self.db, self.write("Backlogs_v3.xlsx", sheet(title_two="Log out", text_three="New help.",
                                                                                     second_sheet=False)))
        changes = wb.load_workbook(self.db, third.workbook_id).changes
        self.assertEqual(changes, {"added": [], "removed": ["Extra|2|1"], "changed": ["Main|3|1"]})
        self.assertEqual(wb.family_of("Order_backlog_6.xlsx"), "order backlog")
        self.assertEqual(wb.family_of("Backlogs v2.xlsx"), wb.family_of("backlogs-version-3.xlsx"))

    def test_apply_mapping_and_reset(self):
        result = wb.import_workbook(self.db, self.write("B.xlsx", sheet()))
        main = wb.load_workbook(self.db, result.workbook_id).sheets[0]
        # Read only rows 1-5: backlog 3 disappears; its picture is no longer attached.
        changed = wb.apply_mapping(self.db, main.id, Mapping(BLOCK, number_col=1, last_row=5))
        self.assertTrue(changed.manual)
        self.assertEqual([b.number for b in changed.backlogs], ["1", "2"])
        reset = wb.apply_mapping(self.db, main.id, None)
        self.assertFalse(reset.manual)
        self.assertEqual([b.number for b in reset.backlogs], ["1", "2", "3"])

    def test_pictures_are_many_to_many(self):
        result = wb.import_workbook(self.db, self.write("B.xlsx", sheet()))
        view = wb.load_workbook(self.db, result.workbook_id)
        login = view.sheets[0].backlogs[0]
        other_sheet_picture = next(i for i in view.images if i.label == "IMG-S1")
        img2 = next(i for i in view.images if i.label == "IMG-02")
        updated = wb.attach_image(self.db, login.id, other_sheet_picture.id)
        updated = wb.attach_image(self.db, login.id, img2.id)
        updated = wb.attach_image(self.db, login.id, img2.id)  # twice: no-op
        self.assertEqual([(i.label, i.origin) for i in updated.images],
                         [("IMG-01", "auto"), ("IMG-S1", "manual"), ("IMG-02", "manual")])
        # IMG-02 stays attached to backlog 2 as well.
        self.assertEqual([i.label for i in wb.load_workbook(self.db, result.workbook_id).sheets[0].backlogs[1].images],
                         ["IMG-02"])
        self.assertEqual([i.label for i in wb.move_image(self.db, login.id, img2.id, 0).images],
                         ["IMG-02", "IMG-01", "IMG-S1"])
        self.assertEqual([i.label for i in wb.detach_image(self.db, login.id, img2.id).images], ["IMG-01", "IMG-S1"])

    def test_attaching_changes_the_content_hash(self):
        first = wb.import_workbook(self.db, self.write("C v1.xlsx", sheet()))
        second = wb.import_workbook(self.db, self.write("C v2.xlsx", sheet(title_two="Changed")))
        view = wb.load_workbook(self.db, second.workbook_id)
        login = view.sheets[0].backlogs[0]
        wb.attach_image(self.db, login.id, view.images[-1].id)
        self.assertIn("Main|1|1", wb.load_workbook(self.db, second.workbook_id).changes["changed"])
        self.assertIsNotNone(first)

    def test_delete_keeps_shared_picture_files(self):
        first = wb.import_workbook(self.db, self.write("A.xlsx", sheet()))
        second = wb.import_workbook(self.db, self.write("Other.xlsx", sheet(title_two="Different")))
        shared_md5 = wb.load_workbook(self.db, first.workbook_id).images[0].md5
        wb.delete_workbook(self.db, first.workbook_id)
        self.assertEqual(wb.image_data(self.db, shared_md5), png(seed=1))  # still used by the other workbook
        wb.delete_workbook(self.db, second.workbook_id)
        with self.assertRaises(LookupError):
            wb.image_data(self.db, shared_md5)
        self.assertEqual(wb.list_workbooks(self.db), [])

    @unittest.skipUnless(REFERENCE.is_file() and EXPECTED.is_file(), "reference workbook or expected map not present")
    def test_reference_workbook(self):
        expected = json.loads(EXPECTED.read_text("utf-8"))
        view = wb.load_workbook(self.db, wb.import_workbook(self.db, REFERENCE).workbook_id)
        self.assertEqual([(s.name, len(s.backlogs)) for s in view.sheets],
                         [(s["name"], len(s["backlogs"])) for s in expected["sheets"]])
        self.assertEqual(sum(len(b.images) for s in view.sheets for b in s.backlogs), expected["attachments"])
        md5 = {i.label: i.md5 for i in view.images}
        reused = expected["reused"]
        self.assertEqual(md5[reused["image"]], md5[reused["same_as"]])
        attached = [i.label for s in view.sheets for b in s.backlogs for i in b.images]
        self.assertIn(reused["image"], attached)
        self.assertIn(reused["same_as"], attached)

if __name__ == "__main__":
    unittest.main()
