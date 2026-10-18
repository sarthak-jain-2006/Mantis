import os
import sys

import pytest

from conftest import SAMPLE_REPO
from tools import git
from tools.workspace import ToolError, Workspace, create_workspace


def test_workspace_is_a_copy_with_a_clean_baseline(workspace):
    assert (workspace.root / "app" / "routers" / "products.py").read_text() == (
        SAMPLE_REPO / "app" / "routers" / "products.py"
    ).read_text()
    assert git.run(workspace.root, "status", "--porcelain") == ""
    log = git.run(workspace.root, "log", "--format=%an|%s")
    assert log.strip() == "Mantis|Baseline before Mantis changes"


def test_secrets_dependencies_and_history_are_not_copied(tmp_path):
    source = tmp_path / "repo"
    (source / "app").mkdir(parents=True)
    (source / "app" / "main.py").write_text("print('hi')\n")
    (source / ".env").write_text("SECRET=hunter2\n")
    (source / ".env.example").write_text("SECRET=\n")
    (source / "server.pem").write_text("-----BEGIN KEY-----\n")
    (source / "node_modules" / "lib").mkdir(parents=True)
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("[core]\n")

    ws = create_workspace(source, "copy-test", tmp_path / "workspaces")

    assert (ws.root / "app" / "main.py").exists()
    assert (ws.root / ".env.example").exists()
    assert not (ws.root / ".env").exists()
    assert not (ws.root / "server.pem").exists()
    assert not (ws.root / "node_modules").exists()
    # .git exists, but it is the workspace's own fresh repository, not the copied one.
    assert "Baseline" in git.run(ws.root, "log", "--format=%s")


@pytest.mark.parametrize("task_id", ["../evil", "a/b", "a\\b", "", "x" * 65, "C:"])
def test_task_id_cannot_choose_the_folder(tmp_path, task_id):
    with pytest.raises(ToolError, match="Task id"):
        create_workspace(SAMPLE_REPO, task_id, tmp_path)


def test_a_workspace_is_never_reused(tmp_path):
    create_workspace(SAMPLE_REPO, "same-id", tmp_path)
    with pytest.raises(ToolError, match="already exists"):
        create_workspace(SAMPLE_REPO, "same-id", tmp_path)


def test_git_ignores_variables_pointing_elsewhere(workspace, tmp_path, monkeypatch):
    # If git honoured GIT_DIR from the environment, these commands would act on another repository.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "somewhere-else" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "somewhere-else"))
    assert "Baseline" in git.run(workspace.root, "log", "--format=%s")


# --- The path guard -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "app/../../outside.txt",
        "/etc/passwd",
        "\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "C:win.ini",
        "\\\\server\\share\\file",
        "app/main.py:hidden",
        "app\x00.py",
    ],
)
def test_paths_outside_the_repository_are_refused(workspace, path):
    with pytest.raises(ToolError):
        workspace.resolve(path)


@pytest.mark.parametrize(
    "path",
    [".git/config", ".GIT/config", ".git./config", "app/../.git/HEAD", "node_modules/x.js", "app/__pycache__/m.pyc"],
)
def test_hidden_folders_are_refused(workspace, path):
    with pytest.raises(ToolError, match="not allowed"):
        workspace.resolve(path)


@pytest.mark.parametrize("path", [".env", ".ENV", ".env.", ".env.local", "config/.env", "keys/server.pem", "id_rsa"])
def test_secret_files_are_refused(workspace, path):
    with pytest.raises(ToolError, match="secrets"):
        workspace.resolve(path)


def test_env_templates_are_allowed(workspace):
    assert workspace.resolve(".env.example") == workspace.root / ".env.example"


def test_ci_config_is_readable_but_not_writable_by_default(workspace):
    workspace.resolve(".github/workflows/ci.yml")
    with pytest.raises(ToolError, match="CI configuration"):
        workspace.resolve(".github/workflows/ci.yml", write=True)
    assert Workspace(workspace.root, allow_ci_writes=True).resolve(".github/workflows/ci.yml", write=True)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_junction_out_of_the_repository_is_refused(workspace, tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    _winapi.CreateJunction(str(outside), str(workspace.root / "docs"))

    with pytest.raises(ToolError, match="outside the repository"):
        workspace.resolve("docs/secret.txt")


def test_a_symlink_out_of_the_repository_is_refused(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret")
    try:
        os.symlink(outside, workspace.root / "link.txt")
    except OSError:
        pytest.skip("this account cannot create symlinks")

    with pytest.raises(ToolError, match="outside the repository"):
        workspace.resolve("link.txt")
