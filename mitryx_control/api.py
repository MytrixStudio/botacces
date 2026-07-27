from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .bot import MitryxBot
from .config import Settings
from .database import Database

LOGGER = logging.getLogger(__name__)
DISCORD_ID_RE = re.compile(r"^\d{15,24}$")


class IdentityPayload(BaseModel):
    minecraft_uuid: str = Field(min_length=32, max_length=36)
    minecraft_name: str = Field(min_length=1, max_length=16)

    @field_validator("minecraft_uuid")
    @classmethod
    def normalize_uuid(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("UUID de Minecraft inválido") from exc

    @field_validator("minecraft_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", value):
            raise ValueError("Nombre de Minecraft inválido")
        return value


class RequestPayload(IdentityPayload):
    discord_id: str

    @field_validator("discord_id")
    @classmethod
    def validate_discord_id(cls, value: str) -> str:
        value = value.strip()
        if not DISCORD_ID_RE.fullmatch(value):
            raise ValueError("ID de Discord inválido")
        return value


class ServerAuthorizePayload(IdentityPayload):
    remote_ip: str = Field(default="", max_length=64)

    @field_validator("remote_ip")
    @classmethod
    def validate_remote_ip(cls, value: str) -> str:
        return normalize_ip(value)


def normalize_ip(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        queue = self._entries[key]
        while queue and now - queue[0] > self.window_seconds:
            queue.popleft()
        if len(queue) >= self.limit:
            raise HTTPException(status_code=429, detail="Demasiadas solicitudes. Intenta más tarde.")
        queue.append(now)


def create_api(settings: Settings, database: Database, bot: MitryxBot) -> FastAPI:
    app = FastAPI(title="Mitryx Access API", version="2.0.0")
    request_limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    status_limiter = SlidingWindowLimiter(limit=60, window_seconds=60)

    def client_ip(request: Request) -> str:
        if settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return normalize_ip(forwarded.split(",", 1)[0])
        return normalize_ip(request.client.host if request.client else "")

    def response_for(status: str, server_address: str = "") -> dict[str, str]:
        messages = {
            "not_requested": "Debes solicitar acceso antes de jugar.",
            "pending": "Tu solicitud está pendiente de revisión.",
            "approved": "Acceso aprobado.",
            "rejected": "Tu solicitud fue rechazada. Puedes volver a solicitar.",
            "banned": "Tu acceso está bloqueado.",
        }
        return {
            "status": status,
            "server_address": server_address if status == "approved" else "",
            "message": messages.get(status, "Estado desconocido."),
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/client/status")
    async def client_status(payload: IdentityPayload, request: Request) -> dict[str, str]:
        ip = client_ip(request)
        status_limiter.check(f"status:{ip}")
        await database.update_client_seen(
            payload.minecraft_uuid, payload.minecraft_name, ip
        )
        record = await database.get_player(payload.minecraft_uuid)
        status = record.status if record else "not_requested"
        address = await database.get_server_address() if status == "approved" else ""
        if status == "approved" and not address:
            return {
                "status": "approved",
                "server_address": "",
                "message": "Acceso aprobado, pero el servidor todavía no tiene IP configurada.",
            }
        return response_for(status, address)

    @app.post("/api/v1/client/request")
    async def client_request(payload: RequestPayload, request: Request) -> dict[str, str]:
        ip = client_ip(request)
        request_limiter.check(f"request:{ip}")
        record, created = await database.submit_request(
            payload.minecraft_uuid,
            payload.minecraft_name,
            payload.discord_id,
            ip,
        )
        if created or (record.status == "pending" and not record.request_message_id):
            try:
                await bot.ensure_pending_request(record.minecraft_uuid)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("No se pudo publicar la solicitud en Discord")
                raise HTTPException(
                    status_code=503,
                    detail="La solicitud se guardó, pero Discord no está disponible.",
                ) from exc

        address = await database.get_server_address() if record.status == "approved" else ""
        return response_for(record.status, address)

    @app.post("/api/v1/server/authorize")
    async def server_authorize(
        payload: ServerAuthorizePayload,
        x_mitryx_server_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, str | bool]:
        supplied = x_mitryx_server_key or ""
        if not hmac.compare_digest(supplied, settings.server_api_key):
            raise HTTPException(status_code=401, detail="Clave de servidor inválida")

        record = await database.authorize_server_player(
            payload.minecraft_uuid,
            payload.minecraft_name,
            payload.remote_ip,
        )
        if record.status == "approved" and record.approved_message_id:
            try:
                await bot.refresh_approved_card(record)
            except Exception:  # noqa: BLE001
                LOGGER.exception("No se pudo actualizar la tarjeta del jugador aprobado")
        address = await database.get_server_address()
        if record.status == "banned":
            return {"allowed": False, "reason": "Tu acceso está bloqueado."}
        if record.status != "approved":
            return {
                "allowed": False,
                "reason": "No tienes una solicitud aprobada en Discord.",
            }
        if not address:
            return {
                "allowed": False,
                "reason": "El servidor todavía no está habilitado por administración.",
            }
        return {"allowed": True, "reason": "Acceso aprobado."}

    return app
