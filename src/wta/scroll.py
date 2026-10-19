"""Scroll areas that let a panel shrink below its contents' minimum size."""

from PySide6.QtWidgets import QFrame, QScrollArea, QWidget


def scrollable(widget: QWidget) -> QScrollArea:
    """Wrap widget so a splitter can make it smaller than it wants; a scrollbar appears instead."""
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    return area
