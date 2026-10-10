from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# All services share the .env at the repository root.
ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    # Shared secret that the Node backend sends in the X-Internal-Token header.
    # min_length stops the service from starting with a placeholder like "change-me".
    internal_token: str = Field(min_length=32)
    ai_service_port: int = 8000


settings = Settings()
