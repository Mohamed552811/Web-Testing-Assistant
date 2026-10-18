"""Ask Claude to suggest scenarios from a user story or the page in the browser; pick, edit and add them."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from wta import suggest
from wta.db.scenarios import ScenarioInput
from wta.scenario_dialogs import ScenarioDialog


class SuggestDialog(QDialog):
    """After OK: `chosen()` are the scenarios to add, and `to_library` says whether to also save them."""

    def __init__(self, work_dir: Path, usage_path: Path, model: str, room: int,
                 page_text: Callable[[Callable[[str, str], None]], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggest scenarios")
        self.resize(780, 620)
        self.work_dir, self.usage_path, self.model, self.room = work_dir, usage_path, model, room
        self.page_text = page_text  # asks the browser for (text, url) of the open page
        self.run: suggest.SuggestRun | None = None
        self.to_library = False

        self.from_story = QRadioButton("From a user story or requirement")
        self.from_page = QRadioButton("From the page open in the browser")
        self.from_story.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.from_story)
        group.addButton(self.from_page)
        self.story = QPlainTextEdit(placeholderText="Paste the user story, acceptance criteria or a description")
        self.suggest_button = QPushButton("Suggest")
        self.suggest_button.setProperty("primary", True)
        self.status = QLabel("Uses your Claude plan (one short request, no browser). The text is only read, "
                             "never followed as instructions.")
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.itemDoubleClicked.connect(self.edit)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(lambda: self.edit(self.results.currentItem()))
        self.buttons = QDialogButtonBox()
        self.add_button = self.buttons.addButton("Add to batch", QDialogButtonBox.ButtonRole.AcceptRole)
        self.add_library_button = self.buttons.addButton("Add to batch and library",
                                                         QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.add_library_button.clicked.connect(lambda: setattr(self, "to_library", True))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        source = QHBoxLayout()
        source.addWidget(self.from_story)
        source.addWidget(self.from_page)
        source.addStretch(1)
        source.addWidget(self.suggest_button)
        result_row = QHBoxLayout()
        result_row.addWidget(self.results, 1)
        side = QVBoxLayout()
        side.addWidget(self.edit_button)
        side.addStretch(1)
        result_row.addLayout(side)
        layout = QVBoxLayout(self)
        layout.addLayout(source)
        layout.addWidget(self.story, 1)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Suggestions (tick the ones to add; double-click to edit)"))
        layout.addLayout(result_row, 2)
        layout.addWidget(self.buttons)

        self.from_page.toggled.connect(lambda on: self.story.setEnabled(not on))
        self.suggest_button.clicked.connect(self.start)
        self.results.itemChanged.connect(lambda _item: self._update())
        self._update()

    def start(self) -> None:
        if self.from_page.isChecked():
            self.status.setText("Reading the page…")
            self.page_text(lambda text, url: self._ask(text, suggest.PAGE, url))
        else:
            self._ask(self.story.toPlainText(), suggest.STORY)

    def _ask(self, text: str, kind: str, url: str = "") -> None:
        if not text.strip():
            self.status.setText("Paste a user story first." if kind == suggest.STORY else "The page has no text.")
            return
        self.suggest_button.setEnabled(False)
        self.status.setText("Claude is thinking… (usually under a minute)")
        self.run = suggest.SuggestRun(self.work_dir, self.usage_path, self.model, self)
        self.run.finished.connect(self._show)
        self.run.start(suggest.build_prompt(text, kind, url))

    def _show(self, scenarios: list, error: str) -> None:
        self.suggest_button.setEnabled(True)
        if error:
            self.status.setText(f"No suggestions: {error}")
            return
        self.status.setText(f"{len(scenarios)} suggestions. Tick the ones you want, edit them, then add them.")
        self.results.blockSignals(True)
        self.results.clear()
        for scenario in scenarios:
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._set(item, scenario)
            self.results.addItem(item)
        self.results.blockSignals(False)
        self._update()

    @staticmethod
    def _set(item: QListWidgetItem, scenario: ScenarioInput) -> None:
        item.setData(Qt.ItemDataRole.UserRole, scenario)
        item.setText(f"{scenario.name}\n{scenario.steps}\nExpected: {scenario.expected_result or '—'}")

    def edit(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        dialog = ScenarioDialog(self, item.data(Qt.ItemDataRole.UserRole))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set(item, dialog.data())
            item.setCheckState(Qt.CheckState.Checked)

    def chosen(self) -> list[ScenarioInput]:
        return [self.results.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.results.count())
                if self.results.item(i).checkState() == Qt.CheckState.Checked]

    def _update(self) -> None:
        count = len(self.chosen())
        ok = 0 < count <= self.room
        self.add_button.setEnabled(ok)
        self.add_library_button.setEnabled(ok)
        self.edit_button.setEnabled(self.results.count() > 0)
        if count > self.room:
            self.status.setText(f"{count} ticked, but this batch has room for {self.room} more.")

    def reject(self) -> None:
        if self.run is not None:
            self.run.cancel()
        super().reject()
