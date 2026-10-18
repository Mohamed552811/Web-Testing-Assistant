"""The one QApplication the tests share, shut down cleanly when the test process exits.

Tests create windows and web views and only schedule them for deletion (deleteLater). Without
this, those objects are still alive when Python tears down the QApplication at exit, and Qt
WebEngine then crashes the process (a segmentation fault after all tests have passed).
"""

import atexit
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])


@atexit.register
def _shut_down() -> None:
    """Close and delete every window and pending object while the application still exists."""
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    for _ in range(3):  # deleting a window can schedule more deletions (its pages, then their views)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
