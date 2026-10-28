"""Snackbar messages: a short confirmation in the bottom-right corner of the window after a change is saved.

    snackbar.show(widget, "Answer added to the backlog's notes.")

The message goes to the main window that `widget` belongs to (or that opened its dialog), stays a few seconds and
fades out; hovering keeps it, clicking closes it. A new message replaces the one on screen. It never takes focus,
so typing and keyboard review shortcuts carry on.
"""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from wta import icons, theme

SUCCESS, INFO, WARNING = "success", "info", "warning"
SHOW_MS = 3500
MARGIN = 18  # from the window's bottom-right corner (above the status bar)
MAX_WIDTH = 460
_ICONS = {SUCCESS: "circle-check", INFO: "info", WARNING: "circle-x"}


def colors(kind: str) -> tuple[str, str, str]:
    """Background, text and accent. Dark on the light theme and light on the dark one, so it stands out."""
    dark_theme = theme.current() is theme.DARK
    background, text = ("#e6e8eb", "#16181b") if dark_theme else ("#22302e", "#ffffff")
    accent = {SUCCESS: "#0F766E" if dark_theme else "#2BB3A3", INFO: "#3B82C4", WARNING: "#d29922"}[kind]
    return background, text, accent


class Snackbar(QWidget):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window_ = window
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)  # paint the rounded background of the style sheet
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("snackbar")
        self.icon = QLabel()
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(MAX_WIDTH - 60)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 16, 10)
        row.setSpacing(10)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.text, 1)
        self.kind = SUCCESS
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(220)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._fade_finished)
        self.timer = QTimer(self, singleShot=True, interval=SHOW_MS)
        self.timer.timeout.connect(self.dismiss)
        window.installEventFilter(self)  # follow the window's size
        self.hide()

    def show_message(self, text: str, kind: str = SUCCESS) -> None:
        self.kind = kind
        background, foreground, accent = colors(kind)
        self.setStyleSheet(f"#snackbar {{ background: {background}; border-radius: 8px; border-left: 4px solid {accent}; }}"
                           f"#snackbar QLabel {{ color: {foreground}; background: transparent; }}")
        self.icon.setPixmap(icons.icon(_ICONS[kind], accent).pixmap(18, 18))
        self.text.setText(text)
        # One line when it fits; only a long message wraps (a word-wrapped label would wrap even short ones).
        width = self.text.fontMetrics().horizontalAdvance(text) + 6
        self.text.setWordWrap(width > MAX_WIDTH - 60)
        self.text.setMinimumWidth(min(width, MAX_WIDTH - 60))
        self.setAccessibleName(text)
        self.adjustSize()
        self._place()
        self.fade.stop()
        self.effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self._animate(1.0)
        self.timer.start()

    def dismiss(self) -> None:
        if self.isVisible():
            self._animate(0.0)

    def _animate(self, target: float) -> None:
        self.fade.stop()
        self.fade.setStartValue(self.effect.opacity())
        self.fade.setEndValue(target)
        self.fade.start()

    def _fade_finished(self) -> None:
        if self.effect.opacity() == 0.0:
            self.hide()

    def _place(self) -> None:
        bottom = self.window_.height() - MARGIN
        status = self.window_.statusBar()
        if status.isVisible():
            bottom -= status.height()
        self.move(self.window_.width() - self.width() - MARGIN, bottom - self.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt name)
        if watched is self.window_ and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt name): hovering keeps it
        self.timer.stop()
        self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt name)
        self.timer.start(SHOW_MS // 2)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt name): click to close
        self.dismiss()


def _main_window(widget: QWidget | None) -> QMainWindow | None:
    """The main window a widget belongs to, also from inside a dialog it opened."""
    while widget is not None:
        if isinstance(widget, QMainWindow):
            return widget
        widget = widget.parentWidget()
    active = QApplication.activeWindow()
    return active if isinstance(active, QMainWindow) else None


def show(widget: QWidget | None, text: str, kind: str = SUCCESS) -> Snackbar | None:
    window = _main_window(widget)
    if window is None:
        return None
    bar = window.findChild(Snackbar)
    if bar is None:
        bar = Snackbar(window)
    bar.show_message(text, kind)
    return bar
