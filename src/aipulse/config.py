"""Load local database settings without exposing credentials in errors."""

from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class DatabaseConfigurationError(ValueError):
    """Report invalid database configuration without echoing input values."""


class DatabaseSettings(BaseSettings):
    """Hold development and separate test database credentials."""

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    db_host: str = Field(default="127.0.0.1", validation_alias="DB_HOST")
    db_port: int = Field(default=3306, ge=1, le=65535, validation_alias="DB_PORT")
    db_name: str = Field(default="aipulse_db", validation_alias="DB_NAME")
    db_user: str = Field(default="aipulse_app", validation_alias="DB_USER")
    db_password: SecretStr = Field(validation_alias="DB_PASSWORD")

    test_db_host: str | None = Field(default=None, validation_alias="TEST_DB_HOST")
    test_db_port: int | None = Field(
        default=None, ge=1, le=65535, validation_alias="TEST_DB_PORT"
    )
    test_db_name: str | None = Field(default=None, validation_alias="TEST_DB_NAME")
    test_db_user: str | None = Field(default=None, validation_alias="TEST_DB_USER")
    test_db_password: SecretStr | None = Field(
        default=None, validation_alias="TEST_DB_PASSWORD"
    )

    @field_validator("db_password")
    @classmethod
    def require_development_password(cls, value: SecretStr) -> SecretStr:
        """Reject an empty password before constructing a development URL."""
        if not value.get_secret_value():
            raise ValueError("A development credential is required")
        return value

    @model_validator(mode="after")
    def fill_test_network_defaults(self) -> "DatabaseSettings":
        """Use the same local host and port when test overrides are absent."""
        if self.test_db_host is None:
            self.test_db_host = self.db_host
        if self.test_db_port is None:
            self.test_db_port = self.db_port
        return self


def load_database_settings(env_file: Path | None = _PROJECT_ENV_FILE) -> DatabaseSettings:
    """Load the process environment over a local dotenv file."""
    try:
        return DatabaseSettings(_env_file=env_file)
    except (ValidationError, OSError, ValueError):
        raise DatabaseConfigurationError("Invalid database configuration") from None
