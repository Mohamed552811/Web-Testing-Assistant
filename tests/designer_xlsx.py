"""Build small .xlsx files for the Test Case Designer tests (raw XML, so drawings are exactly as written)."""

import io
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape


def png(width: int = 2, height: int = 2, seed: int = 0) -> bytes:
    """A tiny valid PNG; different seeds give different bytes (and MD5s)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([seed % 256, (seed * 7) % 256, y % 256]) * width for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@dataclass
class Pic:
    anchor: str  # top-left cell, e.g. "D5"
    data: bytes
    kind: str = "twoCell"  # twoCell | oneCell | absolute


@dataclass
class SheetSpec:
    name: str
    cells: dict  # "A1" -> str | int | float | ("=FORMULA", cached value)
    pictures: list[Pic] = field(default_factory=list)
    shapes: int = 0  # drawing shapes that are not pictures


def _split(ref: str) -> tuple[int, int]:
    letters, digits = re.match(r"([A-Z]+)(\d+)", ref).groups()
    col = 0
    for ch in letters:
        col = col * 26 + ord(ch) - 64
    return int(digits), col


def _cell_xml(ref: str, value) -> str:
    if isinstance(value, tuple):
        formula, cached = value
        if isinstance(cached, str):
            return f'<c r="{ref}" t="str"><f>{escape(formula.lstrip("="))}</f><v>{escape(cached)}</v></c>'
        return f'<c r="{ref}"><f>{escape(formula.lstrip("="))}</f><v>{cached}</v></c>'
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    return f'<c r="{ref}"><v>{value}</v></c>'


def _sheet_xml(spec: SheetSpec, has_drawing: bool) -> str:
    rows: dict[int, list[tuple[int, str]]] = {}
    for ref, value in spec.cells.items():
        row, col = _split(ref)
        rows.setdefault(row, []).append((col, _cell_xml(ref, value)))
    body = "".join(f'<row r="{r}">' + "".join(x for _c, x in sorted(cells)) + "</row>" for r, cells in sorted(rows.items()))
    drawing = '<drawing r:id="rId1"/>' if has_drawing else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheetData>{body}</sheetData>{drawing}</worksheet>")


def _drawing_xml(spec: SheetSpec) -> str:
    parts = []
    pic = ('<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{n}" name="Picture {n}"/><xdr:cNvPicPr/></xdr:nvPicPr>'
           '<xdr:blipFill><a:blip r:embed="rIdImg{n}"/></xdr:blipFill><xdr:spPr/></xdr:pic>')
    for n, picture in enumerate(spec.pictures, 1):
        row, col = _split(picture.anchor)
        start = (f"<xdr:from><xdr:col>{col - 1}</xdr:col><xdr:colOff>0</xdr:colOff>"
                 f"<xdr:row>{row - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>")
        if picture.kind == "absolute":
            parts.append(f'<xdr:absoluteAnchor><xdr:pos x="0" y="0"/><xdr:ext cx="1" cy="1"/>{pic.format(n=n)}'
                         "<xdr:clientData/></xdr:absoluteAnchor>")
        elif picture.kind == "oneCell":
            parts.append(f'<xdr:oneCellAnchor>{start}<xdr:ext cx="1" cy="1"/>{pic.format(n=n)}'
                         "<xdr:clientData/></xdr:oneCellAnchor>")
        else:
            parts.append(f"<xdr:twoCellAnchor>{start}<xdr:to><xdr:col>{col + 3}</xdr:col><xdr:colOff>0</xdr:colOff>"
                         f"<xdr:row>{row + 9}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>{pic.format(n=n)}"
                         "<xdr:clientData/></xdr:twoCellAnchor>")
    for n in range(spec.shapes):
        parts.append('<xdr:twoCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row>'
                     '<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff>'
                     f'<xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to><xdr:sp><xdr:nvSpPr><xdr:cNvPr id="{900 + n}" '
                     'name="Shape"/><xdr:cNvSpPr/></xdr:nvSpPr><xdr:spPr/></xdr:sp><xdr:clientData/></xdr:twoCellAnchor>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            + "".join(parts) + "</xdr:wsDr>")


REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rels(entries: list[tuple[str, str, str]]) -> str:
    items = "".join(f'<Relationship Id="{i}" Type="{REL}/{t}" Target="{target}"/>' for i, t, target in entries)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{RELS_NS}">{items}</Relationships>'


def build(sheets: list[SheetSpec], rich_data: bool = False) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        sheet_list = "".join(f'<sheet name="{escape(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
                             for i, s in enumerate(sheets, 1))
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'xmlns:r="{REL}"><sheets>{sheet_list}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   _rels([(f"rId{i}", "worksheet", f"worksheets/sheet{i}.xml") for i in range(1, len(sheets) + 1)]))
        media = 0
        for i, spec in enumerate(sheets, 1):
            has_drawing = bool(spec.pictures or spec.shapes)
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(spec, has_drawing))
            if not has_drawing:
                continue
            z.writestr(f"xl/worksheets/_rels/sheet{i}.xml.rels", _rels([("rId1", "drawing", f"../drawings/drawing{i}.xml")]))
            z.writestr(f"xl/drawings/drawing{i}.xml", _drawing_xml(spec))
            entries = []
            for n, picture in enumerate(spec.pictures, 1):
                media += 1
                z.writestr(f"xl/media/image{media}.png", picture.data)
                entries.append((f"rIdImg{n}", "image", f"../media/image{media}.png"))
            z.writestr(f"xl/drawings/_rels/drawing{i}.xml.rels", _rels(entries))
        if rich_data:
            z.writestr("xl/richData/rdrichvalue.xml", "<rvData/>")
    return out.getvalue()
