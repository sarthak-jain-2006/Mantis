"""A task's private copy of a repository, and the path guard every tool goes through.

Agents never get a real filesystem path. They pass a path relative to the workspace,
and `Workspace.resolve` either returns a safe absolute path inside it or refuses.
"""

import os
import re
import shutil
from pathlib import Path

from tools import git


class ToolError(Exception):
    """A tool refused or failed. The message is shown to the agent, so it never contains
    host paths outside the workspace."""


# Folders tools never look inside: version control, installed dependencies, caches.
HIDDEN_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
)
# Files that usually hold secrets. Templates such as .env.example are allowed.
SECRET_FILES = frozenset({".env", ".netrc", ".pypirc", ".npmrc", "id_rsa", "id_ecdsa", "id_ed25519"})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
# CI configuration: changing it can run code on someone else's machines, so writes need a flag.
CI_PATHS = frozenset({".github", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml", "jenkinsfile"})

TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _normalise(name: str) -> str:
    # Windows ignores case and trailing dots/spaces, so ".GIT" and ".env." name the same thing as
    # ".git" and ".env". Comparing normalised names stops those spellings from slipping through.
    return name.rstrip(" .").lower()


def is_secret_file(name: str) -> bool:
    name = _normalise(name)
    if name in ENV_TEMPLATES:
        return False
    return name in SECRET_FILES or name.startswith(".env.") or name.endswith(SECRET_SUFFIXES)


def is_hidden_dir(name: str) -> bool:
    return _normalise(name) in HIDDEN_DIRS


def is_link(path: Path) -> bool:
    # Junctions are Windows' directory links; they can point anywhere, like symlinks.
    return path.is_symlink() or path.is_junction()


class Workspace:
    def __init__(self, root: Path, *, allow_ci_writes: bool = False):
        self.root = root.resolve(strict=True)
        self.allow_ci_writes = allow_ci_writes

    def resolve(self, path: str, *, write: bool = False) -> Path:
        """Turn an agent-supplied relative path into a checked absolute path inside the workspace."""
        if "\x00" in path:
            raise ToolError("Path contains a null character")
        if ":" in path:
            # Rules out drive letters (C:\...) and Windows alternate data streams (file.py:hidden).
            raise ToolError(f"Path must be relative to the repository, without ':' ({path!r})")
        requested = Path(path.strip() or ".")
        if requested.is_absolute() or requested.anchor:
            raise ToolError(f"Path must be relative to the repository ({path!r})")

        # resolve() follows "..", symlinks and junctions, so whatever the path really points at is checked.
        target = (self.root / requested).resolve()
        if not target.is_relative_to(self.root):
            raise ToolError(f"Path is outside the repository ({path!r})")

        # Checked on the resolved path, so "app/../.git" or a link to .git is caught too.
        parts = target.relative_to(self.root).parts
        for part in parts:
            if is_hidden_dir(part):
                raise ToolError(f"Access to {part!r} is not allowed ({path!r})")
            if is_secret_file(part):
                raise ToolError(f"{part!r} may contain secrets and cannot be accessed ({path!r})")
        if write and parts and _normalise(parts[0]) in CI_PATHS and not self.allow_ci_writes:
            raise ToolError(f"Changing CI configuration is not allowed for this task ({path!r})")
        return target

    def relative(self, path: Path) -> str:
        """How a path is shown to agents: relative, with forward slashes."""
        rel = path.relative_to(self.root).as_posix()
        return rel if rel != "." else "."


def _ignore_on_copy(directory: str, names: list[str]) -> set[str]:
    # Never copy secrets, dependencies or the source's own git history into a workspace,
    # and skip links: copying one would pull in whatever it points at, even outside the repo.
    skipped = set()
    for name in names:
        full = Path(directory, name)
        if is_link(full) or is_secret_file(name) or (full.is_dir() and is_hidden_dir(name)):
            skipped.add(name)
    return skipped


def create_workspace(source: Path, task_id: str, base_dir: Path) -> Workspace:
    """Copy `source` to base_dir/task_id and snapshot it, so git_diff shows exactly what the agents changed."""
    if not TASK_ID.fullmatch(task_id):
        raise ToolError("Task id may only contain letters, digits, '-' and '_' (max 64 characters)")
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ToolError("The repository to copy is not a folder")

    destination = base_dir / task_id
    if destination.exists():
        raise ToolError(f"A workspace for task {task_id} already exists")
    base_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=_ignore_on_copy)

    git.init_baseline(destination)
    return Workspace(destination)


def remove_workspace(workspace: Workspace) -> None:
    # Git marks some object files read-only, which stops a plain rmtree on Windows.
    def make_writable_and_retry(func, path, _exc):
        os.chmod(path, 0o700)
        func(path)

    shutil.rmtree(workspace.root, onexc=make_writable_and_retry)
