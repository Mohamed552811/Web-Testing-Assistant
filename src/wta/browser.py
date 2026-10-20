"""Embedded browser: URL bar, navigation buttons and a QWebEngineView."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wta.urls import normalize_url

PROFILE_NAME = "wta-browser"
CONTROLLED_TEXT = "Claude is controlling this browser. Please don't click or type in the page until the run ends."


def make_profile(storage_dir: Path, parent=None) -> QWebEngineProfile:
    """A dedicated, on-disk profile so the app's cookies and cache stay apart from other browsers."""
    profile = QWebEngineProfile(PROFILE_NAME, parent)
    profile.setPersistentStoragePath(str(storage_dir / "storage"))
    profile.setCachePath(str(storage_dir / "cache"))
    return profile


class AppPage(QWebEnginePage):
    """A page that opens new tabs and pop-ups (window.open, target=_blank) in a pop-up window,
    so Playwright can switch to them, and reports print requests."""

    def __init__(self, profile: QWebEngineProfile, parent, pane: "BrowserPane") -> None:
        super().__init__(profile, parent)
        self.pane = pane
        # window.print() in the main frame raises printRequested (checked with Qt 6.12); printRequestedByFrame
        # covers frames inside the page.
        self.printRequested.connect(lambda: pane.print_requested.emit(self))
        self.printRequestedByFrame.connect(
            lambda frame: pane.print_requested.emit(self) if frame != self.mainFrame() else None)

    def createWindow(self, _type) -> QWebEnginePage:  # noqa: N802 (Qt name)
        return self.pane.open_popup().view.page()


class PopupWindow(QWidget):
    """A new tab or pop-up opened by the page under test. Closed when the page asks (window.close,
    or Playwright closing the tab), by its own close button, or by Close all browsers."""

    def __init__(self, pane: "BrowserPane") -> None:
        # Owned by the main window (closed with it, before the browser profile goes), but a window of its own.
        super().__init__(pane.window(), Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("New tab (opened by the page)")
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.view = QWebEngineView(self)
        self.view.setPage(AppPage(pane.profile, self.view, pane))
        self.view.urlChanged.connect(lambda url: self.address.setText(url.toString()))
        self.view.titleChanged.connect(lambda title: self.setWindowTitle(f"{title} (opened by the page)"))
        self.view.page().windowCloseRequested.connect(self.close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.address)
        layout.addWidget(self.view, 1)
        self.resize(1000, 760)


class ControlBanner(QFrame):
    """Shown over the browser while Claude runs a test, with its own Stop button."""

    stop_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("controlBanner")
        self.setStyleSheet("#controlBanner { background: #C77700; } #controlBanner QLabel { color: white; "
                           "font-weight: 600; }")
        self.icon = QLabel()
        self.message = QLabel(CONTROLLED_TEXT)
        self.message.setWordWrap(True)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop the run now: ends Claude Code and everything it started")
        self.stop_button.setStyleSheet("QPushButton { background: #da3633; color: white; font-weight: bold; "
                                       "border: 1px solid white; border-radius: 6px; padding: 3px 12px; }")
        self.stop_button.clicked.connect(self.stop_requested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.addWidget(self.icon)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.stop_button)


class BrowserPane(QWidget):
    status_message = Signal(str)
    stop_requested = Signal()
    print_requested = Signal(object)  # the QWebEnginePage that asked to print

    def __init__(self, parent: QWidget | None = None, profile: QWebEngineProfile | None = None) -> None:
        super().__init__(parent)

        self.view = QWebEngineView(self)
        self.profile = profile or QWebEngineProfile.defaultProfile()
        self.popups: list[PopupWindow] = []
        self.view.setPage(AppPage(self.profile, self.view, self))
        # The view fills the pane unless a session viewport is set; then it gets that exact
        # size and the scroll area lets it be larger than the pane.
        self.viewport_area = QScrollArea(self)
        self.viewport_area.setWidget(self.view)
        self.viewport_area.setWidgetResizable(True)
        self.viewport_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self.back_button = QToolButton(text="◀", toolTip="Back")
        self.forward_button = QToolButton(text="▶", toolTip="Forward")
        self.reload_button = QToolButton(text="⟳", toolTip="Reload")
        self.url_bar = QLineEdit(placeholderText="Enter a URL and press Enter")
        self.url_bar.setClearButtonEnabled(True)

        self.progress = QProgressBar(maximumHeight=3, textVisible=False)
        self.progress.hide()
        self.banner = ControlBanner(self)
        self.banner.stop_requested.connect(self.stop_requested)
        self.banner.hide()

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 6, 6, 6)
        for button in (self.back_button, self.forward_button, self.reload_button):
            toolbar.addWidget(button)
        toolbar.addWidget(self.url_bar, 1)
        self._toolbar = toolbar

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self.banner)
        layout.addWidget(self.progress)
        layout.addWidget(self.viewport_area, 1)

        self.back_button.clicked.connect(self.view.back)
        self.forward_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self.view.reload)
        self.url_bar.returnPressed.connect(self._navigate_from_bar)

        self.view.urlChanged.connect(self._on_url_changed)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self.progress.setValue)
        self.view.loadFinished.connect(self._on_load_finished)

    def add_toolbar_widget(self, widget: QWidget) -> None:
        """A control at the end of the address bar row (the main window adds Expand there)."""
        self._toolbar.addWidget(widget)

    def open_popup(self) -> PopupWindow:
        popup = PopupWindow(self)
        self.popups.append(popup)
        popup.destroyed.connect(lambda _obj=None, p=popup: self.popups.remove(p) if p in self.popups else None)
        popup.show()
        self.status_message.emit("The page opened a new tab; it is shown in its own window.")
        return popup

    def close_popups(self) -> int:
        """Close every new tab or pop-up the page opened. Returns how many."""
        popups = list(self.popups)
        for popup in popups:
            popup.close()
        return len(popups)

    def set_controlled(self, controlled: bool, message: str = CONTROLLED_TEXT) -> None:
        """Show or hide the "Claude is controlling this browser" banner."""
        self.banner.message.setText(message)
        self.banner.setVisible(controlled)

    def set_viewport(self, width: int, height: int) -> None:
        """Give the page exactly width x height CSS pixels (at 100% zoom)."""
        self.view.setFixedSize(width, height)
        self.viewport_area.setWidgetResizable(False)

    def clear_viewport(self) -> None:
        self.view.setMinimumSize(0, 0)
        self.view.setMaximumSize(16777215, 16777215)
        self.viewport_area.setWidgetResizable(True)

    def _navigate_from_bar(self) -> None:
        url = normalize_url(self.url_bar.text())
        if url is None:
            self.status_message.emit("Not a valid http(s) URL")
            return
        self.view.load(QUrl(url))
        self.view.setFocus()

    def _on_url_changed(self, url: QUrl) -> None:
        text = url.toString()
        self.url_bar.setText("" if text == "about:blank" else text)
        history = self.view.history()
        self.back_button.setEnabled(history.canGoBack())
        self.forward_button.setEnabled(history.canGoForward())

    def _on_load_started(self) -> None:
        self.progress.setValue(0)
        self.progress.show()

    def _on_load_finished(self, ok: bool) -> None:
        self.progress.hide()
        url = self.view.url().toString()
        self.status_message.emit(f"Loaded {url}" if ok else f"Failed to load {url}")
