"""Environment-based configuration.

All settings are read from environment variables prefixed with ``VERITAS_`` (or a
repository-root ``.env`` file during local development). Defaults are chosen to
fail closed: production environment, debug off, restricted access, no CORS origins.
Production additionally refuses unsafe combinations at startup, including demo access.
"""

# Preserve the V1 BaseSettings constructor correction; mypy 2.x now considers it redundant.
# mypy: disable-error-code="unused-ignore"

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

Environment = Literal["development", "test", "production"]
AccessMode = Literal["restricted", "demo"]


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VERITAS_",
        env_file=REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors must not echo input values: the database URL holds a password.
        hide_input_in_errors=True,
    )

    environment: Environment = "production"
    debug: bool = False

    # "restricted": provisioned authentication and explicit backend authorization required.
    # "demo": anonymous, read-only access to flagged synthetic cases in development/test only.
    access_mode: AccessMode = "restricted"

    database_url: SecretStr = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://USER:PASSWORD@HOST:5432/DB"
    )
    database_echo: bool = False

    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    max_request_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    expose_api_docs: bool = False
    session_ttl_minutes: int = Field(default=720, ge=5, le=1_440)
    login_failure_limit: int = Field(default=5, ge=3, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=120)

    @field_validator("allowed_hosts", "cors_origins", "trusted_proxy_ips", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _validate_trusted_proxy_ips(cls, values: list[str]) -> list[str]:
        try:
            for value in values:
                ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("VERITAS_TRUSTED_PROXY_IPS must contain IPs or CIDRs") from exc
        return values

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.access_mode != "restricted":
            problems.append("VERITAS_ACCESS_MODE must be 'restricted' in production")
        if self.debug:
            problems.append("VERITAS_DEBUG must be false in production")
        if "*" in self.cors_origins:
            problems.append("VERITAS_CORS_ORIGINS must not contain '*' in production")
        if "*" in self.allowed_hosts:
            problems.append("VERITAS_ALLOWED_HOSTS must not contain '*' in production")
        if not self.database_url.get_secret_value().startswith("postgresql"):
            problems.append("VERITAS_DATABASE_URL must be a PostgreSQL URL in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def api_docs_enabled(self) -> bool:
        return self.expose_api_docs and self.environment != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # BaseSettings resolves required fields from environment/.env at runtime.
    # mypy cannot represent that environment-driven constructor contract.
    return Settings()  # type: ignore[call-arg]
