"""PDF report, rendered by Qt from the report HTML (no extra dependency).

Needs a QGuiApplication (the app always has one).
"""

from pathlib import Path

from PySide6.QtCore import QMarginsF, QSizeF, QUrl
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from wta.export.html import all_screenshots, image_name, report_html
from wta.export.model import Report


def write_pdf(report: Report, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setTitle(report.title)
    writer.setCreator("Web Testing Assistant")
    writer.setResolution(96)
    writer.setPageLayout(QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Landscape, QMarginsF(12, 12, 12, 12),
        QPageLayout.Unit.Millimeter,
    ))

    document = QTextDocument()
    for shot in all_screenshots(report):
        image = QImage(str(shot))
        if not image.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(image_name(shot)), image)
    document.setHtml(report_html(report))
    rect = writer.pageLayout().paintRectPixels(writer.resolution())
    document.setPageSize(QSizeF(rect.width(), rect.height()))
    document.print_(writer)
    return path
