"""Claude Code's saved sessions for the app's runs: find them and remove the test password.

Claude Code saves every `claude -p` conversation, including the prompt (which contains the
test account), under <config dir>/projects/<run folder path with every non-alphanumeric
character replaced by "-">/<session id>.jsonl. Because each run has its own folder, that
project folder holds only that run's sessions, so everything in it is cleaned and nothing
else in Claude Code's folder is touched. Credentials are never read.

The session history itself is kept: only the password text is replaced.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Says what happened, so Claude reading a resumed session does not mistake it for the password it typed.
PLACEHOLDER = "[test password, removed after the run]"


def claude_projects_dir() -> Path:
    """Where Claude Code keeps its sessions (CLAUDE_CONFIG_DIR, else ~/.claude)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(base) / "projects"


def project_folder_name(run_dir: Path | str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(run_dir))


def _fs(path: Path) -> Path:
    """The path in Windows' long-path form (\\\\?\\C:\\...). Session folders are named after the whole
    run folder path, so the full file path easily passes 260 characters, and then ordinary file
    calls report the file as missing."""
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\" + os.path.abspath(text))


def _plain(path: Path) -> Path:
    """The path without the long-path prefix, for messages and results."""
    text = str(path)
    return Path(text[4:]) if text.startswith("\\\\?\\") else path


def session_files(run_dir: Path | str, session_id: str | None = None, projects: Path | None = None) -> list[Path]:
    """All saved-session files belonging to a run.

    The run's own project folder is used when it exists; otherwise only a file named after the
    session ID is looked for, so another project's files are never picked up by accident.
    """
    projects = projects or claude_projects_dir()
    folder = _fs(projects / project_folder_name(run_dir))
    if folder.is_dir():
        return sorted(_plain(f) for f in folder.rglob("*") if f.is_file())
    if session_id and _fs(projects).is_dir():
        return sorted(_plain(f) for f in _fs(projects).glob(f"*/{session_id}.jsonl"))
    return []


def find_session_id(run_dir: Path | str, projects: Path | None = None) -> str | None:
    """The session ID of a run whose ID was not recorded (runs from before it was stored):
    the newest session file in the run's own project folder is named after it."""
    folder = _fs((projects or claude_projects_dir()) / project_folder_name(run_dir))
    files = sorted(folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime) if folder.is_dir() else []
    return files[-1].stem if files else None


def _variants(secret: str) -> list[str]:
    """The secret as plain text and as it appears inside JSON strings (escaped quotes,
    backslashes or non-ASCII characters)."""
    forms = {secret, json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1]}
    return sorted((f for f in forms if f), key=len, reverse=True)


def redact_file(path: Path, secrets: list[str], placeholder: str = PLACEHOLDER) -> int:
    """Replace every secret in a file. Returns how many were replaced. The file is rewritten
    through a temporary file, so it is never left half-written."""
    secrets = [s for s in secrets if s]
    if not secrets:
        return 0
    path = _fs(path)
    data = path.read_bytes()
    count = 0
    for secret in secrets:
        for form in _variants(secret):
            needle = form.encode("utf-8")
            hits = data.count(needle)
            if hits:
                data = data.replace(needle, placeholder.encode("utf-8"))
                count += hits
    if count:
        tmp = path.with_name(path.name + ".wta-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return count


@dataclass
class CleanResult:
    files: int = 0
    replaced: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.files > 0

    def done(self, session_id: str | None) -> bool:
        """Safe to mark the run as cleaned: no errors, and the session was found if Claude reported one.
        A run with a session ID but no file found is retried later instead of being trusted."""
        return not self.errors and (self.found or not session_id)


def clean_run(run_dir: Path | str, session_id: str | None, secrets: list[str],
              projects: Path | None = None) -> CleanResult:
    """Remove the secrets from every saved-session file of one run."""
    result = CleanResult()
    for path in session_files(run_dir, session_id, projects):
        result.files += 1
        try:
            result.replaced += redact_file(path, secrets)
        except OSError as exc:
            result.errors.append(f"{path.name}: {exc}")
    return result
