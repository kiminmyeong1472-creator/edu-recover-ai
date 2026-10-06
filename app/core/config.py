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
    google_credentials_json: SecretStr | None = Field(
        default=None,
        validation_alias="GOOGLE_CREDENTIALS_JSON",
    )
    google_cloud_project: str | None = Field(
        default=None,
        validation_alias="GOOGLE_CLOUD_PROJECT",
    )
    gemini_vertex_location: str = Field(
        default="global",
        validation_alias="GEMINI_VERTEX_LOCATION",
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

    redis_url: SecretStr | None = Field(
        default=None,
        validation_alias="REDIS_URL",
    )
    interview_context_ttl_seconds: int = Field(
        default=7200,
        validation_alias="INTERVIEW_CONTEXT_TTL_SECONDS",
        ge=300,
        le=86400,
    )
    interview_context_max_messages: int = Field(
        default=24,
        validation_alias="INTERVIEW_CONTEXT_MAX_MESSAGES",
        ge=4,
        le=100,
    )
    interview_lock_seconds: int = Field(
        default=45,
        validation_alias="INTERVIEW_LOCK_SECONDS",
        ge=10,
        le=120,
    )
    interview_lock_wait_seconds: int = Field(
        default=5,
        validation_alias="INTERVIEW_LOCK_WAIT_SECONDS",
        ge=1,
        le=30,
    )
    interview_llm_concurrency: int = Field(
        default=20,
        validation_alias="INTERVIEW_LLM_CONCURRENCY",
        ge=1,
        le=200,
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
