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
    # "gemini-flash-lite-latest" follows Google's current Flash-Lite model. Free-tier quotas are per model,
    # and "gemini-flash-latest" was limited to 20 requests a day: about one Mantis run. Check limits in AI Studio.
    # Pin a specific name (e.g. from AI Studio's model list) if you need repeatable results.
    gemini_model: str = "gemini-flash-lite-latest"
    # How long the model "thinks" before answering. "low" keeps a tool-calling turn to a few seconds;
    # higher levels were measured at 20-50s per turn, which makes a run take many minutes.
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    # Upper limit for a single model call. A slow call fails instead of hanging the task.
    llm_timeout_seconds: float = Field(default=60, gt=0, le=180)
    # How many times to try a call that was rate limited (HTTP 429) before giving up.
    llm_rate_limit_attempts: int = Field(default=3, ge=1, le=5)
    # Most model calls Mantis sends per minute, across all running tasks. Keep it at or below your
    # Gemini quota (shown in AI Studio) so calls wait their turn instead of failing with 429. 0 = no limit.
    llm_requests_per_minute: int = Field(default=10, ge=0)

    # Each task edits its own copy of the repository under this folder (gitignored).
    workspaces_dir: Path = REPO_ROOT / "workspaces"
    # ripgrep, used by search_code. Set RG_PATH to the full path of rg.exe if it isn't on PATH.
    rg_path: str = "rg"

    # The repository tasks work on, until projects can be chosen in the UI.
    default_repo_path: Path = REPO_ROOT / "sample-repos" / "fastapi-shop"
    # Where run progress is reported. 127.0.0.1 rather than localhost: the backend listens on IPv4 only.
    backend_url: str = "http://127.0.0.1:3000"
    # Tasks that can run at the same time. More runs means more Gemini calls competing for the rate limit.
    max_concurrent_runs: int = Field(default=2, ge=1, le=8)
    # Most tool-calling turns an agent may take before its step fails, so a confused model can't loop forever.
    analyst_max_turns: int = Field(default=15, ge=1, le=50)
    coder_max_turns: int = Field(default=20, ge=1, le=50)

    @property
    def checkpoint_db(self) -> Path:
        # LangGraph saves each run's state here after every step.
        return self.workspaces_dir / "checkpoints.sqlite"

    @model_validator(mode="after")
    def require_provider_key(self) -> "Settings":
        # Fail at startup, not on the first task, when the chosen provider has no key.
        # An empty "GEMINI_API_KEY=" line in .env counts as missing.
        key = self.gemini_api_key.get_secret_value().strip() if self.gemini_api_key else ""
        if self.llm_provider == "gemini" and not key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER is gemini")
        return self


settings = Settings()
