"""The first screen: choose a mode (Test Case Design, Regression Testing, Smoke Testing), like Cypress's Launchpad.

Also holds the mode names shared by the Launchpad, the mode switcher and the remembered last mode.
"""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from wta import __version__, icons

DESIGN = "design"
REGRESSION = "regression"
SMOKE = "smoke"
MODES = {DESIGN: "Test Case Design", REGRESSION: "Regression Testing", SMOKE: "Smoke Testing"}
DESCRIPTIONS = {
    DESIGN: "Turn product backlogs from an Excel file into happy-path and corner-case test cases.",
    REGRESSION: "Run full manual regression checklists and record results.",
    SMOKE: "Run a quick manual checklist of the core journeys before a release.",
}
MODE_ICONS = {DESIGN: "file-text", REGRESSION: "rotate-ccw", SMOKE: "circle-check"}
STACK_BELOW = 760  # narrower than this, the cards stack vertically


class ModeCard(QPushButton):
    """A large, focusable card button: icon, title, one-sentence description and a status badge."""

    def __init__(self, mode: str, parent=None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setProperty("card", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(190)
        self.setAccessibleName(MODES[mode])
        self.setAccessibleDescription(DESCRIPTIONS[mode])

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.themed_icon(MODE_ICONS[mode], "accent").pixmap(40, 40))
        self.title_label = QLabel(MODES[mode])
        self.title_label.setProperty("cardTitle", True)
        self.description_label = QLabel(DESCRIPTIONS[mode])
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.description_label.setProperty("muted", True)
        self.badge = QLabel()
        self.badge.setProperty("badge", "none")
        self.badge.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label, 1)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignLeft)
        for label in (self.icon_label, self.title_label, self.description_label, self.badge):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_badge(self, text: str, kind: str = "ok") -> None:
        """kind: "ok" (green), "warn" (amber) or "info" (neutral); empty text hides the badge."""
        self.badge.setText(text)
        self.badge.setProperty("badge", kind)
        # Re-polishing keeps the old size (no padding); a changed style sheet makes Qt recompute it.
        self.badge.setStyleSheet(f"/* {kind} */")
        self.badge.setVisible(bool(text))
        self.setAccessibleDescription(f"{DESCRIPTIONS[self.mode]} {text}".strip())

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if event.type() == QEvent.Type.PaletteChange:  # theme switched: re-tint the icon
            self.icon_label.setPixmap(icons.themed_icon(MODE_ICONS[self.mode], "accent").pixmap(40, 40))
        super().changeEvent(event)


class Launchpad(QWidget):
    mode_chosen = Signal(str)
    continue_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(72))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Web Testing Assistant", alignment=Qt.AlignmentFlag.AlignCenter)
        title.setProperty("heading", True)
        welcome = QLabel("Welcome. What do you want to do today?", alignment=Qt.AlignmentFlag.AlignCenter)
        welcome.setProperty("muted", True)

        self.cards = {mode: ModeCard(mode, self) for mode in MODES}
        self.cards_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cards_layout.setSpacing(16)
        for card in self.cards.values():
            self.cards_layout.addWidget(card)
            card.clicked.connect(lambda _checked=False, m=card.mode: self.mode_chosen.emit(m))

        self.continue_button = QPushButton()
        self.continue_button.setProperty("primary", True)
        icons.set_icon(self.continue_button, "play", "on-accent")
        self.continue_button.clicked.connect(self.continue_requested)
        self.continue_button.hide()

        column = QVBoxLayout(self)
        column.setContentsMargins(32, 24, 32, 16)
        column.addStretch(2)
        column.addWidget(logo)
        column.addWidget(title)
        column.addWidget(welcome)
        column.addSpacing(24)
        column.addLayout(self.cards_layout)
        column.addSpacing(20)
        column.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(3)
        version = QLabel(f"Version {__version__}", alignment=Qt.AlignmentFlag.AlignCenter)
        version.setProperty("muted", True)
        column.addWidget(version)
        self.setTabOrder(self.cards[DESIGN], self.cards[REGRESSION])
        self.setTabOrder(self.cards[REGRESSION], self.cards[SMOKE])
        self.setTabOrder(self.cards[SMOKE], self.continue_button)

    def set_last_mode(self, mode: str | None) -> None:
        """Offer "Continue where you left off" for the mode used last time."""
        if mode in MODES:
            self.continue_button.setText(f"Continue where you left off: {MODES[mode]}")
            self.continue_button.show()
        else:
            self.continue_button.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        stacked = event.size().width() < STACK_BELOW
        self.cards_layout.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        for card in self.cards.values():
            card.setMinimumHeight(120 if stacked else 190)
        super().resizeEvent(event)
