"""Button and toolbar icons (Lucide, ISC licence, see assets/icons/LICENSE-lucide.txt), tinted to the theme.

Use `set_icon(widget_or_action, "play")`; the icon is re-tinted automatically when the theme changes.
"""

import weakref
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).with_name("assets")
ICON_DIR = ASSETS / "icons"
SIZES = (16, 20, 24, 32, 48)

_cache: dict[tuple[str, str], QIcon] = {}
_registered: "weakref.WeakKeyDictionary[object, tuple[str, str | None]]" = weakref.WeakKeyDictionary()


def _pixmaps(name: str, color: str) -> list[QPixmap]:
    svg = (ICON_DIR / f"{name}.svg").read_text("utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmaps = []
    for size in SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        pixmaps.append(QPixmap.fromImage(image))
    return pixmaps


def icon(name: str, color: str, disabled_color: str | None = None) -> QIcon:
    """An icon from the bundled set, drawn in `color` (and `disabled_color` when the button is disabled)."""
    key = (name, color, disabled_color)
    if key not in _cache:
        result = QIcon()
        for pixmap in _pixmaps(name, color):
            result.addPixmap(pixmap, QIcon.Mode.Normal)
        if disabled_color:
            for pixmap in _pixmaps(name, disabled_color):
                result.addPixmap(pixmap, QIcon.Mode.Disabled)
        _cache[key] = result
    return _cache[key]


def _color(role: str | None) -> str:
    from wta import theme

    palette = theme.current()
    return {"accent": palette.accent, "on-accent": palette.accent_text, "muted": palette.muted}.get(role or "", palette.text)


def _themed(name: str, role: str | None) -> QIcon:
    # Icons on accent buttons are white; when the button is disabled (grey) white would vanish.
    return icon(name, _color(role), _color("muted") if role == "on-accent" else None)


def set_icon(target, name: str, role: str | None = None) -> None:
    """Give a button or action an icon that follows the theme. `role`: None (text colour), "accent",
    "on-accent" (white, for buttons with an accent background) or "muted"."""
    _registered[target] = (name, role)
    target.setIcon(_themed(name, role))
    if hasattr(target, "setIconSize") and not isinstance(target, QAction):
        target.setIconSize(QSize(16, 16))


def retint_all() -> None:
    for target, (name, role) in list(_registered.items()):
        try:
            target.setIcon(_themed(name, role))
        except RuntimeError:  # the Qt object was deleted
            pass


def logo_icon() -> QIcon:
    """The app logo at every size Windows asks for (title bar, taskbar, Alt+Tab)."""
    return QIcon(str(ASSETS / "app.ico"))


def logo_pixmap(size: int) -> QPixmap:
    renderer = QSvgRenderer(str(ASSETS / ("logo-16.svg" if size <= 16 else "logo.svg")))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)
