"""search_code: ripgrep over the workspace."""

import json
import subprocess

from config import settings
from tools.workspace import (
    HIDDEN_DIRS,
    SECRET_FILES,
    SECRET_SUFFIXES,
    ToolError,
    Workspace,
    is_hidden_dir,
    is_secret_file,
)

MAX_RESULTS = 100
MAX_MATCHES_PER_FILE = 20
TIMEOUT_SECONDS = 20

# ripgrep already skips hidden files and anything in .gitignore. These globs also exclude
# dependency folders and secret files whatever the repository's .gitignore says.
_EXCLUDE_GLOBS = [
    *(f"!{name}" for name in sorted(HIDDEN_DIRS | SECRET_FILES)),
    "!.env.*",
    *(f"!*{suffix}" for suffix in SECRET_SUFFIXES),
]


def search_code(ws: Workspace, pattern: str, path: str = ".", fixed_string: bool = False) -> dict:
    target = ws.resolve(path)
    command = [
        settings.rg_path,
        "--json",
        "--no-config",  # ignore any RIPGREP_CONFIG_PATH flags
        "--max-columns", "300",
        "--max-columns-preview",
        "--max-filesize", "1M",
        "--max-count", str(MAX_MATCHES_PER_FILE),
        *(arg for glob in _EXCLUDE_GLOBS for arg in ("--glob", glob)),
        *(["--fixed-strings"] if fixed_string else []),
        "-e", pattern,  # -e: a pattern starting with "-" is still a pattern, not a flag
        "--", ws.relative(target),
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command, cwd=ws.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS,
        )  # fmt: skip
    except FileNotFoundError as e:
        raise ToolError("ripgrep (rg) is not installed or not on PATH; set RG_PATH to rg.exe") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"Search took longer than {TIMEOUT_SECONDS}s; narrow the path or pattern") from e

    matches = []
    truncated = False
    for line in completed.stdout.splitlines():
        event = json.loads(line)
        if event["type"] != "match":
            continue
        data = event["data"]
        text = data["lines"].get("text")  # missing when the line isn't valid UTF-8
        file_path = data["path"].get("text")
        if text is None or file_path is None:
            continue
        file_path = file_path.replace("\\", "/").removeprefix("./")
        # Second line of defence: drop anything the path guard would refuse, whatever rg returned.
        if any(is_hidden_dir(part) or is_secret_file(part) for part in file_path.split("/")):
            continue
        if len(matches) == MAX_RESULTS:
            truncated = True
            break
        matches.append({"path": file_path, "line": data["line_number"], "text": text.rstrip("\r\n")})

    # Exit code 1 means "no matches"; 2 means an error, such as an invalid regular expression.
    if completed.returncode == 2 and not matches:
        message = completed.stderr.strip().splitlines()
        raise ToolError(f"Search failed: {message[-1] if message else 'unknown error'}")
    return {"matches": matches, "truncated": truncated}
