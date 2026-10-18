"""Light and dark themes with the dark brick-red accent.

"system" follows Windows' light/dark setting and switches live when it changes. Status colours
(Pass/Fail/Blocked) are separate from the accent, so a Fail never looks like a highlighted button.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

THEMES = {"system": "Follow Windows", "light": "Light", "dark": "Dark"}


@dataclass(frozen=True)
class Palette:
    window: str
    panel: str
    base: str
    alt: str
    text: str
    muted: str
    border: str
    accent: str
    accent_hover: str
    accent_text: str
    selection: str
    selection_text: str
    header: str


LIGHT = Palette(
    window="#f4f5f7", panel="#ffffff", base="#ffffff", alt="#f6f7f9", text="#1f2933", muted="#5f6b76",
    border="#d3d8de", accent="#9B2D20", accent_hover="#B23A2A", accent_text="#ffffff",
    selection="#f2d6d1", selection_text="#1f2933", header="#eceef2",
)
DARK = Palette(
    window="#16181b", panel="#1e2125", base="#1a1d21", alt="#212429", text="#e6e8eb", muted="#9aa3ad",
    border="#363b42", accent="#C8553D", accent_hover="#D96A52", accent_text="#ffffff",
    selection="#4d2a23", selection_text="#f3f4f6", header="#262a30",
)

_current: Palette = LIGHT
_mode = "system"


def current() -> Palette:
    return _current


def resolve(mode: str) -> Palette:
    """The palette for a theme setting; "system" asks Windows."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    scheme = QGuiApplication.styleHints().colorScheme()
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def stylesheet(p: Palette) -> str:
    return f"""
    QWidget {{ font-family: "Segoe UI", sans-serif; font-size: 9.5pt; }}
    QMainWindow, QDialog {{ background: {p.window}; }}
    QToolBar {{ background: {p.panel}; border: none; border-bottom: 1px solid {p.border}; spacing: 4px; padding: 3px 6px; }}
    QToolBar QToolButton {{ padding: 4px 8px; border-radius: 6px; color: {p.text}; }}
    QToolBar QToolButton:hover {{ background: {p.alt}; }}
    QToolBar QToolButton:disabled {{ color: {p.muted}; }}
    QPushButton {{ background: {p.panel}; color: {p.text}; border: 1px solid {p.border}; border-radius: 6px;
                  padding: 5px 12px; min-height: 18px; }}
    QPushButton:hover {{ border-color: {p.accent}; }}
    QPushButton:pressed {{ background: {p.alt}; }}
    QPushButton:disabled {{ color: {p.muted}; background: {p.alt}; }}
    QPushButton:default, QPushButton[primary="true"] {{ background: {p.accent}; color: {p.accent_text};
                  border: 1px solid {p.accent}; font-weight: 600; }}
    QPushButton:default:hover, QPushButton[primary="true"]:hover {{ background: {p.accent_hover}; }}
    QPushButton[primary="true"]:disabled {{ background: {p.border}; border-color: {p.border}; color: {p.muted}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox {{ background: {p.base}; color: {p.text};
                  border: 1px solid {p.border}; border-radius: 6px; padding: 4px 6px;
                  selection-background-color: {p.accent}; selection-color: {p.accent_text}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {p.accent}; }}
    QGroupBox {{ border: 1px solid {p.border}; border-radius: 8px; margin-top: 12px; padding-top: 6px; background: {p.panel}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.text}; font-weight: 600; }}
    QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 6px; background: {p.panel}; top: -1px; }}
    QTabBar::tab {{ padding: 6px 14px; color: {p.muted}; border: none; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; font-weight: 600; }}
    QTabBar::tab:hover {{ color: {p.text}; }}
    QHeaderView::section {{ background: {p.header}; color: {p.text}; border: none; border-right: 1px solid {p.border};
                  border-bottom: 1px solid {p.border}; padding: 4px 6px; font-weight: 600; }}
    QTableView, QListWidget, QTableWidget {{ background: {p.base}; alternate-background-color: {p.alt}; color: {p.text};
                  border: 1px solid {p.border}; border-radius: 6px; gridline-color: {p.border};
                  selection-background-color: {p.selection}; selection-color: {p.selection_text}; }}
    QMenuBar {{ background: {p.panel}; color: {p.text}; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {p.selection}; color: {p.selection_text}; }}
    QMenu {{ background: {p.panel}; color: {p.text}; border: 1px solid {p.border}; }}
    QStatusBar {{ background: {p.panel}; color: {p.muted}; border-top: 1px solid {p.border}; }}
    QSplitter::handle {{ background: {p.window}; }}
    QLabel[muted="true"] {{ color: {p.muted}; }}
    QToolButton[chip="true"] {{ background: {p.panel}; color: {p.text}; border: 1px solid {p.border};
                  border-radius: 11px; padding: 3px 10px; }}
    QToolButton[chip="true"]:hover {{ border-color: {p.accent}; }}
    QToolButton[chip="true"]:checked {{ background: {p.accent}; color: {p.accent_text}; border-color: {p.accent};
                  font-weight: 600; }}
    QLabel[heading="true"] {{ font-size: 16pt; font-weight: 600; color: {p.text}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 5px; min-height: 24px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """


def _qpalette(p: Palette) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.window, QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.base, QPalette.ColorRole.AlternateBase: p.alt,
        QPalette.ColorRole.Text: p.text, QPalette.ColorRole.Button: p.panel,
        QPalette.ColorRole.ButtonText: p.text, QPalette.ColorRole.Highlight: p.accent,
        QPalette.ColorRole.HighlightedText: p.accent_text, QPalette.ColorRole.ToolTipBase: p.panel,
        QPalette.ColorRole.ToolTipText: p.text, QPalette.ColorRole.PlaceholderText: p.muted,
        QPalette.ColorRole.Link: p.accent,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.muted))
    return pal


def apply(app: QApplication, mode: str = "system") -> Palette:
    """Apply a theme to the whole app and re-tint the icons."""
    global _current, _mode
    from wta import icons  # icons depend on the current palette

    _mode = mode if mode in THEMES else "system"
    _current = resolve(_mode)
    app.setStyle("Fusion")
    app.setPalette(_qpalette(_current))
    app.setStyleSheet(stylesheet(_current))
    icons.retint_all()
    return _current


def follow_system(app: QApplication) -> None:
    """Switch live when Windows changes between light and dark (only while the setting is "system")."""
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: _mode == "system" and apply(app, "system"))
