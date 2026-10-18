"""Housekeeping: disk use of the runs folder, removing old run folders, backing up and restoring the database.

Backups stay in the app's data folder on this machine: the database holds the test accounts,
which must not leave this machine (project rule).
"""

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from wta.db.database import Database
from wta.db.models import Run, RunOutcomeKind, utcnow

BACKUP_DIR = "backups"
PENDING_RESTORE = "restore-pending.sqlite3"
KEEP_BACKUPS = 10


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def human_size(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@dataclass
class OldRun:
    run_id: int
    path: Path
    size: int
    started_at: datetime


def old_runs(db: Database, runs_dir: Path, older_than_days: int, keep_latest: bool = True) -> list[OldRun]:
    """Run folders started more than N days ago that still exist. With keep_latest, each scenario's latest
    run is kept: its screenshots are the evidence behind the steps you review and export."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    root = runs_dir.resolve()
    with db.Session() as orm:
        latest = set(orm.scalars(select(func.max(Run.id)).group_by(Run.scenario_id)))
        rows = list(orm.scalars(select(Run).where(Run.started_at < cutoff, Run.files_removed_at.is_(None),
                                                  Run.outcome != RunOutcomeKind.RUNNING)))
    found = []
    for run in rows:
        if keep_latest and run.id in latest:
            continue
        path = Path(run.run_dir)
        if path.is_dir() and path.resolve().is_relative_to(root):  # only folders the app made, under runs/
            found.append(OldRun(run.id, path, folder_size(path), run.started_at))
    return found


def remove_run_folders(db: Database, runs_dir: Path, runs: list[OldRun]) -> tuple[int, int]:
    """Delete these run folders (only ones under the runs folder). The run's history in the database stays.
    Returns (folders removed, bytes freed)."""
    root = runs_dir.resolve()
    removed = freed = 0
    for old in runs:
        if not old.path.resolve().is_relative_to(root) or old.path.resolve() == root:
            continue
        shutil.rmtree(old.path, ignore_errors=True)
        if not old.path.exists():
            removed += 1
            freed += old.size
            with db.Session.begin() as orm:
                run = orm.get(Run, old.run_id)
                if run is not None:
                    run.files_removed_at = utcnow()
    return removed, freed


def backup(db_path: Path, data_dir: Path, now: datetime | None = None) -> Path:
    """A consistent copy of the database (SQLite's backup API) in <data folder>/backups. Keeps the latest 10."""
    folder = data_dir / BACKUP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = folder / f"wta-{stamp}.sqlite3"
    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    for old in list_backups(data_dir)[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)
    return target


def list_backups(data_dir: Path) -> list[Path]:
    """Backups, newest first."""
    folder = data_dir / BACKUP_DIR
    return sorted(folder.glob("wta-*.sqlite3"), reverse=True) if folder.is_dir() else []


def schedule_restore(backup_path: Path, data_dir: Path) -> Path:
    """Restoring replaces the open database, so it happens at the next start (see apply_pending_restore)."""
    _check_database(backup_path)
    target = data_dir / PENDING_RESTORE
    shutil.copy2(backup_path, target)
    return target


def apply_pending_restore(data_dir: Path, db_path: Path) -> Path | None:
    """At startup, before the database is opened: put a scheduled restore in place.
    The current database is backed up first. Returns the safety backup, or None if nothing was pending."""
    pending = data_dir / PENDING_RESTORE
    if not pending.exists():
        return None
    safety = backup(db_path, data_dir) if db_path.exists() else None
    os.replace(pending, db_path)
    return safety


def _check_database(path: Path) -> None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ValueError(f"{path.name} is not a database: {exc}") from exc
    if "test_session" not in tables:
        raise ValueError(f"{path.name} is not a Web Testing Assistant database.")
