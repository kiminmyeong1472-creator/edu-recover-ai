from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables."""

    project_name: str = "EduRecover AI"
    app_version: str = "0.1.0"
    api_version: str = "v1"
    environment: str = "development"
    debug: bool = False

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    openai_model: str = Field(
        default="gpt-5.6-sol",
        validation_alias="OPENAI_MODEL",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        validation_alias="ANTHROPIC_MODEL",
    )
    gemini_model: str = Field(
        default="gemini-3.1-pro-preview",
        validation_alias="GEMINI_MODEL",
    )
    gemini_thinking_level: str = Field(
        default="high",
        validation_alias="GEMINI_THINKING_LEVEL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()


settings = get_settings()
