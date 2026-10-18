"""Mark the failing element in a bug screenshot.

For a Bug or Blocked step Claude names the element that is wrong ("highlight": its visible text or
accessible name, as in the snapshot). As soon as the step appears in results.json the app finds that
element in the live page, takes its own screenshot and draws a box around it. Claude's own screenshot
is kept as it is; the marked copy is saved next to it as step-N-highlight.png.
"""

import base64
import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWebEngineCore import QWebEngineScript

from wta.results import HIGHLIGHT_SUFFIX, SCREENSHOT_DIR

BOX_COLOR = "#E5484D"


def find_js(text: str) -> str:
    """JavaScript returning (as JSON text) the viewport box of the smallest visible element whose text,
    label, value or name matches `text` (exactly first, then containing it), or null."""
    return """
JSON.stringify((() => {
  const wanted = %s.replace(/\\s+/g, ' ').trim().toLowerCase();
  if (!wanted) return null;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const names = (el) => [el.getAttribute('aria-label'), el.innerText, el.value, el.placeholder, el.alt, el.title,
                         el.labels && el.labels[0] ? el.labels[0].innerText : ''].map(norm).filter(Boolean);
  let best = null;
  for (const el of document.querySelectorAll('body *')) {
    const box = el.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const found = names(el);
    const score = found.includes(wanted) ? 2 : found.some((n) => n.includes(wanted)) ? 1 : 0;
    if (!score) continue;
    const area = box.width * box.height;
    if (!best || score > best.score || (score === best.score && area < best.area)) {
      best = {score, area, x: box.left, y: box.top, width: box.width, height: box.height};
    }
  }
  if (!best) return null;
  return {x: best.x, y: best.y, width: best.width, height: best.height, dpr: window.devicePixelRatio,
          viewport_width: window.innerWidth, viewport_height: window.innerHeight};
})())
""" % json.dumps(text)


def parse_box(result) -> dict | None:
    if not isinstance(result, str):
        return None
    try:
        box = json.loads(result)
    except ValueError:
        return None
    return box if isinstance(box, dict) and box.get("width") and box.get("height") else None


def draw_box(image: QImage, box: dict) -> QImage:
    """A copy of the screenshot with a box around the element (CSS pixels scaled to the image)."""
    scale = image.width() / box["viewport_width"] if box.get("viewport_width") else float(box.get("dpr") or 1)
    marked = image.convertToFormat(QImage.Format.Format_ARGB32)
    painter = QPainter(marked)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(BOX_COLOR))
    pen.setWidthF(max(3.0, 2 * scale))
    painter.setPen(pen)
    margin = 4 * scale
    painter.drawRoundedRect(QRectF(box["x"] * scale - margin, box["y"] * scale - margin,
                                   box["width"] * scale + 2 * margin, box["height"] * scale + 2 * margin), 6, 6)
    painter.end()
    return marked


def highlight_path(run_dir: Path, order: int) -> Path:
    return run_dir / SCREENSHOT_DIR / f"step-{order}{HIGHLIGHT_SUFFIX}"


class Highlighter(QObject):
    """Find the element, take the app's own screenshot and save the marked copy."""

    def __init__(self, view, monitor, parent=None) -> None:
        super().__init__(parent)
        self.view = view  # the embedded QWebEngineView
        self.monitor = monitor  # PageMonitor: screenshots over the app's DevTools connection

    def highlight(self, text: str, target: Path, done: Callable[[Path | None], None]) -> None:
        def found(result) -> None:
            box = parse_box(result)
            if box is None:
                done(None)
                return
            self._capture(lambda image: done(self._save(image, box, target)))
        self.view.page().runJavaScript(find_js(text), QWebEngineScript.ScriptWorldId.ApplicationWorld, found)

    def _capture(self, use: Callable[[QImage | None], None]) -> None:
        if self.monitor is not None and self.monitor.connected:
            def captured(result: dict) -> None:
                data = result.get("data") if isinstance(result, dict) else None
                image = QImage.fromData(base64.b64decode(data), "PNG") if data else QImage()
                use(image if not image.isNull() else self.view.grab().toImage())
            self.monitor.request("Page.captureScreenshot", {"format": "png"}, captured)
        else:
            use(self.view.grab().toImage())

    @staticmethod
    def _save(image: QImage | None, box: dict, target: Path) -> Path | None:
        if image is None or image.isNull():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        return target if draw_box(image, box).save(str(target), "PNG") else None
