import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from wta import processes
from wta.processes import ProcessRegistry, Tracked

# A child that starts a grandchild, writes the grandchild's PID, then sleeps.
TREE_SCRIPT = """
import subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(g.pid))
time.sleep(60)
"""


def start_tree(tmp: Path) -> tuple[subprocess.Popen, int]:
    pid_file = tmp / "grandchild.pid"
    child = subprocess.Popen([sys.executable, "-c", TREE_SCRIPT, str(pid_file)])
    deadline = time.time() + 15
    while not (pid_file.exists() and pid_file.read_text()):
        if time.time() > deadline:
            raise RuntimeError("grandchild did not start")
        time.sleep(0.05)
    return child, int(pid_file.read_text())


def alive(pid: int) -> bool:
    return processes.create_time(pid) is not None


class ProcessTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_time_and_listing(self):
        me = processes.list_processes()
        self.assertIn(os.getpid(), me)
        started = processes.create_time(os.getpid())
        self.assertIsNotNone(started)
        self.assertLess(abs(time.time() - started), 3600 * 24)

    def test_kill_tree_kills_grandchildren(self):
        child, grandchild = start_tree(self.base)
        try:
            self.assertIn(grandchild, processes.descendants(child.pid))
            killed = processes.kill_tree(child.pid)
            self.assertIn(child.pid, killed)
            self.assertIn(grandchild, killed)
            child.wait(timeout=5)
            self.assertFalse(alive(grandchild))
        finally:
            child.kill()

    def test_registry_survives_reload_and_kills_children(self):
        path = self.base / "processes.json"
        child, grandchild = start_tree(self.base)
        try:
            registry = ProcessRegistry(path)
            registry.track(child.pid, "claude run")
            registry.refresh()
            self.assertIn(grandchild, registry.entries)
            # A new registry (e.g. after an app restart) still knows about them.
            reloaded = ProcessRegistry(path)
            # At least these two; with a venv launcher there are extra interpreter processes in between.
            self.assertTrue({child.pid, grandchild} <= set(reloaded.entries))
            killed = {e.pid for e in reloaded.kill_all()}
            self.assertTrue({child.pid, grandchild} <= killed)
            child.wait(timeout=5)
            self.assertFalse(alive(grandchild))
            self.assertEqual(ProcessRegistry(path).entries, {})
        finally:
            child.kill()

    def test_reused_pid_is_never_killed(self):
        # Our own PID, recorded with a different creation time, looks like a reused PID.
        path = self.base / "processes.json"
        registry = ProcessRegistry(path)
        registry.entries[os.getpid()] = Tracked(os.getpid(), 12345.0, "stale entry")
        self.assertFalse(registry.is_same_process(registry.entries[os.getpid()]))
        self.assertEqual(registry.kill_all(), [])
        self.assertTrue(alive(os.getpid()))  # still running this test

    def test_corrupt_registry_file(self):
        path = self.base / "processes.json"
        path.write_text("{nope", "utf-8")
        self.assertEqual(ProcessRegistry(path).entries, {})


if __name__ == "__main__":
    unittest.main()
