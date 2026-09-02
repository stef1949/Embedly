from __future__ import annotations

import logging
import sqlite3
from typing import Awaitable, Callable, Optional, Protocol

import discord

from persistence import MessageOwnership
from security import can_manage_bot_message
from social_cards import ABOUT_EMBEDLY_URL, SocialPost, placeholder_post
from tiktok_handler import TIKTOK_ABOUT_URL, TikTokPost, escape_discord_text

logger = logging.getLogger(__name__)

_IsAdminFn = Callable[[int], bool]
_FetchUserFn = Callable[[int], Awaitable[discord.User]]


class ViewState(Protocol):
    def get_message_ownership(
        self,
        message_id: int,
        channel_id: int,
        guild_id: int | None,
    ) -> MessageOwnership | None: ...

    def delete_message_ownership(self, message_id: int) -> bool: ...

_is_admin: Optional[_IsAdminFn] = None
_user_emulation_preferences: Optional[dict[int, bool]] = None
_default_emulation: bool = True
_fetch_user: Optional[_FetchUserFn] = None
_state: ViewState | None = None


def configure_view_context(
    *,
    is_admin: _IsAdminFn,
    user_emulation_preferences: dict[int, bool],
    default_emulation: bool,
    fetch_user: _FetchUserFn,
    state: ViewState,
) -> None:
    global _is_admin, _user_emulation_preferences, _default_emulation, _fetch_user, _state
    _is_admin = is_admin
    _user_emulation_preferences = user_emulation_preferences
    _default_emulation = default_emulation
    _fetch_user = fetch_user
    _state = state


def _message_coordinates(
    message: discord.Message,
    interaction: discord.Interaction,
) -> tuple[int, int, int | None] | None:
    message_id = getattr(message, "id", None)
    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None) or getattr(interaction, "channel_id", None)
    guild = getattr(message, "guild", None)
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        guild_id = getattr(interaction, "guild_id", None)
    if not isinstance(message_id, int) or message_id <= 0:
        return None
    if not isinstance(channel_id, int) or channel_id <= 0:
        return None
    if guild_id is not None and (not isinstance(guild_id, int) or guild_id <= 0):
        return None
    return message_id, channel_id, guild_id


def _lookup_ownership(
    message: discord.Message,
    interaction: discord.Interaction,
) -> MessageOwnership | None:
    if _state is None:
        return None
    coordinates = _message_coordinates(message, interaction)
    if coordinates is None:
        return None
    try:
        return _state.get_message_ownership(*coordinates)
    except (sqlite3.Error, LookupError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Message ownership lookup failed (%s)", type(exc).__name__)
        return None


def _fallback_owner_id(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


async def _safe_ephemeral_response(interaction: discord.Interaction, content: str) -> None:
    await interaction.response.send_message(
        content,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BaseControlView(discord.ui.View):
    def __init__(self, timeout: float | None = 604800):
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
        ownership = _lookup_ownership(message, interaction)
        if ownership is not None:
            return ownership.original_author_id
        return _fallback_owner_id(self.original_author_id)

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
            if _state is not None:
                try:
                    _state.delete_message_ownership(interaction.message.id)
                except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Could not delete ownership record (%s)", type(exc).__name__)
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
    def __init__(self, original_url: str, timeout: float | None = 604800):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Open Link", style=discord.ButtonStyle.link, url=original_url))

    async def _handle_delete(self, interaction: discord.Interaction):
        try:
            author_id = self._resolve_author_id(interaction.message, interaction)
            if not self._can_manage(interaction, author_id):
                await interaction.response.send_message("You are not allowed to delete this message.", ephemeral=True)
                return
            await interaction.message.delete()
            if _state is not None:
                try:
                    _state.delete_message_ownership(interaction.message.id)
                except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Could not delete ownership record (%s)", type(exc).__name__)
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


class YouTubeControlView(MediaControlView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="youtube_delete_button")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_delete(interaction)


class _TikTokInformationButton(discord.ui.Button["TikTokCardView"]):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="ℹ️",
            custom_id="embedly:tiktok:information",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.show_information(interaction)


class _TikTokTranscriptButton(discord.ui.Button["TikTokCardView"]):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Transcript",
            custom_id="embedly:tiktok:transcript",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.show_transcript(interaction)


class TikTokCardView(discord.ui.LayoutView):
    """TikTok-only Components V2 card with persistent, fail-closed callbacks."""

    def __init__(
        self,
        *,
        post: TikTokPost,
        media: str | discord.File,
        icon: str,
        timeout: float | None = 604800,
    ) -> None:
        super().__init__(timeout=timeout)
        self.message: discord.Message | None = None
        self.original_author_id: int | None = None
        self.details = post.information_text
        self.transcript = post.transcript

        gallery_description = None
        if post.description:
            gallery_description = escape_discord_text(post.description)[:256]

        links = (
            f"[Open in TikTok]({post.original_url})"
            f"  •  [About Embedly]({TIKTOK_ABOUT_URL})"
        )
        controls = discord.ui.ActionRow(
            _TikTokInformationButton(),
            _TikTokTranscriptButton(),
        )
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"{icon} {post.creator_display}"),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(media=media, description=gallery_description)
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(links),
            discord.ui.TextDisplay(post.engagement_text),
            controls,
            accent_colour=0x25F4EE,
        )
        self.add_item(container)

    @classmethod
    def persistent_placeholder(cls) -> "TikTokCardView":
        post = TikTokPost(
            display_name="TikTok creator",
            handle=None,
            creator_url="https://www.tiktok.com/",
            original_url="https://www.tiktok.com/",
            description=None,
            duration_seconds=None,
            upload_date=None,
            width=None,
            height=None,
            like_count=None,
            comment_count=None,
            repost_count=None,
            transcript=None,
        )
        return cls(
            post=post,
            media="https://www.tiktok.com/favicon.ico",
            icon="🎵",
            timeout=None,
        )

    async def on_timeout(self) -> None:
        try:
            for item in self.walk_children():
                if hasattr(item, "disabled"):
                    item.disabled = True
            if self.message:
                await self.message.edit(view=self)
        except Exception as exc:
            logger.debug("TikTok card timeout edit skipped: %s", exc)

    def _ownership(self, interaction: discord.Interaction) -> MessageOwnership | None:
        message = interaction.message
        if message is None:
            return None
        return _lookup_ownership(message, interaction)

    def _author_id(
        self,
        interaction: discord.Interaction,
        ownership: MessageOwnership | None,
    ) -> int | None:
        if ownership is not None:
            return ownership.original_author_id
        return _fallback_owner_id(self.original_author_id)

    def _can_use(
        self,
        interaction: discord.Interaction,
        ownership: MessageOwnership | None,
    ) -> bool:
        if _is_admin is None:
            return False
        return can_manage_bot_message(
            interaction,
            self._author_id(interaction, ownership),
            is_bot_admin=_is_admin,
        )

    async def show_information(self, interaction: discord.Interaction) -> None:
        ownership = self._ownership(interaction)
        if not self._can_use(interaction, ownership):
            await _safe_ephemeral_response(interaction, "You are not allowed to use this control.")
            return
        details = ownership.details if ownership is not None else self.details
        await _safe_ephemeral_response(interaction, details or "Post details are unavailable.")

    async def show_transcript(self, interaction: discord.Interaction) -> None:
        ownership = self._ownership(interaction)
        if not self._can_use(interaction, ownership):
            await _safe_ephemeral_response(interaction, "You are not allowed to use this control.")
            return
        transcript = ownership.transcript if ownership is not None else self.transcript
        await _safe_ephemeral_response(
            interaction,
            transcript or "Transcript unavailable for this post",
        )


class _SocialInformationButton(discord.ui.Button["SocialMediaCardView"]):
    def __init__(self, platform_key: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="ℹ️",
            custom_id=f"embedly:{platform_key}:information",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.show_information(interaction)


class _SocialTranscriptButton(discord.ui.Button["SocialMediaCardView"]):
    def __init__(self, platform_key: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Transcript",
            custom_id=f"embedly:{platform_key}:transcript",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.show_transcript(interaction)


class SocialMediaCardView(discord.ui.LayoutView):
    """Components V2 card shared by non-TikTok social sources."""

    def __init__(
        self,
        *,
        post: SocialPost,
        icon: str,
        media: str | discord.File | None = None,
        spoiler: bool = False,
        include_details: bool = False,
        timeout: float | None = 604800,
    ) -> None:
        super().__init__(timeout=timeout)
        self.message: discord.Message | None = None
        self.original_author_id: int | None = None
        self.details = post.information_text
        self.transcript = post.transcript

        children: list[discord.ui.Item] = [
            discord.ui.TextDisplay(f"{icon} {post.creator_display}"),
        ]
        if media is not None:
            gallery_description = None
            if post.description:
                gallery_description = escape_discord_text(post.description)[:256]
            children.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(media=media, description=gallery_description)
                )
            )
        children.extend(
            (
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"[{post.open_label}]({post.original_url})"
                    f"  •  [About Embedly]({ABOUT_EMBEDLY_URL})"
                ),
                discord.ui.TextDisplay(post.engagement_text),
            )
        )
        if include_details and post.detail_summary:
            children.append(discord.ui.TextDisplay(post.detail_summary))
        children.append(
            discord.ui.ActionRow(
                _SocialInformationButton(post.platform_key),
                _SocialTranscriptButton(post.platform_key),
            )
        )
        self.add_item(
            discord.ui.Container(
                *children,
                accent_colour=post.accent_colour,
                spoiler=spoiler,
            )
        )

    async def on_timeout(self) -> None:
        try:
            for item in self.walk_children():
                if hasattr(item, "disabled"):
                    item.disabled = True
            if self.message:
                await self.message.edit(view=self)
        except Exception as exc:
            logger.debug("Social card timeout edit skipped: %s", exc)

    def _ownership(self, interaction: discord.Interaction) -> MessageOwnership | None:
        message = interaction.message
        if message is None:
            return None
        return _lookup_ownership(message, interaction)

    def _can_use(
        self,
        interaction: discord.Interaction,
        ownership: MessageOwnership | None,
    ) -> bool:
        if _is_admin is None:
            return False
        author_id = ownership.original_author_id if ownership is not None else _fallback_owner_id(
            self.original_author_id
        )
        return can_manage_bot_message(
            interaction,
            author_id,
            is_bot_admin=_is_admin,
        )

    async def show_information(self, interaction: discord.Interaction) -> None:
        ownership = self._ownership(interaction)
        if not self._can_use(interaction, ownership):
            await _safe_ephemeral_response(interaction, "You are not allowed to use this control.")
            return
        details = ownership.details if ownership is not None else self.details
        await _safe_ephemeral_response(interaction, details or "Post details are unavailable.")

    async def show_transcript(self, interaction: discord.Interaction) -> None:
        ownership = self._ownership(interaction)
        if not self._can_use(interaction, ownership):
            await _safe_ephemeral_response(interaction, "You are not allowed to use this control.")
            return
        transcript = ownership.transcript if ownership is not None else self.transcript
        await _safe_ephemeral_response(
            interaction,
            transcript or "Transcript unavailable for this post",
        )


class InstagramCardView(SocialMediaCardView):
    def __init__(
        self,
        *,
        post: SocialPost,
        media: str | discord.File,
        icon: str,
        include_details: bool = False,
        timeout: float | None = 604800,
    ) -> None:
        super().__init__(
            post=post,
            media=media,
            icon=icon,
            include_details=include_details,
            timeout=timeout,
        )

    @classmethod
    def persistent_placeholder(cls) -> "InstagramCardView":
        return cls(
            post=placeholder_post("instagram"),
            media="https://www.instagram.com/favicon.ico",
            icon="📸",
            timeout=None,
        )


class YouTubeCardView(SocialMediaCardView):
    def __init__(
        self,
        *,
        post: SocialPost,
        media: str | discord.File,
        icon: str,
        include_details: bool = False,
        timeout: float | None = 604800,
    ) -> None:
        super().__init__(
            post=post,
            media=media,
            icon=icon,
            include_details=include_details,
            timeout=timeout,
        )

    @classmethod
    def persistent_placeholder(cls) -> "YouTubeCardView":
        return cls(
            post=placeholder_post("youtube"),
            media="https://www.youtube.com/favicon.ico",
            icon="▶️",
            timeout=None,
        )


class TwitterCardView(SocialMediaCardView):
    def __init__(
        self,
        *,
        post: SocialPost,
        icon: str,
        spoiler: bool = False,
        timeout: float | None = 604800,
    ) -> None:
        super().__init__(post=post, icon=icon, spoiler=spoiler, timeout=timeout)

    @classmethod
    def persistent_placeholder(cls) -> "TwitterCardView":
        return cls(
            post=placeholder_post("twitter"),
            icon="𝕏",
            timeout=None,
        )
