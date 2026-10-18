import pytest

from tools import files
from tools.files import git_diff, git_status, list_files, read_file, write_file
from tools.workspace import ToolError

# --- list_files ---------------------------------------------------------------------------


def test_list_files_lists_the_repository(workspace):
    listed = list_files(workspace)["files"]
    assert "app/routers/products.py" in listed
    assert "tests/test_orders.py" in listed
    assert not any(p.startswith(".git/") for p in listed)


def test_list_files_skips_secrets_and_dependencies(workspace):
    (workspace.root / ".env").write_text("SECRET=hunter2\n")
    (workspace.root / "node_modules").mkdir()
    (workspace.root / "node_modules" / "lib.js").write_text("")

    listed = list_files(workspace)["files"]
    assert ".env" not in listed
    assert not any("node_modules" in p for p in listed)


def test_list_files_in_a_subfolder(workspace):
    assert list_files(workspace, "app/routers")["files"] == [
        "app/routers/__init__.py",
        "app/routers/orders.py",
        "app/routers/products.py",
    ]


def test_list_files_is_capped(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_LIST_ENTRIES", 3)
    result = list_files(workspace)
    assert len(result["files"]) == 3
    assert result["truncated"] is True


def test_list_files_needs_a_folder(workspace):
    with pytest.raises(ToolError, match="Not a folder"):
        list_files(workspace, "app/main.py")


# --- read_file ----------------------------------------------------------------------------


def test_read_whole_file(workspace):
    result = read_file(workspace, "app/main.py")
    assert result["content"] == (workspace.root / "app" / "main.py").read_text()
    assert result["start_line"] == 1
    assert result["end_line"] == result["total_lines"]
    assert result["truncated"] is False


def test_read_a_line_range(workspace):
    result = read_file(workspace, "app/routers/products.py", start_line=9, end_line=11)
    assert result["content"].splitlines() == [
        '@router.get("", response_model=list[Product])',
        "def list_products():",
        "    return sorted(store.products.values(), key=lambda p: p.id)",
    ]


def test_end_line_past_the_end_is_clamped(workspace):
    total = read_file(workspace, "app/main.py")["total_lines"]
    assert read_file(workspace, "app/main.py", start_line=2, end_line=9999)["end_line"] == total


def test_start_line_past_the_end_is_an_error(workspace):
    with pytest.raises(ToolError, match="outside the file"):
        read_file(workspace, "app/main.py", start_line=9999)


def test_long_reads_are_truncated(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_READ_LINES", 2)
    result = read_file(workspace, "app/routers/products.py")
    assert result["truncated"] is True
    assert result["end_line"] == 2


@pytest.mark.parametrize("path", ["missing.py", "app"])
def test_read_needs_an_existing_file(workspace, path):
    with pytest.raises(ToolError, match="No such file"):
        read_file(workspace, path)


def test_binary_files_are_refused(workspace):
    (workspace.root / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00binary")
    with pytest.raises(ToolError, match="binary"):
        read_file(workspace, "logo.png")


def test_huge_files_are_refused(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 10)
    with pytest.raises(ToolError, match="cannot be read"):
        read_file(workspace, "app/main.py")


# --- write_file ---------------------------------------------------------------------------


def test_write_creates_a_file_and_folders_with_exact_bytes(workspace):
    result = write_file(workspace, "app/utils/paging.py", "def page():\n    return 1\n")
    assert result == {"path": "app/utils/paging.py", "created": True, "bytes": 25}
    # \n stays \n, even on Windows.
    assert (workspace.root / "app" / "utils" / "paging.py").read_bytes() == b"def page():\n    return 1\n"


def test_write_replaces_a_file(workspace):
    assert write_file(workspace, "app/main.py", "# replaced\n")["created"] is False
    assert (workspace.root / "app" / "main.py").read_text() == "# replaced\n"


@pytest.mark.parametrize(
    "path", ["../escape.py", ".git/hooks/pre-commit", ".env", ".github/workflows/ci.yml", "app"]
)
def test_write_refuses_unsafe_targets(workspace, path):
    with pytest.raises(ToolError):
        write_file(workspace, path, "x")


def test_write_refuses_huge_content(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_WRITE_BYTES", 5)
    with pytest.raises(ToolError, match="limit"):
        write_file(workspace, "big.txt", "123456")


# --- git_status and git_diff --------------------------------------------------------------


def test_status_is_clean_before_any_change(workspace):
    assert git_status(workspace) == {"changes": [], "clean": True}


def test_status_reports_each_kind_of_change(workspace):
    write_file(workspace, "app/main.py", "# changed\n")
    write_file(workspace, "app/new.py", "x = 1\n")
    (workspace.root / "README.md").unlink()

    changes = {c["path"]: c["change"] for c in git_status(workspace)["changes"]}
    assert changes == {"app/main.py": "modified", "app/new.py": "added", "README.md": "deleted"}


def test_diff_shows_edits_and_new_files(workspace):
    write_file(workspace, "app/main.py", "# changed\n")
    write_file(workspace, "app/new.py", "x = 1\n")

    diff = git_diff(workspace)["diff"]
    assert "+# changed" in diff
    assert "-from fastapi import FastAPI" in diff
    assert "new file mode" in diff and "+x = 1" in diff


def test_diff_can_be_limited_to_one_file(workspace):
    write_file(workspace, "app/main.py", "# changed\n")
    write_file(workspace, "app/new.py", "x = 1\n")

    diff = git_diff(workspace, "app/new.py")["diff"]
    assert "app/new.py" in diff
    assert "app/main.py" not in diff


def test_large_diffs_are_truncated(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_DIFF_CHARS", 50)
    write_file(workspace, "app/main.py", "# changed\n")
    result = git_diff(workspace)
    assert result["truncated"] is True
    assert len(result["diff"]) == 50
