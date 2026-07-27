from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class PlayerRecord:
    minecraft_uuid: str
    minecraft_name: str
    discord_id: str
    status: str
    request_ip: str
    last_client_ip: str
    last_server_ip: str
    requested_at: str
    decided_at: str
    decided_by: str
    ban_reason: str
    banned_at: str
    banned_by: str
    request_message_id: int | None
    approved_message_id: int | None
    banned_message_id: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PlayerRecord":
        return cls(**dict(row))


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(self.path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    minecraft_uuid TEXT PRIMARY KEY,
                    minecraft_name TEXT NOT NULL,
                    discord_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'not_requested',
                    request_ip TEXT NOT NULL DEFAULT '',
                    last_client_ip TEXT NOT NULL DEFAULT '',
                    last_server_ip TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL DEFAULT '',
                    decided_by TEXT NOT NULL DEFAULT '',
                    ban_reason TEXT NOT NULL DEFAULT '',
                    banned_at TEXT NOT NULL DEFAULT '',
                    banned_by TEXT NOT NULL DEFAULT '',
                    request_message_id INTEGER,
                    approved_message_id INTEGER,
                    banned_message_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS server_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    address TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO server_config(singleton, address) VALUES(1, '');

                CREATE TABLE IF NOT EXISTS panel_messages (
                    panel_key TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    minecraft_uuid TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database.initialize() no fue llamado")
        return self._connection

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    async def get_player(self, minecraft_uuid: str) -> PlayerRecord | None:
        async with self._lock:
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            return PlayerRecord.from_row(row) if row else None

    async def submit_request(
        self,
        minecraft_uuid: str,
        minecraft_name: str,
        discord_id: str,
        request_ip: str,
    ) -> tuple[PlayerRecord, bool]:
        async with self._lock:
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            if row and row["status"] in {"approved", "banned", "pending"}:
                self.connection.execute(
                    "UPDATE players SET minecraft_name = ?, last_client_ip = ? WHERE minecraft_uuid = ?",
                    (minecraft_name, request_ip, minecraft_uuid),
                )
                self.connection.commit()
                updated = self.connection.execute(
                    "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
                ).fetchone()
                return PlayerRecord.from_row(updated), False

            now = utc_now()
            self.connection.execute(
                """
                INSERT INTO players(
                    minecraft_uuid, minecraft_name, discord_id, status, request_ip,
                    last_client_ip, requested_at, decided_at, decided_by,
                    ban_reason, banned_at, banned_by, request_message_id,
                    approved_message_id, banned_message_id
                ) VALUES(?, ?, ?, 'pending', ?, ?, ?, '', '', '', '', '', NULL, NULL, NULL)
                ON CONFLICT(minecraft_uuid) DO UPDATE SET
                    minecraft_name = excluded.minecraft_name,
                    discord_id = excluded.discord_id,
                    status = 'pending',
                    request_ip = excluded.request_ip,
                    last_client_ip = excluded.last_client_ip,
                    requested_at = excluded.requested_at,
                    decided_at = '',
                    decided_by = '',
                    ban_reason = '',
                    banned_at = '',
                    banned_by = '',
                    request_message_id = NULL,
                    approved_message_id = NULL,
                    banned_message_id = NULL
                """,
                (minecraft_uuid, minecraft_name, discord_id, request_ip, request_ip, now),
            )
            self._audit_locked("request_submitted", minecraft_uuid, discord_id, f"ip={request_ip}")
            self.connection.commit()
            created = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            return PlayerRecord.from_row(created), True

    async def update_client_seen(self, minecraft_uuid: str, minecraft_name: str, ip: str) -> None:
        async with self._lock:
            self.connection.execute(
                """
                INSERT INTO players(minecraft_uuid, minecraft_name, last_client_ip, status)
                VALUES(?, ?, ?, 'not_requested')
                ON CONFLICT(minecraft_uuid) DO UPDATE SET
                    minecraft_name = excluded.minecraft_name,
                    last_client_ip = excluded.last_client_ip
                """,
                (minecraft_uuid, minecraft_name, ip),
            )
            self.connection.commit()

    async def authorize_server_player(
        self, minecraft_uuid: str, minecraft_name: str, remote_ip: str
    ) -> PlayerRecord:
        async with self._lock:
            self.connection.execute(
                """
                INSERT INTO players(minecraft_uuid, minecraft_name, last_server_ip, status)
                VALUES(?, ?, ?, 'not_requested')
                ON CONFLICT(minecraft_uuid) DO UPDATE SET
                    minecraft_name = excluded.minecraft_name,
                    last_server_ip = excluded.last_server_ip
                """,
                (minecraft_uuid, minecraft_name, remote_ip),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            return PlayerRecord.from_row(row)

    async def set_status(self, minecraft_uuid: str, status: str, actor: str) -> PlayerRecord:
        if status not in {"approved", "rejected"}:
            raise ValueError("Estado de decisión inválido")
        async with self._lock:
            now = utc_now()
            self.connection.execute(
                """
                UPDATE players
                SET status = ?, decided_at = ?, decided_by = ?,
                    ban_reason = CASE WHEN ? = 'approved' THEN '' ELSE ban_reason END,
                    banned_at = CASE WHEN ? = 'approved' THEN '' ELSE banned_at END,
                    banned_by = CASE WHEN ? = 'approved' THEN '' ELSE banned_by END
                WHERE minecraft_uuid = ?
                """,
                (status, now, actor, status, status, status, minecraft_uuid),
            )
            self._audit_locked(status, minecraft_uuid, actor, "")
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            if row is None:
                raise KeyError(minecraft_uuid)
            return PlayerRecord.from_row(row)

    async def ban_player(self, minecraft_uuid: str, actor: str, reason: str) -> PlayerRecord:
        async with self._lock:
            now = utc_now()
            self.connection.execute(
                """
                UPDATE players
                SET status = 'banned', ban_reason = ?, banned_at = ?, banned_by = ?
                WHERE minecraft_uuid = ?
                """,
                (reason, now, actor, minecraft_uuid),
            )
            self._audit_locked("banned", minecraft_uuid, actor, reason)
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            if row is None:
                raise KeyError(minecraft_uuid)
            return PlayerRecord.from_row(row)

    async def unban_player(self, minecraft_uuid: str, actor: str) -> PlayerRecord:
        async with self._lock:
            now = utc_now()
            self.connection.execute(
                """
                UPDATE players
                SET status = 'approved', decided_at = ?, decided_by = ?,
                    ban_reason = '', banned_at = '', banned_by = ''
                WHERE minecraft_uuid = ?
                """,
                (now, actor, minecraft_uuid),
            )
            self._audit_locked("unbanned", minecraft_uuid, actor, "")
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM players WHERE minecraft_uuid = ?", (minecraft_uuid,)
            ).fetchone()
            if row is None:
                raise KeyError(minecraft_uuid)
            return PlayerRecord.from_row(row)

    async def set_message_id(self, minecraft_uuid: str, field: str, message_id: int | None) -> None:
        if field not in {"request_message_id", "approved_message_id", "banned_message_id"}:
            raise ValueError("Campo de mensaje inválido")
        async with self._lock:
            self.connection.execute(
                f"UPDATE players SET {field} = ? WHERE minecraft_uuid = ?",
                (message_id, minecraft_uuid),
            )
            self.connection.commit()

    async def list_players(self, status: str | None = None) -> list[PlayerRecord]:
        async with self._lock:
            if status is None:
                rows = self.connection.execute(
                    "SELECT * FROM players ORDER BY requested_at DESC"
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM players WHERE status = ? ORDER BY requested_at DESC", (status,)
                ).fetchall()
            return [PlayerRecord.from_row(row) for row in rows]

    async def counts(self) -> dict[str, int]:
        async with self._lock:
            rows = self.connection.execute(
                "SELECT status, COUNT(*) AS total FROM players GROUP BY status"
            ).fetchall()
            result = {str(row["status"]): int(row["total"]) for row in rows}
            for key in ("not_requested", "pending", "approved", "rejected", "banned"):
                result.setdefault(key, 0)
            return result

    async def get_server_address(self) -> str:
        async with self._lock:
            row = self.connection.execute(
                "SELECT address FROM server_config WHERE singleton = 1"
            ).fetchone()
            return str(row["address"] if row else "")

    async def set_server_address(self, address: str, actor: str) -> None:
        async with self._lock:
            now = utc_now()
            self.connection.execute(
                "UPDATE server_config SET address = ?, updated_by = ?, updated_at = ? WHERE singleton = 1",
                (address, actor, now),
            )
            self._audit_locked("server_address_updated", "", actor, address or "<removed>")
            self.connection.commit()

    async def get_panel_message(self, panel_key: str) -> tuple[int, int] | None:
        async with self._lock:
            row = self.connection.execute(
                "SELECT channel_id, message_id FROM panel_messages WHERE panel_key = ?", (panel_key,)
            ).fetchone()
            return (int(row["channel_id"]), int(row["message_id"])) if row else None

    async def set_panel_message(self, panel_key: str, channel_id: int, message_id: int) -> None:
        async with self._lock:
            self.connection.execute(
                """
                INSERT INTO panel_messages(panel_key, channel_id, message_id)
                VALUES(?, ?, ?)
                ON CONFLICT(panel_key) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id
                """,
                (panel_key, channel_id, message_id),
            )
            self.connection.commit()

    def _audit_locked(self, action: str, minecraft_uuid: str, actor: str, details: str) -> None:
        self.connection.execute(
            "INSERT INTO audit_log(action, minecraft_uuid, actor, details, created_at) VALUES(?, ?, ?, ?, ?)",
            (action, minecraft_uuid, actor, details, utc_now()),
        )
