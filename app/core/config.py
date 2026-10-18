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
    # This controls OpenAI and Claude only. Gemini remains on enterprise Google
    # Cloud. AWS deployment must explicitly opt in; it never falls back to a
    # direct provider API if IAM, retention or profile validation fails.
    provider_transport: str = Field(
        default="direct",
        validation_alias="EDURECOVER_PROVIDER_TRANSPORT",
    )
    aws_region: str | None = Field(default=None, validation_alias="AWS_REGION")
    bedrock_openai_inference_profile: str | None = Field(
        default=None, validation_alias="EDURECOVER_BEDROCK_OPENAI_INFERENCE_PROFILE",
    )
    bedrock_claude_inference_profile: str | None = Field(
        default=None, validation_alias="EDURECOVER_BEDROCK_CLAUDE_INFERENCE_PROFILE",
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
