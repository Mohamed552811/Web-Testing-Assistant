"""Clean up old run folders, and back up or restore the database."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wta import housekeeping as hk
from wta.db.database import Database


class HousekeepingDialog(QDialog):
    def __init__(self, db: Database, runs_dir: Path, data_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db, self.runs_dir, self.data_dir = db, runs_dir, data_dir
        self.setWindowTitle("Clean up and back up")
        self.setMinimumWidth(620)
        self.found: list[hk.OldRun] = []

        # Disk use and old runs
        self.disk = QLabel()
        self.days = QSpinBox(minimum=1, maximum=3650, value=30, suffix=" days")
        self.keep_latest = QCheckBox("Keep each scenario's latest run (its screenshots back the steps you review)")
        self.keep_latest.setChecked(True)
        self.find_button = QPushButton("Find")
        self.remove_button = QPushButton("Remove…")
        self.found_label = QLabel()
        self.found_label.setWordWrap(True)
        older = QHBoxLayout()
        older.addWidget(QLabel("Run folders older than"))
        older.addWidget(self.days)
        older.addWidget(self.find_button)
        older.addStretch(1)
        older.addWidget(self.remove_button)
        runs_box = QGroupBox("Run folders")
        runs_layout = QVBoxLayout(runs_box)
        runs_layout.addWidget(self.disk)
        runs_layout.addLayout(older)
        runs_layout.addWidget(self.keep_latest)
        runs_layout.addWidget(self.found_label)
        note = QLabel("Removing a run folder deletes its prompt, trace and screenshots on disk. The run's history "
                      "(steps, trace and comparison) stays in the database.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        runs_layout.addWidget(note)

        # Backups
        self.backups = QListWidget()
        self.backups.setFixedHeight(140)
        self.backup_button = QPushButton("Back up now")
        self.restore_button = QPushButton("Restore selected…")
        self.open_button = QPushButton("Open backups folder")
        buttons = QHBoxLayout()
        for button in (self.backup_button, self.restore_button, self.open_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        backup_box = QGroupBox("Database backups")
        backup_layout = QVBoxLayout(backup_box)
        backup_layout.addWidget(self.backups)
        backup_layout.addLayout(buttons)
        privacy = QLabel(f"Backups stay in this machine's data folder ({data_dir / hk.BACKUP_DIR}). They include "
                         f"the test accounts. The latest {hk.KEEP_BACKUPS} are kept.")
        privacy.setWordWrap(True)
        privacy.setProperty("muted", True)
        backup_layout.addWidget(privacy)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(runs_box)
        layout.addWidget(backup_box)
        layout.addWidget(close)

        self.find_button.clicked.connect(self.find)
        self.days.valueChanged.connect(lambda _v: self._clear_found())
        self.keep_latest.toggled.connect(lambda _v: self._clear_found())
        self.remove_button.clicked.connect(self.remove)
        self.backup_button.clicked.connect(self.back_up)
        self.restore_button.clicked.connect(self.restore)
        self.open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir / hk.BACKUP_DIR))))
        self.backups.itemSelectionChanged.connect(self._update)
        self._show_disk()
        self._load_backups()
        self._clear_found()

    def _show_disk(self) -> None:
        size = hk.folder_size(self.runs_dir) if self.runs_dir.is_dir() else 0
        self.disk.setText(f"The runs folder uses {hk.human_size(size)}.")

    def _clear_found(self) -> None:
        self.found = []
        self.found_label.setText("Press Find to see which folders would be removed.")
        self._update()

    def find(self) -> None:
        self.found = hk.old_runs(self.db, self.runs_dir, self.days.value(), self.keep_latest.isChecked())
        total = sum(r.size for r in self.found)
        self.found_label.setText(f"{len(self.found)} run folder(s), {hk.human_size(total)}." if self.found
                                 else "No run folders match.")
        self._update()

    def remove(self) -> None:
        if not self.found:
            return
        total = hk.human_size(sum(r.size for r in self.found))
        answer = QMessageBox.question(self, "Remove run folders",
                                      f"Delete {len(self.found)} run folder(s) ({total}) from disk?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed, freed = hk.remove_run_folders(self.db, self.runs_dir, self.found)
        self._show_disk()
        self._clear_found()
        self.found_label.setText(f"Removed {removed} run folder(s), freed {hk.human_size(freed)}.")

    def _load_backups(self) -> None:
        self.backups.clear()
        for path in hk.list_backups(self.data_dir):
            item = QListWidgetItem(f"{path.name}   ({hk.human_size(path.stat().st_size)})")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.backups.addItem(item)
        self._update()

    def _update(self) -> None:
        self.remove_button.setEnabled(bool(self.found))
        self.restore_button.setEnabled(self.backups.currentItem() is not None)

    def back_up(self) -> None:
        path = hk.backup(self._db_path(), self.data_dir)
        self._load_backups()
        self.backups.setCurrentRow(0)
        self.found_label.setText(f"Backed up to {path.name}.")

    def restore(self) -> None:
        item = self.backups.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self, "Restore backup",
            f"Replace the current database with {path.name}?\n\nThis takes effect when you restart the app. "
            "The current database is backed up first, so you can go back.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            hk.schedule_restore(path, self.data_dir)
        except ValueError as exc:
            QMessageBox.warning(self, "Restore backup", str(exc))
            return
        QMessageBox.information(self, "Restore backup", "Restart the app to finish restoring.")

    def _db_path(self) -> Path:
        return self.db.path
