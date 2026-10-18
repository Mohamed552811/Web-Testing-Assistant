"""Files Claude may upload: the session's fixtures folder, copied into each run folder.

Playwright MCP only lets the browser read files under its working folder (the run folder)
unless told otherwise, so copying the files there keeps the rest of the disk out of reach.
"""

import shutil
from pathlib import Path

RUN_SUBFOLDER = "fixtures"
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 100


def list_files(folder: str | Path | None) -> tuple[list[str], list[str]]:
    """(relative paths of the files to offer, files skipped with the reason). Hidden files are skipped."""
    if not folder:
        return [], []
    root = Path(folder)
    if not root.is_dir():
        return [], [f"The fixtures folder is missing: {root}"]
    offered, skipped, total = [], [], 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            skipped.append(f"{relative.as_posix()} (over {MAX_FILE_BYTES // (1024 * 1024)} MB)")
        elif total + size > MAX_TOTAL_BYTES or len(offered) >= MAX_FILES:
            skipped.append(f"{relative.as_posix()} (the folder is over the limit for one run)")
        else:
            offered.append(relative.as_posix())
            total += size
    return offered, skipped


def copy_into_run(folder: str | Path, files: list[str], run_dir: Path) -> Path:
    """Copy the offered files into <run folder>/fixtures, keeping sub-folders."""
    target = run_dir / RUN_SUBFOLDER
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(folder) / relative, destination)
    return target
