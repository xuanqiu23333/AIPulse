"""Validate DeepSeek settings while keeping the API credential private."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aipulse.config import _PROJECT_ENV_FILE


class LLMConfigurationError(ValueError):
    """Invalid model configuration, without source values in the message."""


class DeepSeekSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", populate_by_name=True, hide_input_in_errors=True,
    )

    api_key: SecretStr = Field(validation_alias="DEEPSEEK_API_KEY")
    base_url: str = Field(default="https://api.deepseek.com", validation_alias="DEEPSEEK_BASE_URL")
    model: str = Field(default="deepseek-v4-pro", validation_alias="DEEPSEEK_MODEL")
    research_model: str = Field(default="deepseek-v4-pro", validation_alias="DEEPSEEK_RESEARCH_MODEL")
    reasoning_effort: Literal["high", "max"] = Field(
        default="high", validation_alias="DEEPSEEK_REASONING_EFFORT")
    research_reasoning_effort: Literal["high", "max"] = Field(
        default="max", validation_alias="DEEPSEEK_RESEARCH_REASONING_EFFORT")
    timeout_seconds: int = Field(default=120, ge=1, le=300,
                                 validation_alias="DEEPSEEK_TIMEOUT_SECONDS")

    @field_validator("api_key")
    @classmethod
    def require_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("A DeepSeek API key is required")
        return value

    @field_validator("base_url")
    @classmethod
    def official_endpoint(cls, value: str) -> str:
        if value.rstrip("/") != "https://api.deepseek.com":
            raise ValueError("Only the official DeepSeek HTTPS endpoint is supported")
        return "https://api.deepseek.com"

    @field_validator("model", "research_model")
    @classmethod
    def model_identifier(cls, value: str) -> str:
        if re.fullmatch(r"deepseek-[A-Za-z0-9._-]{1,90}", value) is None:
            raise ValueError("Invalid DeepSeek model identifier")
        return value


def load_deepseek_settings(env_file: Path | None = _PROJECT_ENV_FILE) -> DeepSeekSettings:
    """Use process variables over the existing project .env; never echo values."""
    try:
        return DeepSeekSettings(_env_file=env_file)
    except (ValidationError, OSError, ValueError):
        raise LLMConfigurationError("Invalid DeepSeek configuration") from None
