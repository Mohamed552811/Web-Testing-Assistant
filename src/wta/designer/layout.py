"""Upload-time analysis (spec 3.7): find the backlogs on each sheet and attach the pictures to them.

Nothing is hard-coded: the backlog-number column, title, description cells and row ranges are detected
per sheet (block or table layout), or set by hand with a Mapping. Formula cells are never backlog data.
"""

import bisect
import hashlib
import re
from dataclasses import asdict, dataclass, field

from wta.designer.xlsx_reader import Cell, Picture, RawSheet, RawWorkbook, cell_ref, col_letters

BLOCK = "block"
TABLE = "table"
UNKNOWN = "unknown"
LOW_CONFIDENCE = 0.7  # below this the summary suggests checking the mapping
HEADER_SEARCH_ROWS = 10
STATUS_MAX_LENGTH = 5

_KEY = re.compile(r"^[A-Za-z]{0,5}[-_ ]?\d+$")
_DIGITS = re.compile(r"\d+")
# Header synonyms for the table layout, checked in this order (a "Backlog title" header is a title).
_HEADER_SYNONYMS = (
    ("description", {"description", "descriptions", "details", "detail", "scenario", "scenarios"}),
    ("title", {"title", "name", "summary", "story"}),
    ("number", {"backlog", "id", "no", "#", "number", "num", "nr"}),
    ("status", {"status", "state"}),
)


@dataclass
class Mapping:
    """How backlogs are laid out on a sheet. Columns are 1-based; None means "detect" / "any"."""

    layout: str = BLOCK
    number_col: int = 0
    title_col: int | None = None  # block: None = first text cell right of the number
    description_cols: list[int] | None = None  # None = every text column (except number/status/title)
    status_col: int | None = None
    header_row: int | None = None  # table layout only
    first_row: int | None = None
    last_row: int | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Mapping":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def describe(self) -> str:
        parts = [f"number in column {col_letters(self.number_col)}" if self.number_col else "no number column"]
        if self.title_col:
            parts.append(f"title in {col_letters(self.title_col)}")
        if self.description_cols:
            parts.append("description in " + ", ".join(col_letters(c) for c in self.description_cols))
        if self.status_col:
            parts.append(f"status in {col_letters(self.status_col)} (ignored)")
        if self.header_row:
            parts.append(f"header row {self.header_row}")
        if self.first_row or self.last_row:
            parts.append(f"rows {self.first_row or 'start'}–{self.last_row or 'end'}")
        return ", ".join(parts)


@dataclass
class TextBlock:
    cell: str
    row: int
    text: str


@dataclass
class Backlog:
    number: str  # the key as text: "5", or "BL-5"
    occurrence: int  # 1, or 2+ when the number repeats on the sheet
    title: str
    title_cell: str
    status_raw: str | None  # stored, never used
    first_row: int
    last_row: int
    text_blocks: list[TextBlock]
    picture_indexes: list[int] = field(default_factory=list)  # into SheetAnalysis.pictures (default attachments)
    title_missing: bool = False

    @property
    def unfinished(self) -> bool:
        return any(block.text.rstrip().endswith("?") for block in self.text_blocks)


@dataclass
class LabeledPicture:
    label: str  # IMG-01, IMG-S1 ...
    picture: Picture


@dataclass
class SheetAnalysis:
    name: str
    index: int
    layout: str
    mapping: Mapping
    confidence: float
    warnings: list[str]
    backlogs: list[Backlog]
    pictures: list[LabeledPicture]
    manual: bool = False


@dataclass
class WorkbookAnalysis:
    sheets: list[SheetAnalysis]
    warnings: list[str]
    sha256: str


def number_key(value) -> str | None:
    """The backlog key of a cell value, or None when it is not one (integers, or BL-5 style codes)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and value >= 0 else None
    text = str(value).strip()
    return text if _KEY.match(text) else None


def _key_number(key: str) -> int:
    return int(_DIGITS.findall(key)[-1])


def content_hash(title: str, texts: list[str], image_md5s: list[str]) -> str:
    """Changes when a backlog's title, description or attached pictures change (spec 8.1 (1))."""
    payload = "\x1f".join([title, *texts, "|", *image_md5s])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _data_cells(sheet: RawSheet, mapping: Mapping | None = None) -> dict[tuple[int, int], Cell]:
    """Non-formula cells inside the mapping's row range."""
    first = mapping.first_row if mapping and mapping.first_row else 1
    last = mapping.last_row if mapping and mapping.last_row else None
    return {k: c for k, c in sheet.cells.items()
            if not c.formula and k[0] >= first and (last is None or k[0] <= last)}


# --- detection ------------------------------------------------------------------------------------


def _header_kind(text: str) -> str | None:
    words = set(re.findall(r"[a-z]+|#", text.lower()))
    for kind, synonyms in _HEADER_SYNONYMS:
        if words & synonyms:
            return kind
    return None


def _detect_table(cells: dict[tuple[int, int], Cell]) -> tuple[Mapping, float] | None:
    rows = sorted({r for r, _ in cells})
    for row in rows[:HEADER_SEARCH_ROWS]:
        kinds: dict[str, list[int]] = {}
        for (r, c), cell in sorted(cells.items()):
            if r == row and cell.is_text:
                kind = _header_kind(cell.value)
                if kind:
                    kinds.setdefault(kind, []).append(c)
        if "number" not in kinds or not ({"title", "description"} & kinds.keys()):
            continue
        number_col = kinds["number"][0]
        below = [cell for (r, c), cell in cells.items() if c == number_col and r > row]
        if not below:
            continue
        keyed = sum(1 for cell in below if number_key(cell.value))
        title_col = kinds.get("title", [None])[0]
        used = {number_col, title_col, kinds.get("status", [None])[0]}
        description = kinds.get("description", [])
        extra = sorted({c for (r, c), cell in cells.items() if r == row and cell.is_text and c not in used
                        and c not in description})
        mapping = Mapping(TABLE, number_col, title_col, description + extra or None,
                          kinds.get("status", [None])[0], row)
        return mapping, round(0.6 + 0.4 * keyed / len(below), 2)
    return None


def _detect_block(cells: dict[tuple[int, int], Cell]) -> tuple[Mapping, float, list[str]] | None:
    by_col: dict[int, list[Cell]] = {}
    last_text_col: dict[int, int] = {}  # row -> right-most text column
    for (r, c), cell in sorted(cells.items()):
        by_col.setdefault(c, []).append(cell)
        if cell.is_text:
            last_text_col[r] = max(last_text_col.get(r, 0), c)
    best = None
    for col, column in by_col.items():
        keys = [number_key(cell.value) for cell in column]
        if not column or any(k is None for k in keys):
            continue
        numbers = [_key_number(k) for k in keys]
        with_title = sum(1 for cell in column if last_text_col.get(cell.row, 0) > col)
        title_ratio = with_title / len(column)
        if not title_ratio:
            continue
        pairs = list(zip(numbers, numbers[1:]))
        increasing = sum(1 for a, b in pairs if b > a) / len(pairs) if pairs else 1.0
        consecutive = sum(1 for a, b in pairs if b - a == 1) / len(pairs) if pairs else 1.0
        score = 0.4 * title_ratio + 0.3 * increasing + 0.3 * consecutive
        rank = (len(column) >= 2, score, len(column))
        if best is None or rank > best[0]:
            best = (rank, col, column, score)
    if best is None:
        return None
    _rank, col, column, score = best
    notes = []
    if len(column) == 1:
        score *= 0.6
        notes.append("Only one backlog number was found, so the layout is a guess.")
    status_col = _detect_status(cells, col, [cell.row for cell in column])
    return Mapping(BLOCK, col, None, None, status_col), round(score, 2), notes


def _detect_status(cells, number_col: int, title_rows: list[int]) -> int | None:
    """A short-code column left of the number on the title rows (like "wt"). Recorded, never used."""
    for col in range(number_col - 1, 0, -1):
        values = [cells.get((row, col)) for row in title_rows]
        present = [v for v in values if v is not None]
        if present and len(present) * 2 >= len(title_rows) and all(
                v.is_text and len(v.value) <= STATUS_MAX_LENGTH and " " not in v.value for v in present):
            return col
    return None


# --- building backlogs ----------------------------------------------------------------------------


def _text(cell: Cell) -> str:
    value = cell.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _build_block(cells, mapping: Mapping, last_data_row: int) -> list[Backlog]:
    title_rows = sorted(r for (r, c), cell in cells.items() if c == mapping.number_col and number_key(cell.value))
    by_row: dict[int, list[tuple[int, Cell]]] = {}
    for (r, c), cell in sorted(cells.items()):
        by_row.setdefault(r, []).append((c, cell))
    row_list = sorted(by_row)
    backlogs = []
    for i, row in enumerate(title_rows):
        end = title_rows[i + 1] - 1 if i + 1 < len(title_rows) else max(last_data_row, row)
        if mapping.last_row:
            end = min(end, mapping.last_row)
        key = number_key(cells[(row, mapping.number_col)].value)
        title_cell = None
        if mapping.title_col:
            title_cell = cells.get((row, mapping.title_col))
        else:
            title_cell = next((cell for c, cell in by_row[row] if c > mapping.number_col and cell.is_text), None)
        skip = {mapping.number_col, mapping.status_col}
        rows = row_list[bisect.bisect_right(row_list, row):bisect.bisect_right(row_list, end)]
        blocks = [TextBlock(cell.ref, r, _text(cell)) for r in rows for c, cell in by_row[r]
                  if c not in skip and (mapping.description_cols is None or c in mapping.description_cols)]
        status = cells.get((row, mapping.status_col)) if mapping.status_col else None
        backlogs.append(Backlog(key, 1, _text(title_cell) if title_cell else "",
                                title_cell.ref if title_cell else "", _text(status) if status else None,
                                row, end, blocks))
    return backlogs


def _build_table(cells, mapping: Mapping) -> list[Backlog]:
    backlogs = []
    by_row: dict[int, list[tuple[int, Cell]]] = {}
    for (r, c), cell in sorted(cells.items()):
        if r > (mapping.header_row or 0):
            by_row.setdefault(r, []).append((c, cell))
    skip = {mapping.number_col, mapping.status_col, mapping.title_col}
    for row in sorted(by_row):
        number_cell = cells.get((row, mapping.number_col))
        key = number_key(number_cell.value) if number_cell else None
        if key is None:
            continue
        title_cell = cells.get((row, mapping.title_col)) if mapping.title_col else None
        blocks = [TextBlock(cell.ref, row, _text(cell)) for c, cell in by_row[row]
                  if c not in skip and (mapping.description_cols is None or c in mapping.description_cols)]
        status = cells.get((row, mapping.status_col)) if mapping.status_col else None
        backlogs.append(Backlog(key, 1, _text(title_cell) if title_cell else "", title_cell.ref if title_cell else "",
                                _text(status) if status else None, row, row, blocks))
    return backlogs


def _attach_pictures(backlogs: list[Backlog], pictures: list[LabeledPicture], layout: str, sheet: str) -> list[str]:
    """Default attachments by anchor row (spec 3.7 step 4). The user can change them later."""
    warnings = []
    if not backlogs:
        return warnings
    for index, labeled in enumerate(pictures):
        row = labeled.picture.anchor_row
        owner = None
        if layout == TABLE:
            above = [b for b in backlogs if b.first_row <= row]
            owner = above[-1] if above else None
        else:
            owner = next((b for b in backlogs if b.first_row <= row <= b.last_row), None)
            if owner is None and row > backlogs[-1].last_row:
                owner = backlogs[-1]
                warnings.append(f"Sheet \"{sheet}\": {labeled.label} at {labeled.picture.anchor_cell} is below the last "
                                f"filled row; attached to backlog {owner.number}.")
        if owner is None:
            warnings.append(f"Sheet \"{sheet}\": {labeled.label} at {labeled.picture.anchor_cell} is above the first "
                            "backlog and is not attached.")
            continue
        owner.picture_indexes.append(index)
    return warnings


def _finish(backlogs: list[Backlog], sheet: str) -> list[str]:
    """Occurrences, missing titles and content warnings (spec 3.5)."""
    warnings = []
    seen: dict[str, int] = {}
    for backlog in backlogs:
        lookup = backlog.number.lower()
        seen[lookup] = seen.get(lookup, 0) + 1
        backlog.occurrence = seen[lookup]
        if backlog.occurrence == 2:
            warnings.append(f"Sheet \"{sheet}\": backlog {backlog.number} appears more than once; "
                            "you will be asked which one to use.")
        if not backlog.title:
            backlog.title_missing = True
            if backlog.text_blocks:
                backlog.title = backlog.text_blocks[0].text
                warnings.append(f"Sheet \"{sheet}\": backlog {backlog.number} has no title; "
                                "its first sentence is used.")
            else:
                warnings.append(f"Sheet \"{sheet}\": backlog {backlog.number} has no title.")
        if not backlog.text_blocks and not backlog.picture_indexes:
            warnings.append(f"Sheet \"{sheet}\": backlog {backlog.number} has no description and no pictures; "
                            "add notes before generating.")
        elif backlog.unfinished:
            warnings.append(f"Sheet \"{sheet}\": backlog {backlog.number} has unfinished text (ends with \"?\").")
    return warnings


def analyze_sheet(sheet: RawSheet, pictures: list[LabeledPicture], mapping: Mapping | None = None) -> SheetAnalysis:
    manual = mapping is not None
    warnings = list(sheet.warnings)
    cells = _data_cells(sheet, mapping)
    last_data_row = max((r for r, _ in cells), default=0)
    if mapping is None:
        table = _detect_table(cells)
        block = _detect_block(cells)
        if table and (block is None or table[1] >= block[1]):
            mapping, confidence = table
        elif block:
            mapping, confidence, notes = block
            warnings += [f"Sheet \"{sheet.name}\": {note}" for note in notes]
        else:
            return SheetAnalysis(sheet.name, sheet.index, UNKNOWN, Mapping(UNKNOWN), 0.0,
                                 warnings + [f"Sheet \"{sheet.name}\": no backlog numbers found; "
                                             "set the mapping by hand if this sheet has backlogs."],
                                 [], pictures)
    else:
        confidence = 1.0
    backlogs = (_build_table(cells, mapping) if mapping.layout == TABLE
                else _build_block(cells, mapping, last_data_row))
    if not manual and confidence < LOW_CONFIDENCE:
        warnings.append(f"Sheet \"{sheet.name}\": low confidence ({confidence:.0%}); check the mapping.")
    warnings += _attach_pictures(backlogs, pictures, mapping.layout, sheet.name)
    warnings += _finish(backlogs, sheet.name)
    return SheetAnalysis(sheet.name, sheet.index, mapping.layout, mapping, confidence, warnings, backlogs,
                         pictures, manual)


def label_pictures(raw: RawWorkbook) -> dict[int, list[LabeledPicture]]:
    """IMG-01, IMG-02 … on the first sheet (anchor order); IMG-S1, IMG-S2 … on the other sheets."""
    labeled, other = {}, 0
    for sheet in raw.sheets:
        result = []
        for n, picture in enumerate(sheet.pictures, 1):
            if sheet.index == raw.sheets[0].index:
                label = f"IMG-{n:02d}"
            else:
                other += 1
                label = f"IMG-S{other}"
            result.append(LabeledPicture(label, picture))
        labeled[sheet.index] = result
    return labeled


def analyze_workbook(raw: RawWorkbook, mappings: dict[str, Mapping] | None = None) -> WorkbookAnalysis:
    """Analyze every sheet; `mappings` overrides detection for the named sheets."""
    labeled = label_pictures(raw)
    mappings = mappings or {}
    sheets = [analyze_sheet(sheet, labeled[sheet.index], mappings.get(sheet.name)) for sheet in raw.sheets]
    return WorkbookAnalysis(sheets, list(raw.warnings), raw.sha256)


def backlog_hash(backlog: Backlog, pictures: list[LabeledPicture]) -> str:
    return content_hash(backlog.title, [b.text for b in backlog.text_blocks],
                        [pictures[i].picture.md5 for i in backlog.picture_indexes])


__all__ = ["BLOCK", "TABLE", "UNKNOWN", "Mapping", "Backlog", "SheetAnalysis", "WorkbookAnalysis", "LabeledPicture",
           "analyze_sheet", "analyze_workbook", "number_key", "content_hash", "backlog_hash", "cell_ref"]
