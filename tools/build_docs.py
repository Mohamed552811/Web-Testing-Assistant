"""Print the documentation (docs/source/documentation.html) to docs/Web-Testing-Assistant-Documentation.pdf.

    .venv\\Scripts\\python tools\\build_docs.py

Uses the Qt WebEngine (Chromium) that the app already depends on, so CSS page rules work: A4 pages,
page numbers in the footer, page breaks per chapter. It prints twice: the first print shows on which page
each chapter and section starts, the second fills those page numbers into the contents.
"""

import json
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QMarginsF, QUrl
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "source" / "documentation.html"
OUTPUT = ROOT / "docs" / "Web-Testing-Assistant-Documentation.pdf"

HEADINGS_JS = """JSON.stringify(Array.from(document.querySelectorAll('h1[id], h2[id]'))
  .filter(h => h.id !== 'contents')
  .map(h => ({id: h.id, num: (h.querySelector('.num') || {}).textContent || '',
              title: Array.from(h.childNodes).filter(n => !(n.classList && n.classList.contains('num')))
                          .map(n => n.textContent).join('').trim()})))"""


def wait_for(app: QApplication, done, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    if not done():
        raise SystemExit("Timed out")


def run_js(app: QApplication, page: QWebEnginePage, script: str):
    result: list = []
    page.runJavaScript(script, 0, result.append)
    wait_for(app, lambda: result)
    return result[0]


def print_pdf(app: QApplication, page: QWebEnginePage, path: Path) -> None:
    finished: list = []
    page.pdfPrintingFinished.connect(lambda _path, ok: finished.append(ok))
    # Qt's margins win over the CSS @page margins; the page numbers are drawn in the bottom margin.
    page.printToPdf(str(path), QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                           QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter))
    wait_for(app, lambda: finished, 120)
    page.pdfPrintingFinished.disconnect()
    if not finished[0]:
        raise SystemExit(f"Printing {path.name} failed")


def heading_pages(pdf_path: Path, headings: list[dict]) -> dict[str, int]:
    """Page number (1-based) of each heading, found by its "<number> <title>" line in the page text."""
    pdf = QPdfDocument()
    pdf.load(str(pdf_path))
    texts = [pdf.getAllText(i).text() for i in range(pdf.pageCount())]
    pdf.close()
    chapter_titles = [h["title"] for h in headings if "." not in h["num"]]
    # The contents pages list every title; skip them (they hold most chapter titles at once).
    is_contents = [sum(t in text for t in chapter_titles) >= 6 for text in texts]
    pages, pointer = {}, 0
    for heading in headings:
        line = re.compile(r"^\s*" + re.escape(heading["num"]) + r"\s*" + re.escape(heading["title"]) + r"\s*$",
                          re.MULTILINE)
        for index in range(pointer, len(texts)):
            if not is_contents[index] and line.search(texts[index]):
                pages[heading["id"]] = index + 1
                pointer = index
                break
    return pages


def main() -> int:
    app = QApplication(sys.argv[:1])
    page = QWebEnginePage()
    loaded: list = []
    page.loadFinished.connect(loaded.append)
    page.load(QUrl.fromLocalFile(str(SOURCE)))
    wait_for(app, lambda: loaded)
    if not loaded[0]:
        raise SystemExit(f"Could not load {SOURCE}")
    time.sleep(0.5)
    headings = json.loads(run_js(app, page, HEADINGS_JS))

    draft = OUTPUT.with_suffix(".draft.pdf")
    print_pdf(app, page, draft)
    pages = heading_pages(draft, headings)
    missing = [h["title"] for h in headings if h["id"] not in pages]
    run_js(app, page, "(() => { const p = %s; document.querySelectorAll('.pg').forEach(e => "
                      "{ e.textContent = p[e.dataset.for] || ''; }); return 1; })()" % json.dumps(pages))
    print_pdf(app, page, OUTPUT)
    draft.unlink(missing_ok=True)

    pdf = QPdfDocument()
    pdf.load(str(OUTPUT))
    count = pdf.pageCount()
    pdf.close()
    print(f"{OUTPUT} ({count} pages, {OUTPUT.stat().st_size / 1024 / 1024:.1f} MB)")
    if missing:
        print("Not found for the contents:", "; ".join(missing))
    page.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
