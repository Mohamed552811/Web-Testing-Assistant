"""Choose a CSV or Excel file of test cases and add them to the library after a preview."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from wta.db import library
from wta.db.models import LibraryItem
from wta.test_case_import import import_file

PREVIEW = 12


def import_into_library(db, parent: QWidget | None = None, path: str | None = None) -> list[LibraryItem]:
    if path is None:
        path, _filter = QFileDialog.getOpenFileName(
            parent, "Import test cases", "", "Test cases (*.csv *.xlsx);;All files (*)")
        if not path:
            return []
    result = import_file(path)
    if not result.cases:
        QMessageBox.warning(parent, "Import test cases", "\n".join(result.warnings) or "No test cases found.")
        return []
    names = "\n".join(f"• {case.name}" for case in result.cases[:PREVIEW])
    more = f"\n… and {len(result.cases) - PREVIEW} more" if len(result.cases) > PREVIEW else ""
    notes = ("\n\nNotes:\n" + "\n".join(result.warnings)) if result.warnings else ""
    answer = QMessageBox.question(
        parent, "Import test cases",
        f"Add {len(result.cases)} test case(s) from {Path(path).name} to the library?\n\n{names}{more}{notes}")
    if answer != QMessageBox.StandardButton.Yes:
        return []
    source = f"Imported from {Path(path).name}"
    return library.add_items(db, [library.ItemInput(scenario=case, source=source) for case in result.cases])
