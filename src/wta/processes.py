"""Processes the app started: find their children, kill whole trees, and remember them.

Processes are only ever found from a PID the app started itself, never by program name.
Each tracked PID is stored with its creation time, so a PID that Windows has since reused
for an unrelated process (e.g. the user's own browser) is never touched.
"""

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

CREATE_TIME_TOLERANCE = 1.0  # seconds


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    ppid: int
    name: str


# --- platform layer ------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _EPOCH_AS_FILETIME = 116444736000000000  # 1970-01-01 in 100 ns units since 1601

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    def list_processes() -> dict[int, ProcessEntry]:
        snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == _INVALID_HANDLE_VALUE:
            return {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            found: dict[int, ProcessEntry] = {}
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                found[entry.th32ProcessID] = ProcessEntry(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile)
                ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
            return found
        finally:
            _kernel32.CloseHandle(snap)

    def create_time(pid: int) -> float | None:
        """Creation time in epoch seconds, or None if the process is gone or not accessible."""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            exit_code = wintypes.DWORD()
            if _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != 259:
                return None  # exited (259 = STILL_ACTIVE)
            value = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return (value - _EPOCH_AS_FILETIME) / 10_000_000
        finally:
            _kernel32.CloseHandle(handle)

    def _terminate(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            ok = bool(_kernel32.TerminateProcess(handle, 1))
            _kernel32.WaitForSingleObject(handle, 3000)
            return ok
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _parse_etime(text: str) -> float:
        days, _, rest = text.strip().rpartition("-")
        parts = [int(p) for p in rest.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        hours, minutes, seconds = parts
        return (int(days) if days else 0) * 86400 + hours * 3600 + minutes * 60 + seconds

    def _ps(*fields: str) -> list[list[str]]:
        out = subprocess.run(["ps", "-A", "-o", ",".join(f + "=" for f in fields)], capture_output=True, text=True)
        return [line.split(None, len(fields) - 1) for line in out.stdout.splitlines() if line.strip()]

    def list_processes() -> dict[int, ProcessEntry]:
        return {int(p): ProcessEntry(int(p), int(pp), name) for p, pp, name in _ps("pid", "ppid", "comm")}

    def create_time(pid: int) -> float | None:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return time.time() - _parse_etime(out.stdout)

    def _terminate(pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError:
            return False


# --- trees -----------------------------------------------------------------------


def descendants(root: int, processes: dict[int, ProcessEntry] | None = None) -> list[int]:
    """All descendants of `root`, parents before children. A child created before its
    supposed parent is skipped: its parent PID was reused and it is not really ours."""
    processes = list_processes() if processes is None else processes
    children: dict[int, list[int]] = {}
    for entry in processes.values():
        if entry.pid != entry.ppid:
            children.setdefault(entry.ppid, []).append(entry.pid)
    order, queue = [], [root]
    while queue:
        parent = queue.pop(0)
        parent_time = create_time(parent)
        for child in children.get(parent, []):
            child_time = create_time(child)
            if parent_time is not None and child_time is not None and child_time + CREATE_TIME_TOLERANCE < parent_time:
                continue
            if child not in order:
                order.append(child)
                queue.append(child)
    return order


def kill_tree(root: int) -> list[int]:
    """Kill `root` and everything under it, children first. Returns the PIDs killed."""
    tree = [root] + descendants(root)
    killed = []
    for pid in reversed(tree):
        if _terminate(pid):
            killed.append(pid)
    return killed


# --- registry ----------------------------------------------------------------------


@dataclass
class Tracked:
    pid: int
    create_time: float
    role: str


class ProcessRegistry:
    """Persists the processes the app started (and their children) so they can be closed
    later, even after an app crash. Only exact PID + creation-time matches are killed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[int, Tracked] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.entries = {int(e["pid"]): Tracked(int(e["pid"]), float(e["create_time"]), str(e["role"])) for e in data}
        except (OSError, ValueError, KeyError, TypeError):
            self.entries = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps([asdict(e) for e in self.entries.values()], indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def is_same_process(self, entry: Tracked) -> bool:
        current = create_time(entry.pid)
        return current is not None and abs(current - entry.create_time) <= CREATE_TIME_TOLERANCE

    def track(self, pid: int, role: str) -> None:
        started = create_time(pid)
        if started is not None:
            self.entries[pid] = Tracked(pid, started, role)
            self._save()

    def refresh(self) -> None:
        """Add new children of tracked processes and drop entries that have exited."""
        processes = list_processes()
        changed = False
        for entry in list(self.entries.values()):
            if not self.is_same_process(entry):
                del self.entries[entry.pid]
                changed = True
                continue
            for child in descendants(entry.pid, processes):
                if child not in self.entries:
                    started = create_time(child)
                    if started is not None:
                        self.entries[child] = Tracked(child, started, f"child of {entry.role}")
                        changed = True
        if changed:
            self._save()

    def kill_all(self) -> list[Tracked]:
        """Kill every tracked process tree that is still the same process. Returns what was killed."""
        self.refresh()
        killed = []
        for entry in sorted(self.entries.values(), key=lambda e: e.create_time, reverse=True):
            if self.is_same_process(entry):
                kill_tree(entry.pid)
                killed.append(entry)
        self.entries.clear()
        self._save()
        return killed

    def forget(self, pid: int) -> None:
        if self.entries.pop(pid, None) is not None:
            self._save()
