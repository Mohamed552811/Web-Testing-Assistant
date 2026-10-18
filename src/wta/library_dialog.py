"""The scenario library: pick saved scenarios (or smoke pages) to add to the batch; edit, delete, import."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wta.db import library
from wta.db.models import LibraryItem, MAX_SCENARIOS_PER_BATCH, ScenarioKind
from wta.scenario_dialogs import ScenarioDialog, SmokePageDialog


class LibraryDialog(QDialog):
    """`chosen_ids()` after OK: the items to add to the batch, in the order shown."""

    def __init__(self, db, kind: ScenarioKind, room: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db, self.kind, self.room = db, kind, room
        self.smoke = kind == ScenarioKind.SMOKE
        noun = "smoke pages" if self.smoke else "scenarios"
        self.setWindowTitle(f"Library — {noun}")
        self.resize(720, 520)
        self.search = QLineEdit(placeholderText=f"Search saved {noun}")
        self.search.setClearButtonEnabled(True)
        self.items = QListWidget()
        self.items.setAlternatingRowColors(True)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setProperty("muted", True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.edit_button = QPushButton("Edit…")
        self.delete_button = QPushButton("Delete")
        self.import_button = QPushButton("Import test cases…")
        self.import_button.setVisible(not self.smoke)
        self.room_label = QLabel()
        side = QVBoxLayout()
        for button in (self.edit_button, self.delete_button, self.import_button):
            side.addWidget(button)
        side.addStretch(1)
        middle = QHBoxLayout()
        middle.addWidget(self.items, 1)
        middle.addLayout(side)
        self.buttons = QDialogButtonBox()
        self.add_button = self.buttons.addButton("Add to batch", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addLayout(middle, 1)
        layout.addWidget(self.preview)
        layout.addWidget(self.room_label)
        layout.addWidget(self.buttons)

        self.search.textChanged.connect(lambda _text: self.reload())
        self.items.itemChanged.connect(lambda _item: self._update())
        self.items.currentItemChanged.connect(lambda *_: self._update())
        self.items.itemDoubleClicked.connect(lambda _item: self.edit())
        self.edit_button.clicked.connect(self.edit)
        self.delete_button.clicked.connect(self.delete)
        self.import_button.clicked.connect(self.import_cases)
        self.reload()

    def reload(self, select_ids: set[int] | None = None) -> None:
        checked = set(self.chosen_ids()) | (select_ids or set())
        self.items.blockSignals(True)
        self.items.clear()
        for row in library.list_items(self.db, self.kind, self.search.text()):
            text = f"{row.name} — {row.url}" if self.smoke else row.name
            item = QListWidgetItem(text + (f"   ({row.source})" if row.source else ""))
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.id in checked else Qt.CheckState.Unchecked)
            self.items.addItem(item)
        self.items.blockSignals(False)
        if self.items.count() and self.items.currentRow() < 0:
            self.items.setCurrentRow(0)
        self._update()

    def _current(self) -> LibraryItem | None:
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def chosen_ids(self) -> list[int]:
        return [self.items.item(i).data(Qt.ItemDataRole.UserRole).id for i in range(self.items.count())
                if self.items.item(i).checkState() == Qt.CheckState.Checked]

    def _update(self) -> None:
        current = self._current()
        self.edit_button.setEnabled(current is not None)
        self.delete_button.setEnabled(current is not None)
        if current is None:
            self.preview.setText("The library is empty. Use “Save to library” on a scenario, or import test cases."
                                 if not self.search.text() else "Nothing matches.")
        elif self.smoke:
            self.preview.setText(f"{current.url}\n{current.page_information or ''}".strip())
        else:
            self.preview.setText(f"Steps:\n{current.test_steps or '—'}\nExpected result: {current.expected_result or '—'}")
        chosen = len(self.chosen_ids())
        self.room_label.setText(f"{chosen} chosen; this batch has room for {self.room} more "
                                f"(at most {MAX_SCENARIOS_PER_BATCH} per batch).")
        self.add_button.setEnabled(0 < chosen <= self.room)

    def edit(self) -> None:
        current = self._current()
        if current is None:
            return
        data = library.item_input(current)
        dialog = SmokePageDialog(self, data.page) if self.smoke else ScenarioDialog(self, data.scenario)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = library.ItemInput(page=dialog.data(), source=current.source) if self.smoke \
            else library.ItemInput(scenario=dialog.data(), source=current.source)
        library.update_item(self.db, current.id, new)
        self.reload()

    def delete(self) -> None:
        current = self._current()
        if current is None:
            return
        answer = QMessageBox.question(self, "Delete from library",
                                      f"Delete “{current.name}” from the library? Scenarios already added stay.")
        if answer == QMessageBox.StandardButton.Yes:
            library.delete_item(self.db, current.id)
            self.reload()

    def import_cases(self) -> None:
        from wta.import_dialog import import_into_library  # only needed here
        added = import_into_library(self.db, self)
        if added:
            self.reload({row.id for row in added})
