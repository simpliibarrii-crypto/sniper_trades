"""App configuration — env-overridable, zero-surprise defaults."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SNIPER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RavenTrade Core · Sniper Trades"
    version: str = "6.10.0"
    # 0.0.0.0 so phones on the same LAN can open the deck
    host: str = "0.0.0.0"
    port: int = 8000
    max_sessions: int = 48
    history_cap: int = 64
    broadcast_top_k: int = 7
    cors_origins: str = "*"  # comma-separated
    access_log: bool = False
    # xAI / Grok — prefer Grok Build session (~/.grok/auth.json) over console key
    xai_api_key: str = ""  # optional console key; session used first
    xai_model: str = "grok-4.5"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
