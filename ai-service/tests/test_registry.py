import hashlib

import pytest

from conftest import SAMPLE_REPO, needs_rg
from tools.registry import ROLE_TOOLS, TOOLS

# --- Permissions --------------------------------------------------------------------------


def test_role_permissions_match_the_design_doc():
    assert {role: sorted(tools) for role, tools in ROLE_TOOLS.items()} == {
        "planner": [],
        "analyst": ["git_status", "list_files", "read_file", "search_code"],
        "research": [],
        "coder": ["git_diff", "read_file", "search_code", "write_file"],
        "testing": [],
        "debugger": ["git_diff", "read_file"],
        "reviewer": ["git_diff", "read_file"],
    }


def test_only_the_coder_can_write():
    assert [role for role, tools in ROLE_TOOLS.items() if "write_file" in tools] == ["coder"]


def test_there_is_no_generic_command_tool():
    assert set(TOOLS) == {"list_files", "read_file", "search_code", "write_file", "git_status", "git_diff"}


@pytest.mark.parametrize("role", ["debugger", "reviewer", "analyst", "planner"])
def test_roles_without_write_permission_cannot_write(registry, role):
    call = registry.call(role, "write_file", {"path": "app/main.py", "content": "# hacked\n"})
    assert call.ok is False
    assert call.error == f"The {role} agent is not allowed to use write_file"
    assert "hacked" not in (registry.workspace.root / "app" / "main.py").read_text()


def test_an_unknown_role_gets_nothing(registry):
    call = registry.call("admin", "read_file", {"path": "app/main.py"})
    assert call.ok is False
    assert "not allowed" in call.error


def test_an_unknown_tool_is_refused(registry):
    call = registry.call("coder", "run_command", {"command": "rm -rf /"})
    assert call.ok is False
    assert call.error == "Unknown tool: 'run_command'"


def test_tools_for_a_role(registry):
    assert [t.name for t in registry.tools_for("debugger")] == ["git_diff", "read_file"]


# --- Argument checking and results --------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({}, "path: Field required"),
        ({"path": "app/main.py", "mode": "rb"}, "mode: Extra inputs are not permitted"),
        ({"path": "app/main.py", "start_line": 0}, "start_line: Input should be greater than or equal to 1"),
        ({"path": ["a", "b"]}, "path: Input should be a valid string"),
    ],
)
def test_invalid_arguments_are_refused_with_the_reason(registry, args, problem):
    call = registry.call("analyst", "read_file", args)
    assert call.ok is False
    assert call.error.startswith("Invalid arguments for read_file")
    assert problem in call.error


def test_a_successful_call_is_recorded(registry):
    call = registry.call("analyst", "read_file", {"path": "app/main.py", "end_line": 1})
    assert call.ok is True
    assert call.error is None
    assert call.result["content"] == "from fastapi import FastAPI\n"
    assert call.role == "analyst" and call.tool == "read_file"
    assert isinstance(call.duration_ms, int)


def test_a_refused_path_is_an_error_result_not_an_exception(registry):
    call = registry.call("analyst", "read_file", {"path": "../../.env"})
    assert call.ok is False
    assert "outside the repository" in call.error


def test_os_errors_are_reported_without_host_paths(registry, monkeypatch):
    def locked(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", str(registry.workspace.root / "app" / "main.py"))

    monkeypatch.setattr("tools.files.Path.write_bytes", locked)
    call = registry.call("coder", "write_file", {"path": "app/main.py", "content": "x"})
    assert call.ok is False
    assert call.error == "write_file failed: Permission denied"


# --- Phase 3 "done when": inspect and edit fastapi-shop safely ----------------------------


def _fingerprint(folder):
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@needs_rg
def test_agents_can_inspect_and_edit_the_sample_repo_safely(registry):
    original = _fingerprint(SAMPLE_REPO)

    # Analyst: find the endpoint and read it.
    found = registry.call("analyst", "search_code", {"pattern": r"def list_products"}).result["matches"][0]
    assert found["path"] == "app/routers/products.py"
    source = registry.call("analyst", "read_file", {"path": found["path"]}).result["content"]

    # Coder: add pagination and a test for it.
    edited = source.replace(
        "def list_products():\n    return sorted(store.products.values(), key=lambda p: p.id)",
        "def list_products(limit: int = 20, offset: int = 0):\n"
        "    items = sorted(store.products.values(), key=lambda p: p.id)\n"
        "    return items[offset : offset + limit]",
    )
    assert edited != source
    assert registry.call("coder", "write_file", {"path": found["path"], "content": edited}).ok
    assert registry.call(
        "coder",
        "write_file",
        {"path": "tests/test_pagination.py", "content": "def test_limit(client):\n    assert len(client.get('/products?limit=2').json()) == 2\n"},
    ).ok

    # Reviewer: the diff shows exactly those two changes.
    diff = registry.call("reviewer", "git_diff").result["diff"]
    assert "+def list_products(limit: int = 20, offset: int = 0):" in diff
    assert "+++ b/tests/test_pagination.py" in diff
    status = registry.call("analyst", "git_status").result["changes"]
    assert {c["path"] for c in status} == {"app/routers/products.py", "tests/test_pagination.py"}

    # Attempts to step outside are refused, whoever asks.
    assert not registry.call("coder", "write_file", {"path": "../../escape.py", "content": "x"}).ok
    assert not registry.call("coder", "read_file", {"path": ".git/config"}).ok
    assert not registry.call("debugger", "write_file", {"path": "app/main.py", "content": "x"}).ok

    # The original sample repository was never touched.
    assert _fingerprint(SAMPLE_REPO) == original
