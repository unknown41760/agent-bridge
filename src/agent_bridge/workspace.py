from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
import stat
import subprocess
from contextlib import contextmanager


class WorkspaceError(RuntimeError):
    pass


def path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace(os.sep, "/").lstrip("./")
    pattern = pattern.replace(os.sep, "/").lstrip("./")
    candidate = PurePosixPath(normalized)
    if candidate.match(pattern):
        return True
    if pattern.startswith("**/") and candidate.match(pattern[3:]):
        return True
    return False


def _fingerprint(path: Path) -> str:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(st.st_mode):
        return "symlink:" + os.readlink(path)
    if not stat.S_ISREG(st.st_mode):
        return f"mode:{stat.S_IFMT(st.st_mode)}"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"file:{st.st_mode & 0o7777}:{digest.hexdigest()}"


@dataclass(slots=True)
class WorkspaceSnapshot:
    files: dict[str, str]

    def changed_since(self, baseline: "WorkspaceSnapshot") -> list[str]:
        changed = [
            path
            for path in sorted(set(self.files) | set(baseline.files))
            if self.files.get(path, "missing") != baseline.files.get(path, "missing")
        ]
        return changed


class GitWorkspace:
    def __init__(self, cwd: str | Path):
        cwd = Path(cwd).resolve()
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorkspaceError("workspace_invalid: writable delegation requires a Git-backed repository")
        self.root = Path(proc.stdout.strip()).resolve()


    @contextmanager
    def writer_lock(self):
        git_dir_proc = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if git_dir_proc.returncode != 0:
            raise WorkspaceError("workspace_invalid: cannot resolve Git directory")
        git_dir = Path(git_dir_proc.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = self.root / git_dir
        lock_path = git_dir.resolve() / "agent-bridge-writer.lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise WorkspaceError("workspace_invalid: another delegated writer is already active in this working tree") from exc
        try:
            os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
            os.close(fd)
            fd = -1
            yield
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _git_paths(self) -> set[str]:
        proc = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorkspaceError("workspace_invalid: git ls-files failed")
        return {
            item.decode("utf-8", errors="surrogateescape")
            for item in proc.stdout.split(b"\0")
            if item
        }

    def _matching_ignored_paths(self, patterns: list[str]) -> set[str]:
        if not patterns:
            return set()
        found: set[str] = set()
        for current_root, dirs, files in os.walk(self.root, followlinks=False):
            current = Path(current_root)
            if current == self.root:
                dirs[:] = [name for name in dirs if name != ".git"]
            rel_root = current.relative_to(self.root)
            for name in files:
                rel = (rel_root / name).as_posix()
                if any(path_matches(rel, pattern) for pattern in patterns):
                    found.add(rel)
            for name in list(dirs):
                full = current / name
                if full.is_symlink():
                    rel = (rel_root / name).as_posix()
                    if any(path_matches(rel, pattern) for pattern in patterns):
                        found.add(rel)
        return found

    def snapshot(self, scope_patterns: list[str] | None = None) -> WorkspaceSnapshot:
        paths = self._git_paths()
        paths.update(self._matching_ignored_paths(scope_patterns or []))
        return WorkspaceSnapshot({path: _fingerprint(self.root / path) for path in sorted(paths)})

    @staticmethod
    def scope_violations(changed: list[str], allowed: list[str], forbidden: list[str]) -> list[str]:
        violations: set[str] = set()
        for path in changed:
            if any(path_matches(path, pattern) for pattern in forbidden):
                violations.add(path)
            if allowed and not any(path_matches(path, pattern) for pattern in allowed):
                violations.add(path)
        return sorted(violations)
