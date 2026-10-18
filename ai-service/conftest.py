# Lives at the ai-service root so pytest adds this folder to sys.path,
# letting tests `import main`, `import config`, etc.
import os

# Tests use their own values so they never depend on the developer's .env.
# Real environment variables take priority over the .env file in pydantic-settings.
TEST_TOKEN = "test-token-" + "x" * 32
os.environ["INTERNAL_TOKEN"] = TEST_TOKEN
# Lets settings load. No test calls Gemini: they all use FakeLLM below.
os.environ["GEMINI_API_KEY"] = "test-gemini-key"

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from config import settings  # noqa: E402  (needs the env vars above)
from schemas import ImplementationPlan  # noqa: E402

SAMPLE_REPO = Path(__file__).resolve().parent.parent / "sample-repos" / "fastapi-shop"

# search_code needs ripgrep; those tests are skipped on a machine without it.
needs_rg = pytest.mark.skipif(shutil.which(settings.rg_path) is None, reason="ripgrep (rg) is not installed")


@pytest.fixture
def workspace(tmp_path):
    """A fresh copy of sample-repos/fastapi-shop for one test."""
    from tools.workspace import create_workspace

    return create_workspace(SAMPLE_REPO, "test-task", tmp_path / "workspaces")


@pytest.fixture
def registry(workspace):
    from tools.registry import ToolRegistry

    return ToolRegistry(workspace)

SAMPLE_PLAN = ImplementationPlan(
    summary="Add limit/offset pagination to the products list endpoint.",
    subtasks=[
        "Find the endpoint that lists products",
        "Add limit and offset query parameters",
        "Validate 1 <= limit <= 100 and offset >= 0",
        "Write tests for the defaults and the bounds",
    ],
    acceptance_criteria=["Existing tests still pass", "New tests cover the limit and offset bounds"],
    constraints=["Keep the response shape unchanged"],
    needs_research=False,
)


class FakeLLM:
    """Stands in for a real model. Each call takes the next item from `replies`:
    a value is returned, an exception is raised."""

    model_name = "fake-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, messages, schema):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
