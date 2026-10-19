"""Uploaded workbooks for the Test Case Designer: import with analysis, versions, mapping override and
image attachments (spec 3.7, 8.1 (1) and (4)).

Functions return plain dataclasses (views), so callers never touch ORM objects after the session closes.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from wta.db import Database
from wta.db.designer_models import BacklogImage, BacklogItem, BacklogTextBlock, Image, ImageBlob, Sheet, Workbook
from wta.designer import layout
from wta.designer.layout import Mapping
from wta.designer.xlsx_reader import read_workbook

_VERSION_SUFFIX = re.compile(r"[\s_.-]*(v(er(sion)?)?[\s_.-]*)?\d+([._]\d+)*$", re.IGNORECASE)


@dataclass
class ImageView:
    id: int
    label: str
    md5: str
    mime: str
    width: int
    height: int
    source_sheet: str
    anchor_cell: str
    origin: str = ""  # on a backlog: auto | manual


@dataclass
class BacklogView:
    id: int
    sheet_id: int
    number: str
    occurrence: int
    title: str
    title_missing: bool
    status_raw: str | None
    first_row: int
    last_row: int
    text_blocks: list[tuple[str, str]]  # (cell, text)
    images: list[ImageView]

    @property
    def unfinished(self) -> bool:
        return any(text.rstrip().endswith("?") for _cell, text in self.text_blocks)


@dataclass
class SheetView:
    id: int
    name: str
    position: int
    layout: str
    mapping: Mapping
    manual: bool
    confidence: float
    warnings: list[str]
    backlogs: list[BacklogView]


@dataclass
class WorkbookView:
    id: int
    file_name: str
    version_label: str
    uploaded_at: datetime
    previous_id: int | None
    changes: dict | None
    warnings: list[str]
    sheets: list[SheetView]
    images: list[ImageView]

    @property
    def backlog_count(self) -> int:
        return sum(len(s.backlogs) for s in self.sheets)

    def sheet(self, sheet_id: int) -> SheetView:
        return next(s for s in self.sheets if s.id == sheet_id)


@dataclass
class WorkbookListEntry:
    id: int
    file_name: str
    version_label: str
    uploaded_at: datetime
    sheet_count: int
    backlog_count: int


@dataclass
class ImportResult:
    workbook_id: int
    already_uploaded: bool = False
    previous_id: int | None = None
    # Hand-set mappings of the previous version's sheets with the same names, to offer for reuse.
    reusable_mappings: dict[str, Mapping] = field(default_factory=dict)


def family_of(file_name: str) -> str:
    """"Print_designer_6.xlsx" and "Print designer v7.xlsx" are versions of the family "print designer"."""
    stem = Path(file_name).stem
    stem = _VERSION_SUFFIX.sub("", stem) or stem
    return re.sub(r"[\s_.-]+", " ", stem).strip().lower()


# --- import ---------------------------------------------------------------------------------------


def import_workbook(db: Database, path: Path | str) -> ImportResult:
    """Analyze and store an uploaded .xlsx. Raises xlsx_reader.WorkbookError for unreadable files."""
    path = Path(path)
    data = path.read_bytes()
    raw = read_workbook(data)
    with db.Session() as orm:
        existing = orm.scalar(select(Workbook).where(Workbook.sha256 == raw.sha256))
        if existing is not None:
            return ImportResult(existing.id, already_uploaded=True)
    analysis = layout.analyze_workbook(raw)
    family = family_of(path.name)
    with db.Session() as orm, orm.begin():
        previous = orm.scalar(select(Workbook).where(Workbook.family == family).order_by(Workbook.id.desc()))
        count = orm.scalar(select(func.count()).select_from(Workbook).where(Workbook.family == family))
        workbook = Workbook(file_name=path.name, sha256=raw.sha256, family=family, version_label=f"v{count + 1}",
                            previous_id=previous.id if previous else None, warnings=analysis.warnings, data=data)
        orm.add(workbook)
        orm.flush()
        images = _store_images(orm, workbook, analysis)
        for sheet in analysis.sheets:
            _store_sheet(orm, workbook, sheet, images)
        orm.flush()
        reusable = {}
        if previous is not None:
            workbook.changes = _changes(orm, previous.id, workbook.id)
            names = {s.name for s in analysis.sheets}
            reusable = {s.name: Mapping.from_json(s.mapping) for s in previous.sheets
                        if s.mapping_manual and s.name in names}
        return ImportResult(workbook.id, previous_id=workbook.previous_id, reusable_mappings=reusable)


def _store_images(orm, workbook: Workbook, analysis: layout.WorkbookAnalysis) -> dict[str, Image]:
    images = {}
    for sheet in analysis.sheets:
        for labeled in sheet.pictures:
            picture = labeled.picture
            if orm.get(ImageBlob, picture.md5) is None:
                orm.add(ImageBlob(md5=picture.md5, mime=picture.mime, width=picture.width, height=picture.height,
                                  data=picture.data))
                orm.flush()
            image = Image(workbook_id=workbook.id, label=labeled.label, md5=picture.md5, source_sheet=sheet.name,
                          anchor_cell=picture.anchor_cell, anchor_row=picture.anchor_row,
                          anchor_col=picture.anchor_col, media_path=picture.media_path)
            orm.add(image)
            images[labeled.label] = image
    orm.flush()
    return images


def _store_sheet(orm, workbook: Workbook, analysis: layout.SheetAnalysis, images: dict[str, Image],
                 sheet: Sheet | None = None) -> Sheet:
    if sheet is None:
        sheet = Sheet(workbook_id=workbook.id, position=analysis.index, name=analysis.name)
        orm.add(sheet)
    sheet.layout_type = analysis.layout
    sheet.mapping = analysis.mapping.to_json()
    sheet.mapping_manual = analysis.manual
    sheet.confidence = analysis.confidence
    sheet.warnings = analysis.warnings
    orm.flush()
    for backlog in analysis.backlogs:
        item = BacklogItem(sheet_id=sheet.id, number=backlog.number, occurrence=backlog.occurrence,
                           title=backlog.title, title_cell=backlog.title_cell, title_missing=backlog.title_missing,
                           status_raw=backlog.status_raw, first_row=backlog.first_row, last_row=backlog.last_row,
                           content_hash=layout.backlog_hash(backlog, analysis.pictures))
        item.text_blocks = [BacklogTextBlock(cell=b.cell, row=b.row, text=b.text, position=i)
                            for i, b in enumerate(backlog.text_blocks)]
        item.images = [BacklogImage(image_id=images[analysis.pictures[index].label].id, position=i, origin="auto")
                       for i, index in enumerate(backlog.picture_indexes)]
        orm.add(item)
    return sheet


def _keyed_hashes(orm, workbook_id: int) -> dict[str, str]:
    rows = orm.execute(
        select(Sheet.name, BacklogItem.number, BacklogItem.occurrence, BacklogItem.content_hash)
        .join(BacklogItem, BacklogItem.sheet_id == Sheet.id).where(Sheet.workbook_id == workbook_id)
    )
    return {f"{name}|{number}|{occurrence}": digest for name, number, occurrence, digest in rows}


def _changes(orm, previous_id: int, workbook_id: int) -> dict:
    """Backlogs added, removed or changed (title, description or attached pictures) since the previous version."""
    before, after = _keyed_hashes(orm, previous_id), _keyed_hashes(orm, workbook_id)
    return {
        "added": sorted(k for k in after if k not in before),
        "removed": sorted(k for k in before if k not in after),
        "changed": sorted(k for k in after if k in before and after[k] != before[k]),
    }


# --- mapping override -----------------------------------------------------------------------------


def apply_mapping(db: Database, sheet_id: int, mapping: Mapping | None) -> SheetView:
    """Re-read one sheet with a hand-set mapping (None = back to automatic detection).

    The sheet's backlogs are rebuilt, so pictures attached by hand on this sheet go back to the defaults."""
    with db.Session() as orm, orm.begin():
        sheet = orm.get(Sheet, sheet_id)
        if sheet is None:
            raise LookupError(f"Sheet {sheet_id} not found")
        workbook = sheet.workbook
        raw = read_workbook(workbook.data)
        raw_sheet = next((s for s in raw.sheets if s.name == sheet.name), None)
        if raw_sheet is None:
            raise LookupError(f"Sheet \"{sheet.name}\" is no longer in the stored file")
        labeled = layout.label_pictures(raw)[raw_sheet.index]
        analysis = layout.analyze_sheet(raw_sheet, labeled, mapping)
        for item in list(sheet.backlogs):
            orm.delete(item)
        orm.flush()
        images = {image.label: image for image in workbook.images}
        _store_sheet(orm, workbook, analysis, images, sheet)
        orm.flush()
        _refresh_changes(orm, workbook)
    return load_workbook(db, workbook.id).sheet(sheet_id)


def _refresh_changes(orm, workbook: Workbook) -> None:
    if workbook.previous_id is not None:
        workbook.changes = _changes(orm, workbook.previous_id, workbook.id)
    for later in orm.scalars(select(Workbook).where(Workbook.previous_id == workbook.id)):
        later.changes = _changes(orm, workbook.id, later.id)


# --- reading --------------------------------------------------------------------------------------


def _image_view(image: Image, origin: str = "") -> ImageView:
    return ImageView(image.id, image.label, image.md5, image.blob.mime, image.blob.width, image.blob.height,
                     image.source_sheet, image.anchor_cell, origin)


def _backlog_view(item: BacklogItem) -> BacklogView:
    return BacklogView(item.id, item.sheet_id, item.number, item.occurrence, item.title, item.title_missing,
                       item.status_raw, item.first_row, item.last_row,
                       [(b.cell, b.text) for b in item.text_blocks],
                       [_image_view(link.image, link.origin) for link in item.images])


def load_workbook(db: Database, workbook_id: int) -> WorkbookView:
    with db.Session() as orm:
        workbook = orm.get(Workbook, workbook_id)
        if workbook is None:
            raise LookupError(f"Workbook {workbook_id} not found")
        sheets = [SheetView(s.id, s.name, s.position, s.layout_type, Mapping.from_json(s.mapping), s.mapping_manual,
                            s.confidence, list(s.warnings or []), [_backlog_view(b) for b in s.backlogs])
                  for s in workbook.sheets]
        return WorkbookView(workbook.id, workbook.file_name, workbook.version_label, workbook.uploaded_at,
                            workbook.previous_id, workbook.changes, list(workbook.warnings or []), sheets,
                            [_image_view(i) for i in workbook.images])


def list_workbooks(db: Database) -> list[WorkbookListEntry]:
    with db.Session() as orm:
        backlog_counts = dict(orm.execute(
            select(Sheet.workbook_id, func.count(BacklogItem.id))
            .join(BacklogItem, BacklogItem.sheet_id == Sheet.id).group_by(Sheet.workbook_id)
        ).all())
        sheet_counts = dict(orm.execute(select(Sheet.workbook_id, func.count(Sheet.id)).group_by(Sheet.workbook_id)).all())
        return [WorkbookListEntry(w.id, w.file_name, w.version_label, w.uploaded_at, sheet_counts.get(w.id, 0),
                                  backlog_counts.get(w.id, 0))
                for w in orm.scalars(select(Workbook).order_by(Workbook.id.desc()))]


def delete_workbook(db: Database, workbook_id: int) -> None:
    """Delete a workbook; picture files no other workbook uses are deleted too."""
    with db.Session() as orm, orm.begin():
        workbook = orm.get(Workbook, workbook_id)
        if workbook is None:
            return
        md5s = {image.md5 for image in workbook.images}
        orm.delete(workbook)
        orm.flush()
        used = set(orm.scalars(select(Image.md5).where(Image.md5.in_(md5s))))
        for md5 in md5s - used:
            orm.delete(orm.get(ImageBlob, md5))


def image_data(db: Database, md5: str) -> bytes:
    with db.Session() as orm:
        blob = orm.get(ImageBlob, md5)
        if blob is None:
            raise LookupError(f"Image {md5} not found")
        return blob.data


# --- image attachments (many-to-many) -------------------------------------------------------------


def attach_image(db: Database, backlog_id: int, image_id: int) -> BacklogView:
    """Attach a picture of the same workbook (any sheet) to a backlog; already attached is a no-op."""
    with db.Session() as orm, orm.begin():
        item = orm.get(BacklogItem, backlog_id)
        image = orm.get(Image, image_id)
        if item is None or image is None:
            raise LookupError("Backlog or image not found")
        if image.workbook_id != item.sheet.workbook_id:
            raise ValueError("The picture belongs to another workbook")
        if all(link.image_id != image_id for link in item.images):
            item.images.append(BacklogImage(image_id=image_id, position=len(item.images), origin="manual"))
        _rehash(orm, item)
    return load_backlog(db, backlog_id)


def detach_image(db: Database, backlog_id: int, image_id: int) -> BacklogView:
    with db.Session() as orm, orm.begin():
        item = orm.get(BacklogItem, backlog_id)
        if item is None:
            raise LookupError(f"Backlog {backlog_id} not found")
        item.images = [link for link in item.images if link.image_id != image_id]
        for position, link in enumerate(item.images):
            link.position = position
        _rehash(orm, item)
    return load_backlog(db, backlog_id)


def move_image(db: Database, backlog_id: int, image_id: int, new_position: int) -> BacklogView:
    """Reorder a backlog's pictures (the order they are sent to the model)."""
    with db.Session() as orm, orm.begin():
        item = orm.get(BacklogItem, backlog_id)
        if item is None:
            raise LookupError(f"Backlog {backlog_id} not found")
        links = list(item.images)
        link = next((link for link in links if link.image_id == image_id), None)
        if link is None:
            raise LookupError("The picture is not attached to this backlog")
        links.remove(link)
        links.insert(max(0, min(new_position, len(links))), link)
        for position, each in enumerate(links):
            each.position = position
        orm.flush()
        orm.expire(item, ["images"])
        _rehash(orm, item)
    return load_backlog(db, backlog_id)


def _rehash(orm, item: BacklogItem) -> None:
    orm.flush()
    orm.refresh(item)
    item.content_hash = layout.content_hash(item.title, [b.text for b in item.text_blocks],
                                            [link.image.md5 for link in item.images])
    orm.flush()
    _refresh_changes(orm, item.sheet.workbook)


def load_backlog(db: Database, backlog_id: int) -> BacklogView:
    with db.Session() as orm:
        item = orm.get(BacklogItem, backlog_id)
        if item is None:
            raise LookupError(f"Backlog {backlog_id} not found")
        return _backlog_view(item)

