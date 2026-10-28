"""Open questions of the current generation (spec 8.1 (10)): answer them, and the answer becomes a note of the
backlog for the next generation."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wta import icons, snackbar
from wta.db import Database
from wta.db import designer_content as content


class QuestionsPanel(QWidget):
    answered = Signal()  # a note was added

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.backlog_id: int | None = None
        self.questions: list[content.QuestionView] = []
        self.summary = QLabel("No open questions.")
        self.summary.setProperty("muted", True)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setAccessibleName("Open questions")
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("Your answer. It is added to the backlog's notes for the next generation.")
        self.answer.setFixedHeight(80)
        self.answer_button = QPushButton("Add answer to notes")
        icons.set_icon(self.answer_button, "check")
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.answer_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.summary)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.answer)
        layout.addLayout(row)
        self.list.currentRowChanged.connect(self._selected)
        self.answer.textChanged.connect(self._update_button)
        self.answer_button.clicked.connect(self.save_answer)
        self._update_button()

    def show_backlog(self, backlog_id: int | None) -> None:
        self.backlog_id = backlog_id
        self.reload()

    def reload(self) -> None:
        row = self.list.currentRow()
        self.questions = content.list_questions(self.db, self.backlog_id) if self.backlog_id is not None else []
        self.list.clear()
        for question in self.questions:
            text = question.text + (f"\n✔ Answered: {question.answer}" if question.answered else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, question.id)
            if question.answered:
                item.setForeground(self.palette().placeholderText())
            self.list.addItem(item)
        open_count = sum(1 for q in self.questions if not q.answered)
        self.summary.setText(f"{len(self.questions)} question(s), {open_count} not answered yet." if self.questions
                             else "No open questions.")
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)
        self._update_button()

    def open_count(self) -> int:
        return sum(1 for q in self.questions if not q.answered)

    def _selected(self, row: int) -> None:
        question = self.questions[row] if 0 <= row < len(self.questions) else None
        self.answer.setPlainText(question.answer if question else "")
        self._update_button()

    def _update_button(self) -> None:
        self.answer_button.setEnabled(self.list.currentRow() >= 0 and bool(self.answer.toPlainText().strip()))

    def save_answer(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.questions):
            return
        content.answer_question(self.db, self.questions[row].id, self.answer.toPlainText())
        self.reload()
        self.answered.emit()
        snackbar.show(self, "Answer added to the backlog's notes.")
