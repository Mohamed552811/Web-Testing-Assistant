"""The analysis summary shown after upload (spec 3.7 step 6), as HTML for the setup page."""

from datetime import UTC
from html import escape

from wta.db.workbooks import WorkbookView
from wta.designer.layout import LOW_CONFIDENCE, UNKNOWN


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def summary_html(view: WorkbookView) -> str:
    parts = [f"<h3>{escape(view.file_name)} <small>({escape(view.version_label)})</small></h3>",
             f"<p>Uploaded {view.uploaded_at.replace(tzinfo=UTC).astimezone():%Y-%m-%d %H:%M} · "
             f"{len(view.sheets)} sheet(s) · "
             f"{view.backlog_count} backlog(s) · {len(view.images)} picture(s)</p>"]
    if view.product_context:
        parts.append(f"<p><b>Product context:</b> {escape(view.product_context)}</p>")
    else:
        parts.append("<p><b>Product context:</b> not set. Add it with Edit mapping, so the test cases are written "
                     "for this product.</p>")
    if view.warnings:
        parts.append("<p><b>Workbook warnings</b></p>" + _list(view.warnings))
    if view.changes is not None:
        changes = view.changes
        if not any(changes.values()):
            parts.append("<p>No backlog changed since the previous version.</p>")
        else:
            labels = (("changed", "Changed"), ("added", "Added"), ("removed", "Removed"))
            lines = [f"{label}: " + ", ".join(_change_label(k) for k in changes[key])
                     for key, label in labels if changes.get(key)]
            parts.append("<p><b>Since the previous version</b></p>" + _list(lines))
    for sheet in view.sheets:
        if sheet.layout == UNKNOWN:
            layout = "no backlogs detected"
        else:
            how = "set by hand" if sheet.manual else f"detected, confidence {sheet.confidence:.0%}"
            layout = f"{sheet.layout} layout ({how}): {sheet.mapping.describe()}"
        flag = " ⚠" if not sheet.manual and sheet.confidence < LOW_CONFIDENCE else ""
        parts.append(f"<h4>Sheet “{escape(sheet.name)}”{flag}</h4><p>{escape(layout)}</p>")
        if sheet.backlogs:
            rows = "".join(
                f"<tr><td>{escape(b.number)}{' (' + str(b.occurrence) + ')' if b.occurrence > 1 else ''}</td>"
                f"<td>{escape(b.title)}</td><td>{b.first_row}–{b.last_row}</td><td align='center'>{len(b.text_blocks)}</td>"
                f"<td>{escape(', '.join(i.label for i in b.images)) or '–'}</td></tr>"
                for b in sheet.backlogs)
            parts.append("<table cellspacing='0' cellpadding='3' border='1' style='border-collapse:collapse'>"
                         "<tr><th>#</th><th>Title</th><th>Rows</th><th>Text</th><th>Pictures</th></tr>"
                         f"{rows}</table>")
        if sheet.warnings:
            parts.append(_list(sheet.warnings))
    return "".join(parts)


def _change_label(key: str) -> str:
    sheet, number, occurrence = key.split("|")
    return f"{sheet} #{number}" + (f" ({occurrence})" if occurrence != "1" else "")
