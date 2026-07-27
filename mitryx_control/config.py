from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    discord_application_id: int
    discord_guild_id: int
    approval_channel_id: int
    approved_channel_id: int
    unban_channel_id: int
    server_config_channel_id: int
    server_api_key: str
    api_host: str
    api_port: int
    database_path: Path
    trust_proxy_headers: bool
    rcon_host: str
    rcon_port: int
    rcon_password: str
    rcon_timeout_seconds: float
    rcon_sync_whitelist: bool

    @property
    def rcon_enabled(self) -> bool:
        return bool(self.rcon_host and self.rcon_password)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name} en .env")
    return value


def _int(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise RuntimeError(f"Falta la variable obligatoria {name} en .env")
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un número entero") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def load_settings() -> Settings:
    load_dotenv()
    settings = Settings(
        discord_token=_required("DISCORD_TOKEN"),
        discord_application_id=_int("DISCORD_APPLICATION_ID"),
        discord_guild_id=_int("DISCORD_GUILD_ID"),
        approval_channel_id=_int("APPROVAL_CHANNEL_ID"),
        approved_channel_id=_int("APPROVED_CHANNEL_ID"),
        unban_channel_id=_int("UNBAN_CHANNEL_ID"),
        server_config_channel_id=_int("SERVER_CONFIG_CHANNEL_ID"),
        server_api_key=_required("SERVER_API_KEY"),
        api_host=os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0",
        api_port=_int("API_PORT", 8080),
        database_path=Path(os.getenv("DATABASE_PATH", "data/mitryx.sqlite3")).expanduser(),
        trust_proxy_headers=_bool("TRUST_PROXY_HEADERS", False),
        rcon_host=os.getenv("RCON_HOST", "127.0.0.1").strip(),
        rcon_port=_int("RCON_PORT", 25575),
        rcon_password=os.getenv("RCON_PASSWORD", "").strip(),
        rcon_timeout_seconds=float(os.getenv("RCON_TIMEOUT_SECONDS", "5")),
        rcon_sync_whitelist=_bool("RCON_SYNC_WHITELIST", False),
    )

    if settings.server_api_key in {"CHANGE_ME", "CAMBIA_ESTA_CLAVE_LARGA_Y_ALEATORIA"}:
        raise RuntimeError("SERVER_API_KEY debe cambiarse por una clave larga y aleatoria")
    if settings.api_port < 1 or settings.api_port > 65535:
        raise RuntimeError("API_PORT fuera de rango")
    if settings.rcon_port < 1 or settings.rcon_port > 65535:
        raise RuntimeError("RCON_PORT fuera de rango")

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
