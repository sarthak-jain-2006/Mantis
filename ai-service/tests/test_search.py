import pytest

from config import settings
from conftest import needs_rg
from tools import search
from tools.search import search_code
from tools.workspace import ToolError

pytestmark = needs_rg


def test_finds_a_definition(workspace):
    result = search_code(workspace, r"def list_products")
    assert result["matches"] == [{"path": "app/routers/products.py", "line": 10, "text": "def list_products():"}]


def test_fixed_string_treats_regex_characters_literally(workspace):
    matches = search_code(workspace, "lambda p: p.id)", fixed_string=True)["matches"]
    assert [m["path"] for m in matches] == ["app/routers/products.py"]


def test_a_pattern_starting_with_a_dash_is_not_a_flag(workspace):
    matches = search_code(workspace, "-= item.quantity", fixed_string=True)["matches"]
    assert [m["path"] for m in matches] == ["app/routers/orders.py"]


def test_search_can_be_limited_to_a_folder(workspace):
    matches = search_code(workspace, "def test_", path="tests")["matches"]
    assert matches
    assert all(m["path"].startswith("tests/") for m in matches)


def test_secrets_dependencies_and_git_are_never_searched(workspace):
    (workspace.root / ".env").write_text("TOKEN=hunter2\n")
    (workspace.root / "keys.pem").write_text("hunter2\n")
    (workspace.root / "node_modules").mkdir()
    (workspace.root / "node_modules" / "lib.js").write_text("hunter2\n")

    assert search_code(workspace, "hunter2")["matches"] == []
    # The baseline commit message lives inside .git.
    assert search_code(workspace, "Baseline before Mantis")["matches"] == []


def test_results_are_capped(workspace, monkeypatch):
    monkeypatch.setattr(search, "MAX_RESULTS", 2)
    result = search_code(workspace, "def ")
    assert len(result["matches"]) == 2
    assert result["truncated"] is True


def test_an_invalid_regex_is_an_error(workspace):
    with pytest.raises(ToolError, match="Search failed"):
        search_code(workspace, "(unclosed")


def test_no_matches_is_not_an_error(workspace):
    assert search_code(workspace, "no_such_name_anywhere") == {"matches": [], "truncated": False}


def test_missing_ripgrep_is_explained(workspace, monkeypatch):
    monkeypatch.setattr(settings, "rg_path", "definitely-not-ripgrep")
    with pytest.raises(ToolError, match="RG_PATH"):
        search_code(workspace, "x")
