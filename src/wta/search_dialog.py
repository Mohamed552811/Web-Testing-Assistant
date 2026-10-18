"""Search across all sessions; double-click a result to open it."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wta.db import search
from wta.db.database import Database


class SearchDialog(QDialog):
    hit_chosen = Signal(object)  # search.Hit

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Search everything")
        self.resize(980, 560)
        self.query = QLineEdit(placeholderText="Search scenarios, steps, bugs, site notes and the library "
                                               "in all sessions")
        self.query.setClearButtonEnabled(True)
        self.count = QLabel()
        self.count.setProperty("muted", True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["What", "Session", "Where", "Found"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout = QVBoxLayout(self)
        layout.addWidget(self.query)
        layout.addWidget(self.count)
        layout.addWidget(self.table, 1)
        hint = QLabel("Double-click a result to open it.")
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        self._delay = QTimer(self, singleShot=True, interval=250)  # search while typing, not on every key
        self._delay.timeout.connect(self.run_search)
        self.query.textChanged.connect(lambda _t: self._delay.start())
        self.query.returnPressed.connect(self.run_search)
        self.table.doubleClicked.connect(lambda _index: self.open_current())
        self.hits: list[search.Hit] = []
        self.run_search()

    def run_search(self) -> None:
        self.hits = search.search(self.db, self.query.text())
        self.table.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            for column, text in enumerate((hit.kind, hit.session_name or ("—" if not hit.site else hit.site),
                                           hit.title, hit.snippet)):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 260)
        text = self.query.text().strip()
        self.count.setText("" if len(text) < 2 else
                           f"{len(self.hits)} result(s)" + (" (showing the first ones)"
                                                            if len(self.hits) >= search.MAX_HITS else ""))

    def open_current(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.hits):
            self.hit_chosen.emit(self.hits[row])
            if self.hits[row].session_id is not None or self.hits[row].site:
                self.accept()
