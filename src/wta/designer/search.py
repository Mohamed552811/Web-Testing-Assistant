"""Find a backlog by the user's request (spec 3.4, 3.5): "backlog 5", "5" and "#5" are all accepted.

Numbers match exactly ("1" never matches "10"); alphanumeric keys (BL-5) match exactly, ignoring case.
Lookups are scoped to one sheet, or to all sheets when the user chooses so.
"""

import re
from dataclasses import dataclass

from wta.db.workbooks import BacklogView, SheetView

_REQUEST = re.compile(r"^\s*(?:backlog\s*(?:no\.?|number|#)?\s*|#\s*)?(?P<key>[A-Za-z]{0,5}[-_ ]?\d+)\s*$", re.I)


def parse_request(text: str) -> str | None:
    """The backlog key in a request, or None when the text is not a backlog request."""
    match = _REQUEST.match(text or "")
    if not match:
        return None
    key = match.group("key").strip()
    return str(int(key)) if key.isdigit() else key


def _same(a: str, b: str) -> bool:
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.lower() == b.lower()


@dataclass
class Match:
    sheet: SheetView
    backlog: BacklogView

    @property
    def label(self) -> str:
        repeat = f" (occurrence {self.backlog.occurrence}, rows {self.backlog.first_row}–{self.backlog.last_row})" \
            if self.backlog.occurrence > 1 else ""
        return f"Sheet “{self.sheet.name}” · #{self.backlog.number} {self.backlog.title}{repeat}"


@dataclass
class SearchResult:
    key: str | None
    matches: list[Match]
    message: str = ""  # shown when nothing (or nothing usable) was found

    @property
    def unique(self) -> Match | None:
        return self.matches[0] if len(self.matches) == 1 else None


def find(sheets: list[SheetView], request: str, sheet_id: int | None = None) -> SearchResult:
    """Look up a backlog in one sheet (sheet_id) or all sheets (None). No model call is ever made here."""
    key = parse_request(request)
    if key is None:
        return SearchResult(None, [], f"“{request.strip()}” is not a backlog number. Type for example "
                                      "backlog 5, 5 or #5.")
    scope = [s for s in sheets if sheet_id is None or s.id == sheet_id]
    matches = [Match(s, b) for s in scope for b in s.backlogs if _same(b.number, key)]
    if matches:
        return SearchResult(key, matches)
    if len(scope) == 1:
        numbers = ", ".join(b.number for b in scope[0].backlogs) or "none"
        return SearchResult(key, [], f"Backlog {key} not found in sheet “{scope[0].name}”. "
                                     f"Backlogs on this sheet: {numbers}.")
    listing = "; ".join(f"“{s.name}”: {', '.join(b.number for b in s.backlogs) or 'none'}" for s in scope)
    return SearchResult(key, [], f"Backlog {key} not found in any sheet. Backlogs per sheet: {listing}.")


def content_problem(backlog: BacklogView, has_notes: bool) -> str:
    """Spec 3.5 for a chosen backlog: what blocks or limits generation ("" when nothing does)."""
    if not backlog.text_blocks and not backlog.images:
        if has_notes:
            return "No description text and no pictures: test cases will be generated from your notes only."
        return "No description text and no pictures. Add notes describing the backlog before generating."
    if not backlog.text_blocks:
        return "No description text: test cases will be generated from the pictures."
    return ""
