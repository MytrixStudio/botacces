from __future__ import annotations

import asyncio
import ipaddress
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .database import Database, PlayerRecord
from .rcon import MinecraftRcon, RconConfig, RconError

LOGGER = logging.getLogger(__name__)
SERVER_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._-]+(?::\d{1,5})?$")


def actor_label(user: discord.abc.User) -> str:
    return f"{user} ({user.id})"


def player_ip(record: PlayerRecord) -> str:
    # La IP observada por la API del cliente es preferible para RCON. En redes con
    # Velocity/Bungee, la IP observada por el backend puede ser la del proxy.
    return record.request_ip or record.last_client_ip or record.last_server_ip or "No registrada"


def client_ip_label(record: PlayerRecord) -> str:
    return record.request_ip or record.last_client_ip or "No registrada"


def server_ip_label(record: PlayerRecord) -> str:
    return record.last_server_ip or "Aún no registrada"


def admin_allowed(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and (
        user.guild_permissions.administrator or user.guild_permissions.manage_guild
    )


async def require_admin(interaction: discord.Interaction) -> bool:
    if admin_allowed(interaction):
        return True
    await interaction.response.send_message(
        "No tienes permiso para usar este control.", ephemeral=True
    )
    return False


class DisabledDecisionView(discord.ui.View):
    def __init__(self, approved: bool) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="APROBADO" if approved else "RECHAZADO",
                style=discord.ButtonStyle.success if approved else discord.ButtonStyle.danger,
                disabled=True,
            )
        )


class DisabledModerationView(discord.ui.View):
    def __init__(self, label: str) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label=label, disabled=True))


class RequestDecisionView(discord.ui.View):
    def __init__(self, bot: "MitryxBot", minecraft_uuid: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.minecraft_uuid = minecraft_uuid

        approve = discord.ui.Button(
            label="Aprobar",
            style=discord.ButtonStyle.success,
            custom_id=f"mitryx:approve:{minecraft_uuid}",
        )
        reject = discord.ui.Button(
            label="Rechazar",
            style=discord.ButtonStyle.danger,
            custom_id=f"mitryx:reject:{minecraft_uuid}",
        )
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(approve)
        self.add_item(reject)

    async def approve(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record, rcon_result = await self.bot.approve_player(
                self.minecraft_uuid, interaction.user
            )
            await interaction.followup.send(
                f"{record.minecraft_name} fue aprobado. {rcon_result}", ephemeral=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Error aprobando jugador")
            await interaction.followup.send(f"No se pudo aprobar: {exc}", ephemeral=True)

    async def reject(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record, rcon_result = await self.bot.reject_player(
                self.minecraft_uuid, interaction.user
            )
            await interaction.followup.send(
                f"{record.minecraft_name} fue rechazado. {rcon_result}", ephemeral=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Error rechazando jugador")
            await interaction.followup.send(f"No se pudo rechazar: {exc}", ephemeral=True)


class BanReasonModal(discord.ui.Modal, title="Banear jugador"):
    reason = discord.ui.TextInput(
        label="Motivo",
        placeholder="Escribe el motivo del bloqueo",
        max_length=300,
        required=True,
    )

    def __init__(self, bot: "MitryxBot", minecraft_uuid: str) -> None:
        super().__init__(custom_id=f"mitryx:ban-modal:{minecraft_uuid}")
        self.bot = bot
        self.minecraft_uuid = minecraft_uuid

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record, rcon_result = await self.bot.ban_player(
                self.minecraft_uuid, interaction.user, str(self.reason)
            )
            await interaction.followup.send(
                f"{record.minecraft_name} fue bloqueado. {rcon_result}", ephemeral=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Error baneando jugador")
            await interaction.followup.send(f"No se pudo banear: {exc}", ephemeral=True)


class PlayerModerationView(discord.ui.View):
    def __init__(self, bot: "MitryxBot", minecraft_uuid: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.minecraft_uuid = minecraft_uuid
        ban = discord.ui.Button(
            label="Banear IP",
            style=discord.ButtonStyle.danger,
            custom_id=f"mitryx:ban:{minecraft_uuid}",
        )
        ban.callback = self.ban
        self.add_item(ban)

    async def ban(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.send_modal(BanReasonModal(self.bot, self.minecraft_uuid))


class UnbanView(discord.ui.View):
    def __init__(self, bot: "MitryxBot", minecraft_uuid: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.minecraft_uuid = minecraft_uuid
        unban = discord.ui.Button(
            label="Desbanear",
            style=discord.ButtonStyle.success,
            custom_id=f"mitryx:unban:{minecraft_uuid}",
        )
        unban.callback = self.unban
        self.add_item(unban)

    async def unban(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record, rcon_result = await self.bot.unban_player(
                self.minecraft_uuid, interaction.user
            )
            await interaction.followup.send(
                f"{record.minecraft_name} fue desbaneado. {rcon_result}", ephemeral=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Error desbaneando jugador")
            await interaction.followup.send(f"No se pudo desbanear: {exc}", ephemeral=True)


class ServerAddressModal(discord.ui.Modal):
    def __init__(self, bot: "MitryxBot", mode: str, current: str = "") -> None:
        title = "Agregar IP del servidor" if mode == "add" else "Editar IP del servidor"
        super().__init__(title=title, custom_id=f"mitryx:server-address:{mode}")
        self.bot = bot
        self.mode = mode
        self.address = discord.ui.TextInput(
            label="IP o dominio del servidor",
            placeholder="play.ejemplo.com:25565",
            default=current or None,
            max_length=255,
            required=True,
        )
        self.add_item(self.address)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_admin(interaction):
            return
        value = str(self.address).strip()
        try:
            validated = validate_server_address(value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.database.set_server_address(validated, actor_label(interaction.user))
        await self.bot.refresh_dashboards()
        await interaction.followup.send(
            f"Dirección del servidor guardada: `{validated}`", ephemeral=True
        )


class ServerConfigView(discord.ui.View):
    def __init__(self, bot: "MitryxBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Agregar",
        style=discord.ButtonStyle.success,
        custom_id="mitryx:server:add",
    )
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await require_admin(interaction):
            return
        current = await self.bot.database.get_server_address()
        if current:
            await interaction.response.send_message(
                "Ya existe una dirección. Usa Editar.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ServerAddressModal(self.bot, "add"))

    @discord.ui.button(
        label="Editar",
        style=discord.ButtonStyle.primary,
        custom_id="mitryx:server:edit",
    )
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await require_admin(interaction):
            return
        current = await self.bot.database.get_server_address()
        await interaction.response.send_modal(ServerAddressModal(self.bot, "edit", current))

    @discord.ui.button(
        label="Remover",
        style=discord.ButtonStyle.danger,
        custom_id="mitryx:server:remove",
    )
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.database.set_server_address("", actor_label(interaction.user))
        await self.bot.refresh_dashboards()
        await interaction.followup.send(
            "La dirección fue removida. Ningún cliente podrá usar JUGAR hasta agregar otra.",
            ephemeral=True,
        )


class MitryxBot(commands.Bot):
    def __init__(self, settings: Settings, database: Database) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            application_id=settings.discord_application_id,
        )
        self.settings = settings
        self.database = database
        self.guild_object = discord.Object(id=settings.discord_guild_id)
        self.rcon = (
            MinecraftRcon(
                RconConfig(
                    host=settings.rcon_host,
                    port=settings.rcon_port,
                    password=settings.rcon_password,
                    timeout=settings.rcon_timeout_seconds,
                )
            )
            if settings.rcon_enabled
            else None
        )
        self._dashboard_lock = asyncio.Lock()
        self._player_card_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.add_view(ServerConfigView(self))
        await self._restore_persistent_views()
        self.tree.copy_global_to(guild=self.guild_object)
        await self.tree.sync(guild=self.guild_object)

    async def on_ready(self) -> None:
        LOGGER.info("Bot conectado como %s", self.user)
        await self.refresh_dashboards()
        await self.restore_missing_player_cards()

    async def _restore_persistent_views(self) -> None:
        for record in await self.database.list_players():
            if record.status == "pending" and record.request_message_id:
                self.add_view(
                    RequestDecisionView(self, record.minecraft_uuid),
                    message_id=record.request_message_id,
                )
            elif record.status == "approved" and record.approved_message_id:
                self.add_view(
                    PlayerModerationView(self, record.minecraft_uuid),
                    message_id=record.approved_message_id,
                )
            elif record.status == "banned" and record.banned_message_id:
                self.add_view(
                    UnbanView(self, record.minecraft_uuid),
                    message_id=record.banned_message_id,
                )

    async def channel(self, channel_id: int) -> discord.TextChannel:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"El canal {channel_id} no es un canal de texto")
        return channel

    async def ensure_pending_request(self, minecraft_uuid: str) -> None:
        async with self._player_card_lock:
            record = await self.database.get_player(minecraft_uuid)
            if record is None or record.status != "pending" or record.request_message_id:
                return
            await self.post_pending_request(record)

    async def post_pending_request(self, record: PlayerRecord) -> None:
        channel = await self.channel(self.settings.approval_channel_id)
        message = await channel.send(
            embed=self.request_embed(record),
            view=RequestDecisionView(self, record.minecraft_uuid),
        )
        await self.database.set_message_id(
            record.minecraft_uuid, "request_message_id", message.id
        )
        self.add_view(
            RequestDecisionView(self, record.minecraft_uuid), message_id=message.id
        )
        await self.refresh_dashboards()

    async def approve_player(
        self, minecraft_uuid: str, user: discord.abc.User
    ) -> tuple[PlayerRecord, str]:
        current = await self.database.get_player(minecraft_uuid)
        if current is None or current.status != "pending":
            raise RuntimeError("La solicitud ya no está pendiente")
        record = await self.database.set_status(
            minecraft_uuid, "approved", actor_label(user)
        )
        rcon_result = await self._rcon_whitelist_add(record)
        await self._finalize_request_message(record, approved=True)
        await self.post_approved_card(record)
        await self._notify_discord_user(
            record,
            "Tu solicitud de acceso a Mytrix fue aprobada. Abre Minecraft y pulsa JUGAR.",
        )
        await self.refresh_dashboards()
        return record, rcon_result

    async def reject_player(
        self, minecraft_uuid: str, user: discord.abc.User
    ) -> tuple[PlayerRecord, str]:
        current = await self.database.get_player(minecraft_uuid)
        if current is None or current.status != "pending":
            raise RuntimeError("La solicitud ya no está pendiente")
        record = await self.database.set_status(
            minecraft_uuid, "rejected", actor_label(user)
        )
        rcon_result = await self._rcon_whitelist_remove(record)
        await self._finalize_request_message(record, approved=False)
        await self._notify_discord_user(
            record,
            "Tu solicitud de acceso a Mytrix fue rechazada. Puedes volver a solicitar desde el menú.",
        )
        await self.refresh_dashboards()
        return record, rcon_result

    async def ban_player(
        self, minecraft_uuid: str, user: discord.abc.User, reason: str
    ) -> tuple[PlayerRecord, str]:
        current = await self.database.get_player(minecraft_uuid)
        if current is None or current.status != "approved":
            raise RuntimeError("El jugador no está aprobado actualmente")
        record = await self.database.ban_player(
            minecraft_uuid, actor_label(user), reason.strip()
        )
        rcon_result = await self._rcon_ban(record)
        await self._disable_approved_message(record)
        await self.post_banned_card(record)
        await self._notify_discord_user(
            record,
            f"Tu acceso a Mytrix fue bloqueado. Motivo: {record.ban_reason}",
        )
        await self.refresh_dashboards()
        return record, rcon_result

    async def unban_player(
        self, minecraft_uuid: str, user: discord.abc.User
    ) -> tuple[PlayerRecord, str]:
        current = await self.database.get_player(minecraft_uuid)
        if current is None or current.status != "banned":
            raise RuntimeError("El jugador no está baneado actualmente")
        old_ip = player_ip(current)
        record = await self.database.unban_player(minecraft_uuid, actor_label(user))
        rcon_result = await self._rcon_unban(record, old_ip)
        await self._disable_banned_message(record)
        await self.post_approved_card(record)
        await self._notify_discord_user(
            record,
            "Tu acceso a Mytrix fue restablecido.",
        )
        await self.refresh_dashboards()
        return record, rcon_result

    async def post_approved_card(self, record: PlayerRecord) -> None:
        channel = await self.channel(self.settings.approved_channel_id)
        message = await channel.send(
            embed=self.approved_embed(record),
            view=PlayerModerationView(self, record.minecraft_uuid),
        )
        await self.database.set_message_id(
            record.minecraft_uuid, "approved_message_id", message.id
        )
        self.add_view(
            PlayerModerationView(self, record.minecraft_uuid), message_id=message.id
        )

    async def refresh_approved_card(self, record: PlayerRecord) -> None:
        if record.status != "approved" or not record.approved_message_id:
            return
        channel = await self.channel(self.settings.approved_channel_id)
        try:
            message = await channel.fetch_message(record.approved_message_id)
            await message.edit(
                embed=self.approved_embed(record),
                view=PlayerModerationView(self, record.minecraft_uuid),
            )
        except discord.NotFound:
            await self.database.set_message_id(
                record.minecraft_uuid, "approved_message_id", None
            )
            await self.post_approved_card(record)

    async def post_banned_card(self, record: PlayerRecord) -> None:
        channel = await self.channel(self.settings.unban_channel_id)
        message = await channel.send(
            embed=self.banned_embed(record),
            view=UnbanView(self, record.minecraft_uuid),
        )
        await self.database.set_message_id(
            record.minecraft_uuid, "banned_message_id", message.id
        )
        self.add_view(UnbanView(self, record.minecraft_uuid), message_id=message.id)

    async def restore_missing_player_cards(self) -> None:
        for record in await self.database.list_players("pending"):
            if not record.request_message_id:
                await self.ensure_pending_request(record.minecraft_uuid)
        for record in await self.database.list_players("approved"):
            if not record.approved_message_id:
                await self.post_approved_card(record)
        for record in await self.database.list_players("banned"):
            if not record.banned_message_id:
                await self.post_banned_card(record)

    async def _finalize_request_message(
        self, record: PlayerRecord, approved: bool
    ) -> None:
        if not record.request_message_id:
            return
        channel = await self.channel(self.settings.approval_channel_id)
        try:
            message = await channel.fetch_message(record.request_message_id)
            embed = self.request_embed(record)
            embed.color = discord.Color.green() if approved else discord.Color.red()
            embed.title = (
                "Solicitud aprobada" if approved else "Solicitud rechazada"
            )
            await message.edit(embed=embed, view=DisabledDecisionView(approved))
        except discord.NotFound:
            pass

    async def _disable_approved_message(self, record: PlayerRecord) -> None:
        if not record.approved_message_id:
            return
        channel = await self.channel(self.settings.approved_channel_id)
        try:
            message = await channel.fetch_message(record.approved_message_id)
            await message.edit(
                embed=self.banned_embed(record),
                view=DisabledModerationView("BANEADO"),
            )
        except discord.NotFound:
            pass

    async def _disable_banned_message(self, record: PlayerRecord) -> None:
        if not record.banned_message_id:
            return
        channel = await self.channel(self.settings.unban_channel_id)
        try:
            message = await channel.fetch_message(record.banned_message_id)
            embed = self.approved_embed(record)
            embed.title = "Jugador desbaneado"
            await message.edit(embed=embed, view=DisabledModerationView("DESBANEADO"))
        except discord.NotFound:
            pass

    async def _notify_discord_user(self, record: PlayerRecord, text: str) -> None:
        if not record.discord_id.isdigit():
            return
        try:
            user = self.get_user(int(record.discord_id)) or await self.fetch_user(
                int(record.discord_id)
            )
            await user.send(text)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            LOGGER.info("No se pudo enviar DM a Discord ID %s", record.discord_id)

    async def _rcon_whitelist_add(self, record: PlayerRecord) -> str:
        if not self.settings.rcon_sync_whitelist:
            return "La whitelist RCON está desactivada."
        if self.rcon is None:
            return "RCON no está configurado; la API seguirá aplicando el acceso."
        try:
            result = await self.rcon.command(f"whitelist add {record.minecraft_name}")
            return f"Whitelist sincronizada: {result or 'OK'}"
        except (OSError, RconError) as exc:
            LOGGER.warning("Falló whitelist add por RCON: %s", exc)
            return f"Falló la whitelist por RCON ({exc}); la API seguirá aplicando el acceso."

    async def _rcon_whitelist_remove(self, record: PlayerRecord) -> str:
        if not self.settings.rcon_sync_whitelist:
            return "La whitelist RCON está desactivada."
        if self.rcon is None:
            return "RCON no está configurado; la API seguirá aplicando el bloqueo."
        try:
            result = await self.rcon.command(f"whitelist remove {record.minecraft_name}")
            return f"Whitelist actualizada: {result or 'OK'}"
        except (OSError, RconError) as exc:
            LOGGER.warning("Falló whitelist remove por RCON: %s", exc)
            return f"Falló la whitelist por RCON ({exc}); la API seguirá aplicando el bloqueo."

    async def _rcon_ban(self, record: PlayerRecord) -> str:
        ip = normalize_player_ip(player_ip(record))
        if self.rcon is None:
            return "RCON no está configurado; el bloqueo por UUID/API sí quedó activo."

        results: list[str] = []
        if self.settings.rcon_sync_whitelist:
            try:
                result = await self.rcon.command(f"whitelist remove {record.minecraft_name}")
                results.append(f"whitelist: {result or 'OK'}")
            except (OSError, RconError) as exc:
                LOGGER.warning("Falló whitelist remove al banear: %s", exc)
                results.append(f"whitelist falló: {exc}")

        safe_reason = " ".join(record.ban_reason.split())[:200] or "Bloqueado desde Discord"
        if ip is None:
            results.append("sin IP válida para ban-ip")
        else:
            try:
                result = await self.rcon.command(f"ban-ip {ip} {safe_reason}")
                results.append(f"ban-ip: {result or 'OK'}")
            except (OSError, RconError) as exc:
                LOGGER.warning("Falló ban-ip por RCON: %s", exc)
                results.append(f"ban-ip falló: {exc}")

        # El bloqueo por UUID/API impide futuras conexiones. El kick también
        # expulsa al jugador si ya estaba conectado cuando se pulsa el botón.
        try:
            result = await self.rcon.command(f"kick {record.minecraft_name} {safe_reason}")
            results.append(f"kick: {result or 'OK'}")
        except (OSError, RconError) as exc:
            LOGGER.warning("Falló kick por RCON: %s", exc)
            results.append(f"kick falló: {exc}")
        return "; ".join(results) + ". El bloqueo por UUID/API está activo."

    async def _rcon_unban(self, record: PlayerRecord, ip: str) -> str:
        normalized_ip = normalize_player_ip(ip)
        if self.rcon is None:
            return "RCON no está configurado; el acceso por UUID/API fue restaurado."

        results: list[str] = []
        if normalized_ip is not None:
            try:
                result = await self.rcon.command(f"pardon-ip {normalized_ip}")
                results.append(f"pardon-ip: {result or 'OK'}")
            except (OSError, RconError) as exc:
                LOGGER.warning("Falló pardon-ip por RCON: %s", exc)
                results.append(f"pardon-ip falló: {exc}")
        else:
            results.append("sin IP válida para pardon-ip")

        if self.settings.rcon_sync_whitelist:
            try:
                result = await self.rcon.command(f"whitelist add {record.minecraft_name}")
                results.append(f"whitelist: {result or 'OK'}")
            except (OSError, RconError) as exc:
                LOGGER.warning("Falló whitelist add al desbanear: %s", exc)
                results.append(f"whitelist falló: {exc}")
        return "; ".join(results) + ". El acceso por UUID/API fue restaurado."

    async def refresh_dashboards(self) -> None:
        async with self._dashboard_lock:
            counts = await self.database.counts()
            server_address = await self.database.get_server_address()
            await self._upsert_panel(
                "approval_dashboard",
                self.settings.approval_channel_id,
                self.dashboard_embed(
                    "Solicitudes de acceso",
                    "Las nuevas solicitudes aparecen debajo con botones Aprobar y Rechazar.",
                    counts,
                    server_address,
                ),
                None,
            )
            await self._upsert_panel(
                "approved_dashboard",
                self.settings.approved_channel_id,
                self.dashboard_embed(
                    "Jugadores aprobados",
                    "Cada jugador aprobado aparece debajo con un botón para banear su IP.",
                    counts,
                    server_address,
                ),
                None,
            )
            await self._upsert_panel(
                "unban_dashboard",
                self.settings.unban_channel_id,
                self.dashboard_embed(
                    "Jugadores baneados",
                    "Cada jugador baneado aparece debajo con un botón para desbanear.",
                    counts,
                    server_address,
                ),
                None,
            )
            await self._upsert_panel(
                "server_config_dashboard",
                self.settings.server_config_channel_id,
                self.server_config_embed(server_address),
                ServerConfigView(self),
            )

    async def _upsert_panel(
        self,
        panel_key: str,
        channel_id: int,
        embed: discord.Embed,
        view: discord.ui.View | None,
    ) -> None:
        channel = await self.channel(channel_id)
        existing = await self.database.get_panel_message(panel_key)
        if existing:
            existing_channel_id, message_id = existing
            try:
                existing_channel = await self.channel(existing_channel_id)
                message = await existing_channel.fetch_message(message_id)
                await message.edit(embed=embed, view=view)
                return
            except (discord.NotFound, discord.Forbidden):
                pass

        message = await channel.send(embed=embed, view=view)
        await self.database.set_panel_message(panel_key, channel.id, message.id)

    @staticmethod
    def request_embed(record: PlayerRecord) -> discord.Embed:
        embed = discord.Embed(
            title="Nueva solicitud de acceso",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Minecraft", value=record.minecraft_name, inline=True)
        embed.add_field(name="ID de usuario (UUID)", value=f"`{record.minecraft_uuid}`", inline=False)
        embed.add_field(name="IP de solicitud", value=f"`{client_ip_label(record)}`", inline=True)
        embed.add_field(name="Estado", value=record.status, inline=True)
        embed.set_footer(text=f"Solicitada: {record.requested_at or 'desconocido'}")
        return embed

    @staticmethod
    def approved_embed(record: PlayerRecord) -> discord.Embed:
        embed = discord.Embed(
            title="Jugador aprobado",
            color=discord.Color.green(),
        )
        embed.add_field(name="Minecraft", value=record.minecraft_name, inline=True)
        embed.add_field(name="ID de usuario (UUID)", value=f"`{record.minecraft_uuid}`", inline=False)
        embed.add_field(name="IP cliente/API", value=f"`{client_ip_label(record)}`", inline=True)
        embed.add_field(name="IP vista por servidor", value=f"`{server_ip_label(record)}`", inline=True)
        embed.add_field(name="Aprobado por", value=record.decided_by or "Desconocido", inline=False)
        return embed

    @staticmethod
    def banned_embed(record: PlayerRecord) -> discord.Embed:
        embed = discord.Embed(
            title="Jugador baneado",
            color=discord.Color.red(),
        )
        embed.add_field(name="Minecraft", value=record.minecraft_name, inline=True)
        embed.add_field(name="ID de usuario (UUID)", value=f"`{record.minecraft_uuid}`", inline=False)
        embed.add_field(name="IP baneada/RCON", value=f"`{player_ip(record)}`", inline=True)
        embed.add_field(name="IP vista por servidor", value=f"`{server_ip_label(record)}`", inline=True)
        embed.add_field(name="Motivo", value=record.ban_reason or "Sin motivo", inline=False)
        embed.add_field(name="Baneado por", value=record.banned_by or "Desconocido", inline=False)
        return embed

    @staticmethod
    def dashboard_embed(
        title: str,
        description: str,
        counts: dict[str, int],
        server_address: str,
    ) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
        embed.add_field(name="Pendientes", value=str(counts["pending"]), inline=True)
        embed.add_field(name="Aprobados", value=str(counts["approved"]), inline=True)
        embed.add_field(name="Baneados", value=str(counts["banned"]), inline=True)
        embed.add_field(name="Rechazados", value=str(counts["rejected"]), inline=True)
        embed.add_field(
            name="Servidor actual",
            value=f"`{server_address}`" if server_address else "No configurado",
            inline=False,
        )
        return embed

    @staticmethod
    def server_config_embed(server_address: str) -> discord.Embed:
        embed = discord.Embed(
            title="Configuración de IP del servidor",
            description=(
                "Esta dirección se envía automáticamente a todos los clientes aprobados. "
                "Usa los botones para agregar, editar o remover."
            ),
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="Dirección actual",
            value=f"`{server_address}`" if server_address else "No configurada",
            inline=False,
        )
        return embed


@app_commands.guild_only()
@app_commands.command(name="actualizar_paneles", description="Reconstruye los paneles de Mytrix")
async def refresh_panels_command(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    bot = interaction.client
    if not isinstance(bot, MitryxBot):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    await bot.refresh_dashboards()
    await bot.restore_missing_player_cards()
    await interaction.followup.send("Paneles actualizados.", ephemeral=True)


@app_commands.guild_only()
@app_commands.command(name="estado_mytrix", description="Muestra el estado del sistema de acceso")
async def status_command(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    bot = interaction.client
    if not isinstance(bot, MitryxBot):
        return
    counts = await bot.database.counts()
    address = await bot.database.get_server_address()
    await interaction.response.send_message(
        embed=bot.dashboard_embed(
            "Estado de Mytrix",
            "Resumen del sistema de acceso.",
            counts,
            address,
        ),
        ephemeral=True,
    )


MitryxBot.tree_commands = (refresh_panels_command, status_command)


def register_commands(bot: MitryxBot) -> None:
    for command in MitryxBot.tree_commands:
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)



def normalize_player_ip(value: str) -> str | None:
    value = value.strip()
    if not value or value == "No registrada":
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None

def validate_server_address(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 255 or not SERVER_ADDRESS_RE.fullmatch(value):
        raise ValueError("Usa un dominio o IP válida, opcionalmente con puerto.")

    host, sep, raw_port = value.rpartition(":")
    if sep and raw_port.isdigit():
        port = int(raw_port)
        if port < 1 or port > 65535:
            raise ValueError("El puerto debe estar entre 1 y 65535.")
        candidate_host = host
    else:
        candidate_host = value

    if not candidate_host:
        raise ValueError("Falta la IP o dominio.")

    try:
        ipaddress.ip_address(candidate_host)
    except ValueError:
        if candidate_host.startswith(".") or candidate_host.endswith(".") or ".." in candidate_host:
            raise ValueError("El dominio no es válido.") from None

    return value
