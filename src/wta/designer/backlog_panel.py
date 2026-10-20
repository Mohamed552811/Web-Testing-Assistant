"""The chosen backlog: its source text, pictures (attach, detach, reorder, view), notes and supplementary context
(spec 8.1 (2)-(4)). Everything here is what will be sent to the model."""

from html import escape

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from wta import icons
from wta.db import Database
from wta.db import designer_content as content
from wta.db import workbooks as wb
from wta.designer.search import content_problem

THUMBNAIL = QSize(168, 105)


def pixmap_for(db: Database, md5: str) -> QPixmap:
    pixmap = QPixmap()
    try:
        pixmap.loadFromData(wb.image_data(db, md5))
    except LookupError:
        pass
    return pixmap


class ImageViewer(QDialog):
    """A picture at full size, scrollable."""

    def __init__(self, pixmap: QPixmap, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        label = QLabel()
        label.setPixmap(pixmap)
        area = QScrollArea()
        area.setWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(area)
        layout.addWidget(buttons)
        self.resize(min(pixmap.width() + 40, 1400), min(pixmap.height() + 80, 900))


def _list_box(title: str, list_widget: QListWidget, *buttons: QPushButton) -> QGroupBox:
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    layout.addWidget(list_widget)
    layout.addLayout(row)
    return box


class BacklogPanel(QWidget):
    changed = Signal()  # notes, context or pictures changed (what will be sent)
    generate_requested = Signal()
    history_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.backlog: wb.BacklogView | None = None
        self.workbook_id: int | None = None

        self.heading = QLabel("Choose a backlog on the left.")
        self.heading.setProperty("cardTitle", True)
        self.heading.setWordWrap(True)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("badge", "warn")
        self.notice.hide()
        self.description = QTextBrowser()
        self.description.setAccessibleName("Backlog description")
        self.description.setMaximumHeight(130)

        self.pictures = QListWidget()
        self.pictures.setViewMode(QListView.ViewMode.IconMode)
        self.pictures.setIconSize(THUMBNAIL)
        self.pictures.setMovement(QListView.Movement.Static)
        self.pictures.setFlow(QListView.Flow.LeftToRight)
        self.pictures.setWrapping(False)
        self.pictures.setFixedHeight(THUMBNAIL.height() + 60)
        self.pictures.setAccessibleName("Pictures sent with this backlog")
        self.attach_button = QPushButton("Attach…")
        self.detach_button = QPushButton("Detach")
        self.left_button = QPushButton("Move left")
        self.right_button = QPushButton("Move right")
        self.view_button = QPushButton("View")
        icons.set_icon(self.attach_button, "plus")
        icons.set_icon(self.detach_button, "trash-2")
        icons.set_icon(self.left_button, "arrow-left")
        icons.set_icon(self.right_button, "arrow-right")
        icons.set_icon(self.view_button, "eye")

        self.notes = QListWidget()
        self.notes.setWordWrap(True)
        self.notes.setAccessibleName("Notes")
        self.notes.setMaximumHeight(150)
        self.add_note_button = QPushButton("Add note…")
        self.edit_note_button = QPushButton("Edit…")
        self.remove_note_button = QPushButton("Remove")
        icons.set_icon(self.add_note_button, "plus")
        icons.set_icon(self.edit_note_button, "pencil")
        icons.set_icon(self.remove_note_button, "trash-2")

        self.context = QListWidget()
        self.context.setWordWrap(True)
        self.context.setAccessibleName("Supplementary context")
        self.context.setMaximumHeight(150)
        self.add_backlog_button = QPushButton("Add from backlog…")
        self.paste_button = QPushButton("Paste text…")
        self.remove_context_button = QPushButton("Remove")
        icons.set_icon(self.add_backlog_button, "folder-open")
        icons.set_icon(self.paste_button, "file-text")
        icons.set_icon(self.remove_context_button, "trash-2")

        self.generate_button = QPushButton("Generate test cases")
        self.generate_button.setProperty("primary", True)
        icons.set_icon(self.generate_button, "play", "on-accent")
        self.cancel_button = QPushButton("Cancel")
        icons.set_icon(self.cancel_button, "square")
        self.cancel_button.hide()
        self.progress = QLabel()
        self.progress.setProperty("muted", True)
        self.guidance = QLineEdit(placeholderText="Guidance for this generation (optional), e.g. focus on permissions")
        self.guidance.setAccessibleName("Guidance for this generation")
        self.keep_locked = QCheckBox("Keep locked test cases")
        self.keep_locked.setToolTip("Locked test cases are sent as test cases to keep unchanged; Claude only adds "
                                    "what is missing.")
        self.keep_locked.setChecked(True)
        self.keep_locked.hide()
        self.history_button = QPushButton("History…")
        icons.set_icon(self.history_button, "timer")
        self.history_button.setEnabled(False)
        guidance_row = QHBoxLayout()
        guidance_row.addWidget(self.guidance, 1)
        guidance_row.addWidget(self.keep_locked)
        generate_row = QHBoxLayout()
        generate_row.addWidget(self.generate_button)
        generate_row.addWidget(self.cancel_button)
        generate_row.addWidget(self.progress, 1)
        generate_row.addWidget(self.history_button)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.heading)
        layout.addWidget(self.notice)
        layout.addWidget(self.description)
        layout.addWidget(_list_box("Pictures (sent to Claude in this order)", self.pictures, self.attach_button,
                                   self.detach_button, self.left_button, self.right_button, self.view_button))
        layout.addWidget(_list_box("Notes (definitions, clarifications, answers)", self.notes,
                                   self.add_note_button, self.edit_note_button, self.remove_note_button))
        layout.addWidget(_list_box("Supplementary context (from other backlogs or files)", self.context,
                                   self.add_backlog_button, self.paste_button, self.remove_context_button))
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addLayout(guidance_row)
        outer.addLayout(generate_row)

        self.attach_button.clicked.connect(self.attach_picture)
        self.detach_button.clicked.connect(self.detach_picture)
        self.left_button.clicked.connect(lambda: self.move_picture(-1))
        self.right_button.clicked.connect(lambda: self.move_picture(1))
        self.view_button.clicked.connect(self.view_picture)
        self.pictures.itemActivated.connect(lambda _item: self.view_picture())
        self.add_note_button.clicked.connect(self.add_note)
        self.edit_note_button.clicked.connect(self.edit_note)
        self.notes.itemActivated.connect(lambda _item: self.edit_note())
        self.remove_note_button.clicked.connect(lambda: self._remove(self.notes))
        self.add_backlog_button.clicked.connect(self.add_from_backlog)
        self.paste_button.clicked.connect(self.paste_context)
        self.remove_context_button.clicked.connect(lambda: self._remove(self.context))
        self.generate_button.clicked.connect(self.generate_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.history_button.clicked.connect(self.history_requested)
        self.pictures.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.notes.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.context.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.show_backlog(None)

    # --- showing ----------------------------------------------------------------------------------

    def show_backlog(self, backlog_id: int | None, workbook_id: int | None = None, sheet_name: str = "") -> None:
        self.workbook_id = workbook_id if workbook_id is not None else self.workbook_id
        self.backlog = wb.load_backlog(self.db, backlog_id) if backlog_id is not None else None
        b = self.backlog
        if b is None:
            self.heading.setText("Choose a backlog on the left.")
            self.description.clear()
            self.notice.hide()
        else:
            self.heading.setText(f"#{b.number}  {b.title}")
            where = f"Sheet “{sheet_name}”, rows {b.first_row}–{b.last_row}" if sheet_name else \
                f"Rows {b.first_row}–{b.last_row}"
            lines = "".join(f"<p><b>{escape(cell)}</b> {escape(text)}</p>" for cell, text in b.text_blocks)
            self.description.setHtml(f"<p style='color:gray'>{escape(where)}</p>{lines or '<p><i>No text.</i></p>'}")
        self.refresh()

    def refresh(self) -> None:
        """Reload pictures, notes and context of the shown backlog."""
        if self.backlog is not None:
            self.backlog = wb.load_backlog(self.db, self.backlog.id)
        self.pictures.clear()
        self.notes.clear()
        self.context.clear()
        b = self.backlog
        if b is not None:
            for image in b.images:
                item = QListWidgetItem(QIcon(pixmap_for(self.db, image.md5).scaled(
                    THUMBNAIL, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)),
                    f"{image.label} · {image.anchor_cell}" + (" (attached by hand)" if image.origin == "manual" else ""))
                item.setData(Qt.ItemDataRole.UserRole, image.id)
                item.setToolTip(f"{image.label}: {image.width}×{image.height}, sheet “{image.source_sheet}” "
                                f"{image.anchor_cell}")
                self.pictures.addItem(item)
            for note in content.list_context(self.db, b.id):
                target = self.notes if note.kind == content.DOMAIN_NOTE else self.context
                text = note.text if not note.source or note.kind == content.DOMAIN_NOTE else f"[{note.source}] {note.text}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, note.id)
                target.addItem(item)
        self._update_notice()
        self._update_buttons()

    def _update_notice(self) -> None:
        b = self.backlog
        messages = []
        if b is not None:
            if b.title_missing:
                messages.append("This backlog has no title in the file; its first sentence is used.")
            problem = content_problem(b, self.notes.count() > 0)
            if problem:
                messages.append(problem)
            if b.unfinished:
                messages.append("The text is unfinished (ends with “?”); the gap will go into the open questions.")
            if b.occurrence > 1:
                messages.append(f"Backlog {b.number} appears more than once on this sheet; this is occurrence "
                                f"{b.occurrence}.")
        self.notice.setText(" ".join(messages))
        self.notice.setVisible(bool(messages))

    def _update_buttons(self) -> None:
        has = self.backlog is not None
        picture = self.pictures.currentRow()
        for button in (self.attach_button, self.add_note_button, self.add_backlog_button, self.paste_button):
            button.setEnabled(has)
        self.detach_button.setEnabled(has and picture >= 0)
        self.view_button.setEnabled(has and picture >= 0)
        self.left_button.setEnabled(has and picture > 0)
        self.right_button.setEnabled(has and 0 <= picture < self.pictures.count() - 1)
        self.edit_note_button.setEnabled(has and self.notes.currentRow() >= 0)
        self.remove_note_button.setEnabled(has and self.notes.currentRow() >= 0)
        self.remove_context_button.setEnabled(has and self.context.currentRow() >= 0)
        blocked = has and not self.backlog.text_blocks and not self.backlog.images and self.notes.count() == 0
        self.generate_button.setEnabled(has and not blocked and not self.cancel_button.isVisible())

    def set_generation_state(self, has_run: bool, run_count: int, locked_count: int) -> None:
        """After the first generation the button regenerates; locked cases can be kept (spec 8.1 (6))."""
        self.generate_button.setText("Regenerate test cases" if has_run else "Generate test cases")
        self.keep_locked.setText(f"Keep locked test cases ({locked_count})")
        self.keep_locked.setVisible(locked_count > 0)
        self.history_button.setEnabled(run_count > 0)
        self.history_button.setText(f"History ({run_count})…" if run_count else "History…")

    def set_running(self, running: bool, message: str = "") -> None:
        self.cancel_button.setVisible(running)
        self.progress.setText(message)
        self._update_buttons()

    # --- pictures --------------------------------------------------------------------------------

    def _current_picture_id(self) -> int | None:
        item = self.pictures.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def attach_picture(self) -> None:
        if self.backlog is None or self.workbook_id is None:
            return
        attached = {i.id for i in self.backlog.images}
        choices = [i for i in wb.load_workbook(self.db, self.workbook_id).images if i.id not in attached]
        if not choices:
            QMessageBox.information(self, "Attach picture", "Every picture of this workbook is already attached.")
            return
        labels = [f"{i.label} · sheet “{i.source_sheet}” {i.anchor_cell} ({i.width}×{i.height})" for i in choices]
        label, ok = QInputDialog.getItem(self, "Attach picture", "Picture from this workbook:", labels, 0, False)
        if ok:
            wb.attach_image(self.db, self.backlog.id, choices[labels.index(label)].id)
            self.refresh()
            self.changed.emit()

    def detach_picture(self) -> None:
        image_id = self._current_picture_id()
        if self.backlog is not None and image_id is not None:
            wb.detach_image(self.db, self.backlog.id, image_id)
            self.refresh()
            self.changed.emit()

    def move_picture(self, offset: int) -> None:
        row, image_id = self.pictures.currentRow(), self._current_picture_id()
        if self.backlog is None or image_id is None:
            return
        wb.move_image(self.db, self.backlog.id, image_id, row + offset)
        self.refresh()
        self.pictures.setCurrentRow(max(0, min(self.pictures.count() - 1, row + offset)))
        self.changed.emit()

    def view_picture(self) -> None:
        item = self.pictures.currentItem()
        if self.backlog is None or item is None:
            return
        image = next(i for i in self.backlog.images if i.id == item.data(Qt.ItemDataRole.UserRole))
        ImageViewer(pixmap_for(self.db, image.md5), f"{image.label} ({image.anchor_cell})", self).exec()

    # --- notes and context ------------------------------------------------------------------------

    def add_note(self) -> None:
        if self.backlog is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Add note", "Note (sent with every generation of this backlog):")
        if ok and text.strip():
            content.add_context(self.db, self.backlog.id, content.DOMAIN_NOTE, text)
            self.refresh()
            self.changed.emit()

    def edit_note(self) -> None:
        item = self.notes.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Edit note", "Note:", item.text())
        if ok and text.strip():
            content.update_context(self.db, item.data(Qt.ItemDataRole.UserRole), text)
            self.refresh()
            self.changed.emit()

    def add_from_backlog(self) -> None:
        if self.backlog is None:
            return
        choices = []
        for entry in wb.list_workbooks(self.db):
            view = wb.load_workbook(self.db, entry.id)
            for sheet in view.sheets:
                for b in sheet.backlogs:
                    if b.id != self.backlog.id:
                        choices.append((f"{entry.file_name} ({entry.version_label}) · {sheet.name} · #{b.number} "
                                        f"{b.title}", b.id))
        if not choices:
            QMessageBox.information(self, "Add from backlog", "There are no other backlogs.")
            return
        label, ok = QInputDialog.getItem(self, "Add from backlog", "Backlog whose text to add as context:",
                                         [c[0] for c in choices], 0, False)
        if ok:
            text, source = content.backlog_as_context(self.db, dict(choices)[label])
            content.add_context(self.db, self.backlog.id, content.SUPPLEMENTARY, text, source)
            self.refresh()
            self.changed.emit()

    def paste_context(self) -> None:
        if self.backlog is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Paste text", "Text from another file or project:")
        if ok and text.strip():
            content.add_context(self.db, self.backlog.id, content.SUPPLEMENTARY, text, "pasted text")
            self.refresh()
            self.changed.emit()

    def _remove(self, list_widget: QListWidget) -> None:
        item = list_widget.currentItem()
        if item is not None:
            content.delete_context(self.db, item.data(Qt.ItemDataRole.UserRole))
            self.refresh()
            self.changed.emit()
