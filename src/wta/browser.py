"""Embedded browser: URL bar, navigation buttons and a QWebEngineView."""

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wta.urls import normalize_url

PROFILE_NAME = "wta-browser"


def make_profile(storage_dir: Path, parent=None) -> QWebEngineProfile:
    """A dedicated, on-disk profile so the app's cookies and cache stay apart from other browsers."""
    profile = QWebEngineProfile(PROFILE_NAME, parent)
    profile.setPersistentStoragePath(str(storage_dir / "storage"))
    profile.setCachePath(str(storage_dir / "cache"))
    return profile


class BrowserPane(QWidget):
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None, profile: QWebEngineProfile | None = None) -> None:
        super().__init__(parent)

        self.view = QWebEngineView(self)
        if profile is not None:
            self.view.setPage(QWebEnginePage(profile, self.view))
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

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 6, 6, 6)
        for button in (self.back_button, self.forward_button, self.reload_button):
            toolbar.addWidget(button)
        toolbar.addWidget(self.url_bar, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
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
