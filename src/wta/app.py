"""Application entry point and main window."""

import sys

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QMainWindow

from wta import __version__
from wta.browser import BrowserPane

START_PAGE = "about:blank"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Web Testing Assistant {__version__}")
        self.resize(1280, 860)

        self.browser = BrowserPane(self)
        self.setCentralWidget(self.browser)
        self.browser.view.load(QUrl(START_PAGE))
        self.browser.status_message.connect(self.statusBar().showMessage)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Web Testing Assistant")
    window = MainWindow()
    window.show()
    return app.exec()
