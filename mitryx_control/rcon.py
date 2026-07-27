from __future__ import annotations

import asyncio
import socket
import struct
from dataclasses import dataclass


class RconError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RconConfig:
    host: str
    port: int
    password: str
    timeout: float = 5.0


class MinecraftRcon:
    def __init__(self, config: RconConfig) -> None:
        self.config = config

    async def command(self, command: str) -> str:
        return await asyncio.to_thread(self._command_sync, command)

    def _command_sync(self, command: str) -> str:
        with socket.create_connection(
            (self.config.host, self.config.port), timeout=self.config.timeout
        ) as sock:
            sock.settimeout(self.config.timeout)
            self._send_packet(sock, 1, 3, self.config.password)
            request_id, _, _ = self._read_packet(sock)
            if request_id == -1:
                raise RconError("RCON rechazó la contraseña")

            self._send_packet(sock, 2, 2, command)
            response_id, _, payload = self._read_packet(sock)
            if response_id != 2:
                raise RconError("Respuesta RCON inesperada")
            return payload

    @staticmethod
    def _send_packet(sock: socket.socket, request_id: int, packet_type: int, payload: str) -> None:
        encoded = payload.encode("utf-8")
        body = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(body)) + body)

    @staticmethod
    def _read_exact(sock: socket.socket, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise RconError("Conexión RCON cerrada inesperadamente")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @classmethod
    def _read_packet(cls, sock: socket.socket) -> tuple[int, int, str]:
        length = struct.unpack("<i", cls._read_exact(sock, 4))[0]
        if length < 10 or length > 4_194_304:
            raise RconError(f"Longitud de paquete RCON inválida: {length}")
        body = cls._read_exact(sock, length)
        request_id, packet_type = struct.unpack("<ii", body[:8])
        payload = body[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, payload
