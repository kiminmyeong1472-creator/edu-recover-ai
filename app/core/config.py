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
    gemini_flash_model: str = Field(
        default="gemini-3.8-flash",
        validation_alias="GEMINI_FLASH_MODEL",
    )
    aws_region: str = Field(
        default="ap-northeast-2",
        validation_alias="AWS_REGION",
    )
    bedrock_gpt_luna_model: str = Field(
        default="openai.gpt-5.6-luna",
        validation_alias="BEDROCK_GPT_LUNA_MODEL",
    )
    bedrock_claude_haiku_model: str = Field(
        default="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        validation_alias="BEDROCK_CLAUDE_HAIKU_MODEL",
    )
    bedrock_claude_sonnet_model: str = Field(
        default="anthropic.claude-sonnet-5",
        validation_alias="BEDROCK_CLAUDE_SONNET_MODEL",
    )
    bedrock_claude_opus_model: str = Field(
        default="anthropic.claude-opus-5",
        validation_alias="BEDROCK_CLAUDE_OPUS_MODEL",
    )
    google_credit_krw: float = Field(
        default=400000.0,
        validation_alias="EDURECOVER_GOOGLE_CREDIT_KRW",
    )
    aws_credit_krw: float = Field(
        default=280000.0,
        validation_alias="EDURECOVER_AWS_CREDIT_KRW",
    )
    budget_usd_krw: float = Field(
        default=1400.0,
        validation_alias="EDURECOVER_BUDGET_USD_KRW",
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
