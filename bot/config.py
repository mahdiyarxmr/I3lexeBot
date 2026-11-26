"""Environment configuration and validation for the i3l.exe webhook runtime.

All secrets (bot token, database URL, webhook secret) come from environment
variables. Nothing is hardcoded and nothing is ever logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Legacy default owner id, kept so a missing OWNER_ID does not silently change
# business rules. Owners should always set OWNER_ID explicitly.
DEFAULT_OWNER_ID = 6341879634

BOT_NAME = "i3l.exe"
AUTO_EDIT_EVERY = 200


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing or invalid."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    database_url: str
    webhook_secret: str
    webhook_url: str
    db_pool_max: int
    db_pool_min: int

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = os.environ if env is None else env

        bot_token = env.get("BOT_TOKEN", "")
        database_url = env.get("DATABASE_URL", "")
        webhook_secret = env.get("WEBHOOK_SECRET", "")
        webhook_url = env.get("WEBHOOK_URL", "")

        raw_owner = env.get("OWNER_ID", "")
        if raw_owner == "":
            owner_id = DEFAULT_OWNER_ID
        else:
            try:
                owner_id = int(str(raw_owner).strip())
            except (TypeError, ValueError) as exc:
                raise ConfigError("OWNER_ID must be an integer.") from exc

        def _int(name: str, default: int, minimum: int) -> int:
            raw = env.get(name, "")
            if raw in ("", None):
                return default
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{name} must be an integer.") from exc
            if value < minimum:
                raise ConfigError(f"{name} must be >= {minimum}.")
            return value

        return cls(
            bot_token=bot_token,
            owner_id=owner_id,
            database_url=database_url,
            webhook_secret=webhook_secret,
            webhook_url=webhook_url,
            db_pool_max=_int("DB_POOL_MAX", 4, 1),
            db_pool_min=_int("DB_POOL_MIN", 1, 0),
        )

    def validate_runtime(self) -> None:
        """Validate the variables the webhook function needs at request time."""
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.webhook_secret:
            missing.append("WEBHOOK_SECRET")
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )

    def validate_registration(self) -> None:
        """Validate the variables required to register the Telegram webhook."""
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.webhook_secret:
            missing.append("WEBHOOK_SECRET")
        if not self.webhook_url:
            missing.append("WEBHOOK_URL")
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )
        if not self.webhook_url.rstrip("/").endswith("/api/webhook"):
            raise ConfigError(
                "WEBHOOK_URL must point at /api/webhook on your Vercel deployment, "
                "for example https://i3lexebot.vercel.app/api/webhook"
            )

    def validate_webhook_url(self) -> None:
        """Validate the variables required before registering the webhook."""
        self.validate_registration()


def load_config() -> Config:
    """Load configuration from the environment without validating."""
    return Config.from_env()
