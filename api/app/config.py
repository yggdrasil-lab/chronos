"""Configuration management for Chronos API."""

from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_secret_file(path_value: str, label: str) -> str:
    secret_path = Path(path_value)
    if not secret_path.is_file():
        raise ValueError(f"{label} file not found: {path_value}")
    secret_value = secret_path.read_text(encoding="utf-8").strip()
    if not secret_value:
        raise ValueError(f"{label} file is empty: {path_value}")
    return secret_value


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and Swarm secret files."""

    database_url: str = "postgresql+psycopg://chronos:chronos@localhost:5432/chronos"
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "chronos"
    db_user: str = "chronos"
    db_password: str = ""
    db_password_file: str = ""
    environment: str = "development"
    timezone: str = "Pacific/Auckland"
    vapid_private_key: str = ""
    vapid_private_key_file: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = ""
    push_scheduler_enabled: bool = True
    push_poll_interval_seconds: int = 30
    eir_base_url: str = ""
    kratos_base_url: str = ""
    plutus_base_url: str = ""
    peer_timeout_seconds: float = 2.5

    model_config = SettingsConfigDict(
        env_prefix="CHRONOS_",
        case_sensitive=False,
    )

    @property
    def vapid_configured(self) -> bool:
        """Return True when private key, public key, and subject are all non-empty."""
        return bool(
            self.vapid_private_key.strip()
            and self.vapid_public_key.strip()
            and self.vapid_subject.strip()
        )

    @model_validator(mode="after")
    def _load_and_validate_secrets(self) -> "Settings":
        """Load Swarm secret files and enforce fail-fast production credentials."""
        if self.db_password_file:
            secret_value = _read_secret_file(self.db_password_file, "Database password")
            if not self.db_password:
                self.db_password = secret_value

        if self.vapid_private_key_file:
            vapid_secret = _read_secret_file(self.vapid_private_key_file, "VAPID private key")
            if not self.vapid_private_key:
                self.vapid_private_key = vapid_secret

        if self.push_poll_interval_seconds <= 0:
            raise ValueError("CHRONOS_PUSH_POLL_INTERVAL_SECONDS must be > 0.")

        if self.peer_timeout_seconds <= 0:
            raise ValueError("CHRONOS_PEER_TIMEOUT_SECONDS must be > 0.")

        if self.environment.strip().lower() == "production":
            self._validate_production_requirements()

        if "database_url" not in self.model_fields_set and self.db_password:
            self.database_url = (
                f"postgresql+psycopg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )

        return self

    def _validate_production_requirements(self) -> None:
        if not self.db_password.strip():
            raise ValueError(
                "CHRONOS_DB_PASSWORD (or CHRONOS_DB_PASSWORD_FILE) is required when CHRONOS_ENVIRONMENT=production."
            )
        missing: list[str] = []
        if not self.vapid_private_key.strip():
            missing.append("CHRONOS_VAPID_PRIVATE_KEY (or CHRONOS_VAPID_PRIVATE_KEY_FILE)")
        if not self.vapid_public_key.strip():
            missing.append("CHRONOS_VAPID_PUBLIC_KEY")
        if not self.vapid_subject.strip():
            missing.append("CHRONOS_VAPID_SUBJECT")
        if not self.eir_base_url.strip():
            missing.append("CHRONOS_EIR_BASE_URL")
        if not self.kratos_base_url.strip():
            missing.append("CHRONOS_KRATOS_BASE_URL")
        if not self.plutus_base_url.strip():
            missing.append("CHRONOS_PLUTUS_BASE_URL")
        if missing:
            raise ValueError(
                f"Missing required production configuration: {', '.join(missing)}"
            )


settings = Settings()
