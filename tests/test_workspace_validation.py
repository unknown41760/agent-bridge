from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_bridge.validation import run_validation_commands
from agent_bridge.workspace import GitWorkspace


def git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


class WorkspaceValidationTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        git(root, "init", "-q")
        return td, root

    def test_changed_files_from_workspace_evidence(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        path = root / "a.txt"
        path.write_text("one", encoding="utf-8")
        git(root, "add", "a.txt")
        ws = GitWorkspace(root)
        before = ws.snapshot()
        path.write_text("two", encoding="utf-8")
        after = ws.snapshot()
        self.assertEqual(after.changed_since(before), ["a.txt"])

    def test_preexisting_dirty_file_not_attributed_if_unchanged(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        path = root / "dirty.txt"
        path.write_text("base", encoding="utf-8")
        git(root, "add", "dirty.txt")
        path.write_text("already dirty", encoding="utf-8")
        ws = GitWorkspace(root)
        before = ws.snapshot()
        after = ws.snapshot()
        self.assertEqual(after.changed_since(before), [])

    def test_preexisting_dirty_file_is_attributed_if_changed_further(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        path = root / "dirty.txt"
        path.write_text("base", encoding="utf-8")
        git(root, "add", "dirty.txt")
        path.write_text("already dirty", encoding="utf-8")
        ws = GitWorkspace(root)
        before = ws.snapshot()
        path.write_text("worker changed it", encoding="utf-8")
        after = ws.snapshot()
        self.assertEqual(after.changed_since(before), ["dirty.txt"])

    def test_forbidden_tracked_file_change_is_caught(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        path = root / "secret.key"
        path.write_text("old", encoding="utf-8")
        git(root, "add", "secret.key")
        ws = GitWorkspace(root)
        before = ws.snapshot(["**/*.key"])
        path.write_text("new", encoding="utf-8")
        changed = ws.snapshot(["**/*.key"]).changed_since(before)
        self.assertEqual(ws.scope_violations(changed, [], ["**/*.key"]), ["secret.key"])

    def test_forbidden_ignored_file_change_is_caught(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        (root / ".gitignore").write_text("*.key\n", encoding="utf-8")
        git(root, "add", ".gitignore")
        ws = GitWorkspace(root)
        before = ws.snapshot(["**/*.key"])
        (root / "ignored.key").write_text("secret", encoding="utf-8")
        changed = ws.snapshot(["**/*.key"]).changed_since(before)
        self.assertIn("ignored.key", changed)
        self.assertEqual(ws.scope_violations(changed, [], ["**/*.key"]), ["ignored.key"])

    def test_outside_allowed_path_is_caught(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        ws = GitWorkspace(root)
        before = ws.snapshot(["src/**"])
        (root / "README.md").write_text("changed", encoding="utf-8")
        changed = ws.snapshot(["src/**"]).changed_since(before)
        self.assertEqual(ws.scope_violations(changed, ["src/**"], []), ["README.md"])

    def test_validation_commands_are_compact(self):
        td, root = self.make_repo()
        self.addCleanup(td.cleanup)
        command = f'{sys.executable} -c "print(\'x\'*5000)"'
        results = run_validation_commands([command], root)
        self.assertEqual(results[0].status, "passed")
        self.assertLessEqual(len(results[0].summary), 1200)

class WriterLockTests(unittest.TestCase):
    def test_one_writer_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            ws = GitWorkspace(root)
            with ws.writer_lock():
                with self.assertRaises(Exception) as ctx:
                    with ws.writer_lock():
                        pass
            self.assertIn("another delegated writer", str(ctx.exception))
