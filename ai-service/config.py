from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
# All services share the .env at the repository root.
ROOT_ENV = REPO_ROOT / ".env"


class Settings(BaseSettings):
    # hide_input_in_errors: a startup error must not print the values it read (tokens, keys).
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore", hide_input_in_errors=True)

    # Shared secret that the Node backend sends in the X-Internal-Token header.
    # min_length stops the service from starting with a placeholder like "change-me".
    internal_token: str = Field(min_length=32)
    ai_service_port: int = 8000

    # Which LLM backend to use. Only Gemini exists for now; Ollama can be added behind the same interface.
    llm_provider: Literal["gemini"] = "gemini"
    # SecretStr hides the value in logs and error messages (it prints as "**********").
    gemini_api_key: SecretStr | None = None
    # "gemini-flash-latest" always points at Google's current Flash model.
    # Pin a specific name (e.g. from AI Studio's model list) if you need repeatable results.
    gemini_model: str = "gemini-flash-latest"
    # Upper limit for a single model call. A slow call fails instead of hanging the task.
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    # How many times to try a call that was rate limited (HTTP 429) before giving up.
    llm_rate_limit_attempts: int = Field(default=3, ge=1, le=5)

    # Each task edits its own copy of the repository under this folder (gitignored).
    workspaces_dir: Path = REPO_ROOT / "workspaces"
    # ripgrep, used by search_code. Set RG_PATH to the full path of rg.exe if it isn't on PATH.
    rg_path: str = "rg"

    @model_validator(mode="after")
    def require_provider_key(self) -> "Settings":
        # Fail at startup, not on the first task, when the chosen provider has no key.
        # An empty "GEMINI_API_KEY=" line in .env counts as missing.
        key = self.gemini_api_key.get_secret_value().strip() if self.gemini_api_key else ""
        if self.llm_provider == "gemini" and not key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER is gemini")
        return self


settings = Settings()
