    # Lives at the ai-service root so pytest adds this folder to sys.path,
# letting tests `import main`, `import config`, etc.
import os

# Tests use their own token so they never depend on the developer's .env.
# Real environment variables take priority over the .env file in pydantic-settings.
TEST_TOKEN = "test-token-" + "x" * 32
os.environ["INTERNAL_TOKEN"] = TEST_TOKEN
