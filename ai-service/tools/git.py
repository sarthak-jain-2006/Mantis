"""Git commands run inside a workspace.

Every call is pinned to the workspace's own repository and ignores the user's personal git
config, so neither can leak in: no signing prompts, hooks, aliases or credential helpers.
"""

import os
import subprocess
from pathlib import Path

# Environment variables that could point git at a different repository or config.
_REDIRECTING_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")

BASELINE_AUTHOR = ("-c", "user.name=Mantis", "-c", "user.email=mantis@localhost", "-c", "commit.gpgsign=false")


class GitError(Exception):
    pass


def run(root: Path, *args: str, timeout: float = 30) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _REDIRECTING_VARS}
    env.update(
        GIT_DIR=str(root / ".git"),
        GIT_WORK_TREE=str(root),
        GIT_CONFIG_GLOBAL=os.devnull,  # skip ~/.gitconfig
        GIT_CONFIG_NOSYSTEM="1",  # skip the system-wide config
        GIT_TERMINAL_PROMPT="0",  # never wait for a password
    )
    command = [
        "git",
        "-c", "core.autocrlf=false",  # report files exactly as written
        "-c", "core.fsmonitor=false",
        "-c", "core.quotepath=false",  # show non-ASCII file names as-is
        "--literal-pathspecs",  # a path like "*.py" means that file, not a pattern
        *args,
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took longer than {timeout:g}s") from e
    if completed.returncode != 0:
        raise GitError(f"git {args[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def init_baseline(root: Path) -> None:
    """Start a fresh repository holding the workspace as copied, before any agent touches it."""
    run(root, "init", "-q", "-b", "main")
    run(root, "add", "-A")
    run(root, *BASELINE_AUTHOR, "commit", "-q", "--allow-empty", "-m", "Baseline before Mantis changes")


def mark_new_files(root: Path) -> None:
    # "Intent to add" lists new files in the index without staging their content,
    # so `git diff HEAD` includes them. Without it, files the Coder creates would not appear.
    run(root, "add", "-A", "--intent-to-add")
