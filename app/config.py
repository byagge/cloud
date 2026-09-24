from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    bot_token: str = ""
    bot_mode: str = "polling"  # webhook | polling
    public_base_url: str = "http://localhost:8080"
    tg_webhook_secret: str = "change-me-tg-webhook-secret-32chars"
    owner_tg_id: int = 0
    admin_chat_id: int | None = None

    database_url: str = "sqlite+aiosqlite:///./data/arix.db"
    redis_url: str = "memory://"
    embedded_worker: bool = True

    partner_base_url: str = "http://127.0.0.1:9"
    partner_api_key: str = ""
    partner_write_limit: int = 9
    partner_read_limit: int = 50
    partner_timeout_connect: float = 5.0
    partner_timeout_read: float = 15.0
    use_fake_partner: bool = True

    fernet_key: str = ""
    webhook_path_secret: str = "change-me-pay-webhook-secret-32chars"
    gateway: str = "fake"

    support_url: str = "https://t.me/arxixx"
    terms_url: str = "https://example.com/terms"
    default_lang: str = "en"
    sentry_dsn: str = ""

    http_host: str = "0.0.0.0"
    http_port: int = 8080

    partner_quotas: dict[str, int] = Field(
        default_factory=lambda: {"purchase": 4, "manage": 2, "renew": 2, "retry": 1}
    )

    @field_validator("admin_chat_id", mode="before")
    @classmethod
    def _empty_admin_chat(cls, v: object) -> object:
        if v == "" or v is None:
            return None
        return v

    @field_validator("bot_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"webhook", "polling"}:
            raise ValueError("BOT_MODE must be webhook or polling")
        return v

    @model_validator(mode="after")
    def _validate_partner_url(self) -> Settings:
        if self.use_fake_partner:
            return self
        parsed = urlparse(self.partner_base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1"}:
            raise ValueError(
                "PARTNER_BASE_URL must use https for non-localhost hosts (OPEN-1)"
            )
        if not self.partner_base_url:
            raise ValueError("PARTNER_BASE_URL is required")
        return self

    @property
    def is_dev(self) -> bool:
        return self.env.lower() == "dev"

    def ensure_fernet(self) -> str:
        if self.fernet_key:
            return self.fernet_key
        key = Fernet.generate_key().decode()
        object.__setattr__(self, "fernet_key", key)
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
