"""list_files, read_file, write_file and the git inspection tools."""

import os
from pathlib import Path

from tools import git
from tools.workspace import ToolError, Workspace, is_hidden_dir, is_link, is_secret_file

MAX_LIST_ENTRIES = 500
MAX_FILE_BYTES = 1_000_000  # larger files are refused outright
MAX_READ_LINES = 2_000  # one read returns at most this many lines...
MAX_READ_CHARS = 100_000  # ...and at most this many characters
MAX_WRITE_BYTES = 1_000_000
MAX_DIFF_CHARS = 100_000


def list_files(ws: Workspace, path: str = ".") -> dict:
    top = ws.resolve(path)
    if not top.is_dir():
        raise ToolError(f"Not a folder: {path!r}")

    files: list[str] = []
    truncated = False
    for directory, dirnames, filenames in os.walk(top):
        # Editing dirnames in place stops os.walk from going into those folders.
        dirnames[:] = sorted(d for d in dirnames if not is_hidden_dir(d) and not is_link(Path(directory, d)))
        for name in sorted(filenames):
            full = Path(directory, name)
            if is_secret_file(name) or is_link(full):
                continue
            if len(files) == MAX_LIST_ENTRIES:
                truncated = True
                break
            files.append(ws.relative(full))
        if truncated:
            break
    return {"files": files, "truncated": truncated}


def read_file(ws: Workspace, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    target = ws.resolve(path)
    if not target.is_file():
        raise ToolError(f"No such file: {path!r}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(f"{path!r} is {size:,} bytes; files over {MAX_FILE_BYTES:,} bytes cannot be read")
    data = target.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path!r} looks like a binary file")

    lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
    total = len(lines)
    first = start_line or 1
    last = min(end_line or total, total)
    if first > max(total, 1) or (end_line is not None and end_line < first):
        raise ToolError(f"Line range {first}-{end_line} is outside the file ({total} lines)")

    chosen = lines[first - 1 : last]
    truncated = False
    if len(chosen) > MAX_READ_LINES:
        chosen, truncated = chosen[:MAX_READ_LINES], True
    content = "".join(chosen)
    if len(content) > MAX_READ_CHARS:
        content, truncated = content[:MAX_READ_CHARS], True
        chosen = content.splitlines(keepends=True)
    return {
        "path": ws.relative(target),
        "content": content,
        "start_line": first,
        "end_line": first + len(chosen) - 1,
        "total_lines": total,
        "truncated": truncated,
    }


def write_file(ws: Workspace, path: str, content: str) -> dict:
    target = ws.resolve(path, write=True)
    if target == ws.root or target.is_dir():
        raise ToolError(f"{path!r} is a folder, not a file")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(f"Content is {len(data):,} bytes; the limit is {MAX_WRITE_BYTES:,}")

    created = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text mode: text mode on Windows would turn every \n into \r\n.
    target.write_bytes(data)
    return {"path": ws.relative(target), "created": created, "bytes": len(data)}


_CHANGE_NAMES = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "T": "modified", "?": "added"}


def git_status(ws: Workspace) -> dict:
    git.mark_new_files(ws.root)
    output = _git(ws, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    changes = []
    for entry in filter(None, output.split("\0")):
        code, file_path = entry[:2], entry[3:]
        letter = code[1] if code[1] != " " else code[0]
        changes.append({"path": file_path, "change": _CHANGE_NAMES.get(letter, "changed")})
    return {"changes": changes, "clean": not changes}


def git_diff(ws: Workspace, path: str | None = None) -> dict:
    git.mark_new_files(ws.root)
    pathspec = ["--", ws.relative(ws.resolve(path))] if path else []
    # --no-ext-diff / --no-textconv: never run a diff program named in repository config.
    diff = _git(ws, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "HEAD", *pathspec)
    truncated = len(diff) > MAX_DIFF_CHARS
    return {"diff": diff[:MAX_DIFF_CHARS], "truncated": truncated}


def _git(ws: Workspace, *args: str) -> str:
    try:
        return git.run(ws.root, *args)
    except git.GitError as e:
        raise ToolError(str(e)) from e
