"""Read an .xlsx (a ZIP of XML parts) as raw cells and anchored pictures (spec 3.7, step 1).

Plain zipfile + ElementTree, so every picture keeps its original bytes (for MD5) and its cell anchor
from the drawing XML. Nothing here knows about backlogs; see layout.py.
"""

import hashlib
import io
import posixpath
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
_R_ID = f"{{{NS['r']}}}id"
_R_EMBED = f"{{{NS['r']}}}embed"
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
MAX_PARTS = 5000  # refuse absurd archives
_NOT_PICTURES = {"sp": "shape", "cxnSp": "connector", "graphicFrame": "chart or other object", "grpSp": "group"}


class WorkbookError(ValueError):
    """The file is not a readable .xlsx."""


@dataclass
class Cell:
    row: int  # 1-based
    col: int  # 1-based
    value: str | int | float | bool
    formula: bool = False

    @property
    def ref(self) -> str:
        return cell_ref(self.row, self.col)

    @property
    def is_text(self) -> bool:
        return isinstance(self.value, str)


@dataclass
class Picture:
    media_path: str  # e.g. xl/media/image1.png
    data: bytes = field(repr=False)
    anchor_row: int  # 1-based (the drawing XML is 0-based)
    anchor_col: int
    order: int  # position in the drawing XML
    md5: str = ""
    mime: str = ""
    width: int = 0
    height: int = 0

    @property
    def anchor_cell(self) -> str:
        return cell_ref(self.anchor_row, self.anchor_col)


@dataclass
class RawSheet:
    name: str
    index: int  # 0-based position in the workbook
    cells: dict[tuple[int, int], Cell]
    pictures: list[Picture]
    warnings: list[str]


@dataclass
class RawWorkbook:
    sheets: list[RawSheet]
    warnings: list[str]
    sha256: str


def col_letters(col: int) -> str:
    letters = ""
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def col_number(letters: str) -> int:
    number = 0
    for ch in letters.strip().upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"Not a column: {letters!r}")
        number = number * 26 + ord(ch) - 64
    return number


def cell_ref(row: int, col: int) -> str:
    return f"{col_letters(col)}{row}"


def image_info(data: bytes) -> tuple[str, int, int]:
    """(mime, width, height) from the file header; ("", 0, 0) for unknown formats."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return "image/gif", width, height
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", 0, 0
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return "image/jpeg", width, height
            i += 2 + length
        return "image/jpeg", 0, 0
    return "", 0, 0


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id -> (type suffix, absolute part path) for a part, from its _rels file."""
    folder, name = posixpath.split(part)
    rels_path = posixpath.join(folder, "_rels", name + ".rels")
    if rels_path not in z.namelist():
        return {}
    result = {}
    for rel in ET.fromstring(z.read(rels_path)).findall("rel:Relationship", NS):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        result[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], path)
    return result


def _shared_strings(z: zipfile.ZipFile, rels: dict) -> list[str]:
    path = next((p for kind, p in rels.values() if kind == "sharedStrings"), None)
    if path is None or path not in z.namelist():
        return []
    root = ET.fromstring(z.read(path))
    return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root.findall("m:si", NS)]


def _number(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() and "e" not in text.lower() and abs(value) < 1e15 else value


def _cells(root: ET.Element, strings: list[str]) -> dict[tuple[int, int], Cell]:
    cells = {}
    for c in root.iter(f"{{{NS['m']}}}c"):
        match = _CELL_REF.match(c.get("r", ""))
        if not match:
            continue
        row, col = int(match.group(2)), col_number(match.group(1))
        kind = c.get("t", "n")
        formula = c.find("m:f", NS) is not None
        v = c.find("m:v", NS)
        text = v.text if v is not None else None
        value: str | int | float | bool | None
        if kind == "s":
            value = strings[int(text)] if text is not None and text.isdigit() and int(text) < len(strings) else None
        elif kind == "inlineStr":
            value = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
        elif kind in ("str", "d"):
            value = text
        elif kind == "b":
            value = text == "1" if text is not None else None
        elif kind == "e":
            value = None  # errors (and pictures placed in cells, which Excel stores as #VALUE! with rich data)
        else:
            try:
                value = _number(text) if text is not None else None
            except ValueError:
                value = None
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        cells[(row, col)] = Cell(row, col, value.strip() if isinstance(value, str) else value, formula)
    return cells


def _pictures(z: zipfile.ZipFile, drawing: str, sheet_name: str) -> tuple[list[Picture], list[str]]:
    pictures, warnings = [], []
    rels = _rels(z, drawing)
    root = ET.fromstring(z.read(drawing))
    order = 0
    ignored: dict[str, int] = {}
    for anchor in root:
        tag = anchor.tag.rsplit("}", 1)[-1]
        pics = list(anchor.iter(f"{{{NS['xdr']}}}pic"))
        if not pics:
            for el in anchor:
                kind = el.tag.rsplit("}", 1)[-1]
                if kind in _NOT_PICTURES:
                    ignored[kind] = ignored.get(kind, 0) + 1
            continue
        start = anchor.find("xdr:from", NS)
        if tag == "absoluteAnchor" or start is None:
            warnings.append(f"Sheet \"{sheet_name}\": {len(pics)} picture(s) placed without a cell anchor were ignored; "
                            "attach them by hand if needed.")
            continue
        row = int(start.findtext("xdr:row", "0", NS)) + 1
        col = int(start.findtext("xdr:col", "0", NS)) + 1
        for pic in pics:
            blip = pic.find(".//a:blip", NS)
            rel = rels.get(blip.get(_R_EMBED) if blip is not None else None)
            if rel is None or rel[1] not in z.namelist():
                warnings.append(f"Sheet \"{sheet_name}\": a picture at {cell_ref(row, col)} has no image data (linked picture?).")
                continue
            data = z.read(rel[1])
            mime, width, height = image_info(data)
            if not mime:
                warnings.append(f"Sheet \"{sheet_name}\": the picture at {cell_ref(row, col)} ({posixpath.basename(rel[1])}) "
                                "is not PNG, JPEG, GIF or WebP and was ignored.")
                continue
            pictures.append(Picture(rel[1], data, row, col, order, hashlib.md5(data).hexdigest(), mime, width, height))
            order += 1
    if ignored:
        parts = ", ".join(f"{count} {_NOT_PICTURES[kind]}{'s' if count > 1 else ''}" for kind, count in sorted(ignored.items()))
        warnings.append(f"Sheet \"{sheet_name}\": ignored drawing objects that are not pictures ({parts}).")
    return pictures, warnings


def read_workbook(source: Path | str | bytes) -> RawWorkbook:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise WorkbookError("The file is not an Excel workbook (.xlsx).") from exc
    with z:
        names = z.namelist()
        if len(names) > MAX_PARTS or "xl/workbook.xml" not in names:
            raise WorkbookError("The file is not an Excel workbook (.xlsx).")
        warnings = []
        if any(n.startswith("xl/richData/") for n in names):
            warnings.append("The workbook has pictures placed in cells (Place in Cell). They are not read; "
                            "use Place over Cells in Excel, or attach them by hand.")
        book_rels = _rels(z, "xl/workbook.xml")
        strings = _shared_strings(z, book_rels)
        sheets = []
        for index, sheet in enumerate(ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet")):
            name = sheet.get("name", f"Sheet {index + 1}")
            kind, part = book_rels.get(sheet.get(_R_ID), ("", ""))
            if kind != "worksheet" or part not in names:
                warnings.append(f"Sheet \"{name}\" is not a worksheet (chart or dialog sheet) and was skipped.")
                continue
            root = ET.fromstring(z.read(part))
            pictures, sheet_warnings = [], []
            for rel_kind, drawing in _rels(z, part).values():
                if rel_kind == "drawing" and drawing in names:
                    found, notes = _pictures(z, drawing, name)
                    pictures += found
                    sheet_warnings += notes
            pictures.sort(key=lambda p: (p.anchor_row, p.anchor_col, p.order))
            sheets.append(RawSheet(name, index, _cells(root, strings), pictures, sheet_warnings))
    return RawWorkbook(sheets, warnings, hashlib.sha256(data).hexdigest())
