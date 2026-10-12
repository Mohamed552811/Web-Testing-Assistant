"""Report export: one report model, four formats."""

from pathlib import Path

from wta.db import Database
from wta.export.markdown import write_markdown
from wta.export.model import Report, build_report
from wta.export.pdf import write_pdf
from wta.export.word import write_docx
from wta.export.xlsx import write_xlsx

WRITERS = {"xlsx": write_xlsx, "pdf": write_pdf, "docx": write_docx, "md": write_markdown}


def export_report(db: Database, session_id: int, path: Path, fmt: str | None = None) -> Path:
    """Write the session's report. The format comes from `fmt` or the file extension."""
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    if fmt not in WRITERS:
        raise ValueError(f"Unknown export format: {fmt!r}")
    if path.suffix.lower() != f".{fmt}":
        path = path.with_suffix(f".{fmt}")
    return WRITERS[fmt](build_report(db, session_id), path)


__all__ = ["Report", "WRITERS", "build_report", "export_report"]
