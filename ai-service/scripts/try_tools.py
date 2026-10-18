"""Walk through the Phase 3 tools on a fresh copy of sample-repos/fastapi-shop.

    cd ai-service
    uv run python scripts/try_tools.py          # removes the copy at the end
    uv run python scripts/try_tools.py --keep   # keeps it under workspaces/ to look at

No AI is involved: this plays the agents' part by hand, the way Phase 4 agents will call the tools.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import tools` works from scripts/

from config import REPO_ROOT, settings  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402
from tools.workspace import create_workspace, remove_workspace  # noqa: E402


def show(text: str, limit: int = 25) -> None:
    lines = text.splitlines()
    print("\n".join("   | " + line for line in lines[:limit]))
    if len(lines) > limit:
        print(f"   | ... ({len(lines) - limit} more lines)")


def step(registry: ToolRegistry, title: str, role: str, tool: str, args: dict | None = None) -> dict | None:
    call = registry.call(role, tool, args)
    shown_args = {k: (v[:40] + "...") if k == "content" and len(v) > 40 else v for k, v in (args or {}).items()}
    print(f"\n== {title}")
    print(f"   {role} -> {tool}({json.dumps(shown_args)})")
    if not call.ok:
        print(f"   REFUSED: {call.error}")
    elif "diff" in call.result:
        show(call.result["diff"])
    elif "content" in call.result:
        show(call.result["content"])
    elif "files" in call.result:
        show("\n".join(call.result["files"]))
    elif "matches" in call.result:
        show("\n".join(f"{m['path']}:{m['line']}: {m['text']}" for m in call.result["matches"]))
    elif "changes" in call.result:
        show("\n".join(f"{c['change']}: {c['path']}" for c in call.result["changes"]) or "(no changes)")
    else:
        show(json.dumps(call.result))
    return call.result


def main() -> None:
    keep = "--keep" in sys.argv
    task_id = "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = create_workspace(REPO_ROOT / "sample-repos" / "fastapi-shop", task_id, settings.workspaces_dir)
    print(f"Created workspace {task_id} (a private copy of sample-repos/fastapi-shop)")
    r = ToolRegistry(workspace)

    print("\n--------- Things agents are allowed to do ---------")
    step(r, "List the repository", "analyst", "list_files")
    step(r, "Find the products endpoint", "analyst", "search_code", {"pattern": "def list_products"})
    source = step(r, "Read it", "analyst", "read_file", {"path": "app/routers/products.py"})["content"]

    edited = source.replace(
        "def list_products():\n    return sorted(store.products.values(), key=lambda p: p.id)",
        "def list_products(limit: int = 20, offset: int = 0):\n"
        "    items = sorted(store.products.values(), key=lambda p: p.id)\n"
        "    return items[offset : offset + limit]",
    )
    step(r, "Coder adds pagination", "coder", "write_file", {"path": "app/routers/products.py", "content": edited})
    step(r, "Which files changed?", "analyst", "git_status")
    step(r, "The exact change", "reviewer", "git_diff")

    print("\n--------- Things that are refused ---------")
    step(r, "Escape the repository", "analyst", "read_file", {"path": "../../.env"})
    step(r, "Absolute Windows path", "analyst", "read_file", {"path": "C:/Windows/win.ini"})
    step(r, "Read git internals", "analyst", "read_file", {"path": ".git/config"})
    step(r, "Read a secrets file", "analyst", "read_file", {"path": ".env"})
    step(r, "Debugger tries to write", "debugger", "write_file", {"path": "app/main.py", "content": "# hacked"})
    step(r, "Run a shell command", "coder", "run_command", {"command": "del /s *"})

    if keep:
        print(f"\nKept the workspace at {workspace.root}")
    else:
        remove_workspace(workspace)
        print(f"\nRemoved workspace {task_id}. The original sample repo was never changed.")


if __name__ == "__main__":
    main()
