"""Render the app logo into the PNG and ICO files the app, reports and installer use.

Run from the repo root after changing the logo SVGs:
    .venv\\Scripts\\python tools\\build_icons.py
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[1] / "src" / "wta" / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg: Path, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(svg)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build_ico(images: dict[int, bytes]) -> bytes:
    """An ICO file whose entries are PNG images (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(images)
    for size, data in sorted(images.items()):
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> int:
    QGuiApplication(sys.argv[:1])
    logo, small = ASSETS / "logo.svg", ASSETS / "logo-16.svg"
    images = {size: png_bytes(render(small if size <= 16 else logo, size)) for size in ICO_SIZES}
    (ASSETS / "app.ico").write_bytes(build_ico(images))
    (ASSETS / "logo.png").write_bytes(images[256])
    (ASSETS / "logo-64.png").write_bytes(images[64])
    print("wrote app.ico, logo.png, logo-64.png in", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
