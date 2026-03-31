from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import discord

from security import can_manage_bot_message, extract_author_id

logger = logging.getLogger(__name__)

_IsAdminFn = Callable[[int], bool]
_FetchUserFn = Callable[[int], Awaitable[discord.User]]

_is_admin: Optional[_IsAdminFn] = None
_user_emulation_preferences: Optional[dict[int, bool]] = None
_default_emulation: bool = True
_fetch_user: Optional[_FetchUserFn] = None


def configure_view_context(
    *,
    is_admin: _IsAdminFn,
    user_emulation_preferences: dict[int, bool],
    default_emulation: bool,
    fetch_user: _FetchUserFn,
) -> None:
    global _is_admin, _user_emulation_preferences, _default_emulation, _fetch_user
    _is_admin = is_admin
    _user_emulation_preferences = user_emulation_preferences
    _default_emulation = default_emulation
    _fetch_user = fetch_user


class BaseControlView(discord.ui.View):
    def __init__(self, timeout: float = 604800):
        super().__init__(timeout=timeout)
        self.message = None
        self.original_author_id = None

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
            if self.message:
                await self.message.edit(view=self)
        except Exception as e:
            logger.debug("View timeout edit skipped: %s", e)

    def _resolve_author_id(self, message: discord.Message, interaction: discord.Interaction) -> Optional[int]:
        author_id = extract_author_id(message, self.original_author_id)
        if not author_id and getattr(message, "webhook_id", None):
            author_id = interaction.user.id
        return author_id

    def _can_manage(self, interaction: discord.Interaction, author_id: Optional[int]) -> bool:
        if _is_admin is None:
            return False
        return can_manage_bot_message(interaction, author_id, is_bot_admin=_is_admin)


class MessageControlView(BaseControlView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="delete_button")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            author_id = self._resolve_author_id(interaction.message, interaction)
            if not self._can_manage(interaction, author_id):
                await interaction.response.send_message("You are not allowed to delete this message.", ephemeral=True)
                return
            await interaction.message.delete()
            await interaction.response.send_message("Message deleted.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message already deleted.", ephemeral=True)
        except Exception as e:
            logger.error("Delete button error: %s", e)
            await interaction.response.send_message("Error processing request.", ephemeral=True)

    @discord.ui.button(label="Toggle Emulation", style=discord.ButtonStyle.secondary, custom_id="toggle_emulation")
    async def toggle_emulation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if _user_emulation_preferences is None or _is_admin is None:
            await interaction.response.send_message("View context is not initialized.", ephemeral=True)
            return

        author_id = self._resolve_author_id(interaction.message, interaction)
        if not can_manage_bot_message(interaction, author_id, is_bot_admin=_is_admin):
            await interaction.response.send_message("You can only change your own emulation preference.", ephemeral=True)
            return

        user_id_to_toggle = author_id or interaction.user.id
        new_preference = not _user_emulation_preferences.get(user_id_to_toggle, _default_emulation)
        _user_emulation_preferences[user_id_to_toggle] = new_preference

        msg = "Future posts will use your name and avatar." if new_preference else "Future posts will show as coming from the bot with a mention to you."
        if author_id and interaction.user.id != author_id and _fetch_user:
            try:
                user = await _fetch_user(author_id)
                msg = f"Changed {user.name}'s emulation preference. {msg}"
            except Exception:
                msg = f"Changed User {author_id}'s emulation preference. {msg}"
        await interaction.response.send_message(msg, ephemeral=True)


class MediaControlView(BaseControlView):
    def __init__(self, original_url: str, timeout: float = 604800):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Open Link", style=discord.ButtonStyle.link, url=original_url))

    async def _handle_delete(self, interaction: discord.Interaction):
        try:
            author_id = self._resolve_author_id(interaction.message, interaction)
            if not self._can_manage(interaction, author_id):
                await interaction.response.send_message("You are not allowed to delete this message.", ephemeral=True)
                return
            await interaction.message.delete()
            await interaction.response.send_message("Message deleted.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message already deleted.", ephemeral=True)
        except Exception as e:
            logger.error("Media delete button error: %s", e)
            await interaction.response.send_message("Error processing request.", ephemeral=True)


class TikTokControlView(MediaControlView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="tiktok_delete_button")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_delete(interaction)


class InstagramControlView(MediaControlView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="instagram_delete_button")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_delete(interaction)
