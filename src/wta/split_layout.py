"""The browser / work split of Regression and Smoke Testing: drag to resize, swap the sides, place them side by
side or stacked, and expand either side to fill the tab until it is restored.

The layout is not remembered: every start (and Reset) shows both sides, side by side, half each.
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QSplitter, QToolButton, QWidget

from wta import icons

BROWSER, WORK = "browser", "work"


class SplitLayout(QObject):
    changed = Signal()  # after any layout change; buttons update their icons and tooltips

    def __init__(self, splitter: QSplitter, browser_side: QWidget, work_side: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.splitter = splitter
        self.sides = {BROWSER: browser_side, WORK: work_side}
        self.expanded: str | None = None
        self._sizes_before: list[int] = []
        self._halve_when_shown = True  # sizes set before the splitter is on screen are not kept
        splitter.installEventFilter(self)
        for index in range(splitter.count()):
            splitter.setCollapsible(index, False)  # dragging never hides a side; Expand does that on purpose
            splitter.setStretchFactor(index, 1)
        self.reset()

    # --- state ------------------------------------------------------------------------------------

    def order(self) -> list[str]:
        """The sides from left (or top) to right (or bottom)."""
        return [BROWSER if self.splitter.widget(i) is self.sides[BROWSER] else WORK for i in range(2)]

    def stacked(self) -> bool:
        return self.splitter.orientation() == Qt.Orientation.Vertical

    # --- actions -----------------------------------------------------------------------------------

    def reset(self) -> None:
        """Browser left, work right, half each, nothing expanded."""
        if self.expanded:
            self.restore()
        if self.splitter.widget(0) is not self.sides[BROWSER]:
            self.splitter.insertWidget(0, self.sides[BROWSER])
        self.splitter.setOrientation(Qt.Orientation.Horizontal)
        self.halve()

    def halve(self) -> None:
        if self.splitter.isVisible():
            self._set_halves()
        else:
            self._halve_when_shown = True
        self.changed.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt name)
        # A hidden page is still resized, so halve on whichever comes last: the page shown, or its resize.
        if watched is self.splitter and self._halve_when_shown                 and event.type() in (QEvent.Type.Show, QEvent.Type.Resize) and self.splitter.isVisible():
            self._halve_when_shown = False
            self._set_halves()
        return False

    def _set_halves(self) -> None:
        total = sum(self.splitter.sizes()) or (self.splitter.width() if not self.stacked() else self.splitter.height())
        self.splitter.setSizes([total // 2, total - total // 2])

    def swap(self) -> None:
        sizes = self.splitter.sizes()
        self.splitter.insertWidget(0, self.splitter.widget(1))
        self.splitter.setSizes(list(reversed(sizes)))
        self.changed.emit()

    def toggle_stacked(self) -> None:
        self.splitter.setOrientation(Qt.Orientation.Horizontal if self.stacked() else Qt.Orientation.Vertical)
        self.halve()

    def toggle_expanded(self, side: str) -> None:
        if self.expanded == side:
            self.restore()
        else:
            self.expand(side)

    def expand(self, side: str) -> None:
        """Let `side` fill the whole tab; the other side is hidden until restore()."""
        if self.expanded is None:
            self._sizes_before = self.splitter.sizes()
        other = WORK if side == BROWSER else BROWSER
        self.sides[side].show()
        self.sides[other].hide()
        self.expanded = side
        self.changed.emit()

    def restore(self) -> None:
        for widget in self.sides.values():
            widget.show()
        self.expanded = None
        if sum(self._sizes_before):
            self.splitter.setSizes(self._sizes_before)
        else:
            self._set_halves()
        self.changed.emit()


class ExpandButton(QToolButton):
    """Expand / restore one side; the icon and tooltip follow the layout."""

    def __init__(self, layout: SplitLayout, side: str, parent=None) -> None:
        super().__init__(parent)
        self.layout_ = layout
        self.side = side
        self.setAutoRaise(True)
        self.clicked.connect(lambda: layout.toggle_expanded(side))
        layout.changed.connect(self.update_look)
        self.update_look()

    def update_look(self) -> None:
        name = "the browser" if self.side == BROWSER else "this panel"
        expanded = self.layout_.expanded == self.side
        icons.set_icon(self, "minimize-2" if expanded else "maximize-2")
        self.setToolTip("Restore both sides" if expanded else f"Expand {name} to fill the tab")
        self.setAccessibleName(self.toolTip())


def layout_buttons(layout: SplitLayout, parent=None) -> list[QToolButton]:
    """Swap sides, side by side / stacked, reset, and expand-this-panel, for the work side's tab bar."""
    swap = QToolButton(parent, autoRaise=True, toolTip="Swap the browser and this panel")
    icons.set_icon(swap, "arrow-left-right")
    swap.clicked.connect(layout.swap)
    stack = QToolButton(parent, autoRaise=True)
    stack.clicked.connect(layout.toggle_stacked)
    reset = QToolButton(parent, autoRaise=True, toolTip="Reset the layout: side by side, half each")
    icons.set_icon(reset, "rotate-ccw")
    reset.clicked.connect(layout.reset)

    def update() -> None:
        stacked = layout.stacked()
        icons.set_icon(stack, "columns-2" if stacked else "rows-2")
        stack.setToolTip("Place side by side" if stacked else "Stack the browser and this panel")
        for button in (swap, stack):
            button.setEnabled(layout.expanded is None)

    layout.changed.connect(update)
    update()
    for button in (swap, stack, reset):
        button.setAccessibleName(button.toolTip())
    return [swap, stack, reset, ExpandButton(layout, WORK, parent)]
