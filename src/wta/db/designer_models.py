"""Test Case Designer tables (spec 8.2, adapted): uploaded workbooks, their sheets, backlogs and pictures.

Tables are prefixed `designer_` so they stay apart from the testing tables in the same database.
Pictures are stored once per file content (ImageBlob, keyed by MD5); every anchored picture and
every backlog attachment keeps its own row, so a reused screenshot is never dropped (spec 3.3).
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from wta.db.models import Base, utcnow


class Workbook(Base):
    __tablename__ = "designer_workbook"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    # Versions of the same file (same family) are numbered v1, v2 …; changes lists backlogs that differ
    # from the previous version: {"added": [...], "removed": [...], "changed": [...]} of "sheet|number|occurrence".
    family: Mapped[str] = mapped_column(String(255), default="")
    version_label: Mapped[str] = mapped_column(String(20), default="v1")
    previous_id: Mapped[int | None] = mapped_column(ForeignKey("designer_workbook.id", ondelete="SET NULL"))
    changes: Mapped[dict | None] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary))  # the original file, for re-analysis

    sheets: Mapped[list["Sheet"]] = relationship(
        back_populates="workbook", cascade="all, delete-orphan", order_by="Sheet.position"
    )
    images: Mapped[list["Image"]] = relationship(
        back_populates="workbook", cascade="all, delete-orphan", order_by="Image.id"
    )


class Sheet(Base):
    __tablename__ = "designer_sheet"

    id: Mapped[int] = mapped_column(primary_key=True)
    workbook_id: Mapped[int] = mapped_column(ForeignKey("designer_workbook.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    layout_type: Mapped[str] = mapped_column(String(20))  # block | table | unknown
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)  # layout.Mapping.to_json()
    mapping_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    workbook: Mapped[Workbook] = relationship(back_populates="sheets")
    backlogs: Mapped[list["BacklogItem"]] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", order_by="BacklogItem.first_row"
    )


class BacklogItem(Base):
    __tablename__ = "designer_backlog"

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("designer_sheet.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(50))  # numeric or alphanumeric key as detected
    # A number repeated on one sheet is kept (warned at upload; the user picks one), so the key
    # is (sheet, number, occurrence) instead of the spec's UNIQUE(sheet_id, number).
    occurrence: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text, default="")
    title_cell: Mapped[str] = mapped_column(String(20), default="")
    title_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    status_raw: Mapped[str | None] = mapped_column(String(50))  # stored, never used
    first_row: Mapped[int] = mapped_column(Integer)
    last_row: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), default="")

    sheet: Mapped[Sheet] = relationship(back_populates="backlogs")
    text_blocks: Mapped[list["BacklogTextBlock"]] = relationship(
        back_populates="backlog", cascade="all, delete-orphan", order_by="BacklogTextBlock.position"
    )
    images: Mapped[list["BacklogImage"]] = relationship(
        back_populates="backlog", cascade="all, delete-orphan", order_by="BacklogImage.position"
    )

    __table_args__ = (UniqueConstraint("sheet_id", "number", "occurrence", name="uq_backlog_number"),)


class BacklogTextBlock(Base):
    __tablename__ = "designer_text_block"

    id: Mapped[int] = mapped_column(primary_key=True)
    backlog_item_id: Mapped[int] = mapped_column(ForeignKey("designer_backlog.id", ondelete="CASCADE"))
    cell: Mapped[str] = mapped_column(String(20))
    row: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)

    backlog: Mapped[BacklogItem] = relationship(back_populates="text_blocks")


class ImageBlob(Base):
    """One stored picture file, shared by every anchored picture with the same content."""

    __tablename__ = "designer_image_blob"

    md5: Mapped[str] = mapped_column(String(32), primary_key=True)
    mime: Mapped[str] = mapped_column(String(50))
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary))


class Image(Base):
    """A picture anchored in a workbook (IMG-01 …), whatever backlogs it is attached to."""

    __tablename__ = "designer_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    workbook_id: Mapped[int] = mapped_column(ForeignKey("designer_workbook.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(20))
    md5: Mapped[str] = mapped_column(ForeignKey("designer_image_blob.md5"))
    source_sheet: Mapped[str] = mapped_column(String(255))
    anchor_cell: Mapped[str] = mapped_column(String(20))
    anchor_row: Mapped[int] = mapped_column(Integer)
    anchor_col: Mapped[int] = mapped_column(Integer)
    media_path: Mapped[str] = mapped_column(Text, default="")

    workbook: Mapped[Workbook] = relationship(back_populates="images")
    blob: Mapped[ImageBlob] = relationship()

    __table_args__ = (UniqueConstraint("workbook_id", "label", name="uq_image_label"),)


class DesignerSetting(Base):
    """Workspace-level settings (spec 8.2 `settings`); the feature settings live under name "feature"."""

    __tablename__ = "designer_setting"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(50), default="workspace")
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (UniqueConstraint("scope", "name", name="uq_designer_setting"),)


class BacklogImage(Base):
    """Many-to-many: a picture attached to a backlog, automatically (anchor row) or by hand."""

    __tablename__ = "designer_backlog_image"

    backlog_item_id: Mapped[int] = mapped_column(ForeignKey("designer_backlog.id", ondelete="CASCADE"),
                                                 primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("designer_image.id", ondelete="CASCADE"), primary_key=True)
    label: Mapped[str] = mapped_column(Text, default="")  # optional caption
    position: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(10), default="auto")  # auto | manual

    backlog: Mapped[BacklogItem] = relationship(back_populates="images")
    image: Mapped[Image] = relationship()
