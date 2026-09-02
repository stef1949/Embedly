from __future__ import annotations

import inspect
import logging
import sqlite3
from collections.abc import Callable

import discord

from social_cards import extract_twitter_post
from utils.urls import RewriteResult
from views import MessageControlView, TwitterCardView

logger = logging.getLogger(__name__)

WEBHOOK_NAME = "Embedly"
LEGACY_WEBHOOK_NAMES = {"TempWebhook"}
_webhook_cache: dict[int, discord.Webhook] = {}
OwnershipRecorder = Callable[..., object]
NO_MENTIONS = discord.AllowedMentions.none()


async def send_twitter_rewrite_message(
    *,
    message: discord.Message,
    rewrite_result: RewriteResult,
    should_emulate: bool,
    icon: str,
    ownership_recorder: OwnershipRecorder,
) -> int:
    links_processed = 0

    for rewritten_url, spoiler in (
        *((url, True) for url in rewrite_result.spoiler_urls),
        *((url, False) for url in rewrite_result.rewritten_urls),
    ):
        native_sent = await _send_native_twitter_card(
            message=message,
            rewritten_url=rewritten_url,
            spoiler=spoiler,
            icon=icon,
            ownership_recorder=ownership_recorder,
        )
        if native_sent:
            links_processed += 1
            continue

        fallback_sent = await _send_legacy_twitter_link(
            message=message,
            rewritten_url=rewritten_url,
            spoiler=spoiler,
            should_emulate=should_emulate,
            ownership_recorder=ownership_recorder,
        )
        if fallback_sent:
            links_processed += 1

    return links_processed


async def _send_native_twitter_card(
    *,
    message: discord.Message,
    rewritten_url: str,
    spoiler: bool,
    icon: str,
    ownership_recorder: OwnershipRecorder,
) -> bool:
    sent: discord.Message | None = None
    try:
        post = extract_twitter_post(rewritten_url)
        view = TwitterCardView(post=post, icon=icon, spoiler=spoiler, timeout=604800)
        view.original_author_id = message.author.id
        sent = await message.reply(
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )
        view.message = sent
        await _record_ownership(
            ownership_recorder,
            sent,
            source_message=message,
            message_type="twitter_card",
            details=post.information_text,
            transcript=post.transcript,
        )
        return True
    except (
        discord.Forbidden,
        discord.HTTPException,
        sqlite3.Error,
        LookupError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        logger.warning("Twitter/X card could not be sent (%s); using rewrite fallback", type(exc).__name__)
        if sent is not None:
            await _delete_message_silently(sent)
        return False


async def _send_legacy_twitter_link(
    *,
    message: discord.Message,
    rewritten_url: str,
    spoiler: bool,
    should_emulate: bool,
    ownership_recorder: OwnershipRecorder,
) -> bool:
    view = MessageControlView(timeout=604800)
    view.original_author_id = message.author.id
    sent: discord.Message | None = None
    try:
        if spoiler:
            embed = discord.Embed(
                title="Spoiler Embed",
                description="This post is hidden behind a spoiler. Click to reveal.",
                color=0x1DA1F2,
            )
            embed.add_field(name="Link", value=rewritten_url, inline=False)
            sent = await message.channel.send(content="||spoiler||", embed=embed, view=view)
        else:
            sent = await _send_with_optional_emulation(
                message=message,
                content=rewritten_url,
                view=view,
                emulate=should_emulate,
            )
        view.message = sent
        await _record_ownership(
            ownership_recorder,
            sent,
            source_message=message,
            message_type="twitter_spoiler" if spoiler else "twitter",
        )
        return True
    except (
        discord.Forbidden,
        discord.HTTPException,
        sqlite3.Error,
        LookupError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        logger.warning("Twitter/X rewrite fallback failed (%s)", type(exc).__name__)
        if sent is not None:
            await _delete_message_silently(sent)
        return False


async def _send_with_optional_emulation(
    *,
    message: discord.Message,
    content: str,
    view: discord.ui.View,
    emulate: bool,
) -> discord.Message:
    if emulate and isinstance(message.channel, discord.TextChannel):
        perms = message.channel.permissions_for(message.guild.me)
        if perms.manage_webhooks:
            try:
                webhook = await _get_or_create_channel_webhook(message.channel, bot_user=message.guild.me)
                if webhook:
                    sent = await webhook.send(
                        content=content,
                        username=message.author.display_name,
                        avatar_url=message.author.display_avatar.url,
                        view=view,
                        wait=True,
                    )
                    return sent
            except discord.HTTPException as exc:
                logger.warning("Webhook send failed for %s: %s", message.id, exc)
                _webhook_cache.pop(message.channel.id, None)

    user_id_mention = f"<@{message.author.id}>"
    return await message.channel.send(f"**Link shared by {user_id_mention}:**\n{content}", view=view)


async def _get_or_create_channel_webhook(
    channel: discord.TextChannel,
    *,
    bot_user: discord.abc.User,
) -> discord.Webhook | None:
    cached = _webhook_cache.get(channel.id)
    if cached:
        return cached

    reusable_webhook = await _find_reusable_webhook(channel, bot_user=bot_user)
    if reusable_webhook:
        _webhook_cache[channel.id] = reusable_webhook
        return reusable_webhook

    try:
        webhook = await channel.create_webhook(name=WEBHOOK_NAME)
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30007:
            logger.info("Channel %s has the maximum number of webhooks; falling back to bot identity", channel.id)
        else:
            logger.warning("Could not create reusable webhook for channel %s: %s", channel.id, exc)
        return None

    _webhook_cache[channel.id] = webhook
    return webhook


async def _find_reusable_webhook(
    channel: discord.TextChannel,
    *,
    bot_user: discord.abc.User,
) -> discord.Webhook | None:
    try:
        webhooks = await channel.webhooks()
    except discord.HTTPException as exc:
        logger.warning("Could not list webhooks for channel %s: %s", channel.id, exc)
        return None

    owned_by_bot = [webhook for webhook in webhooks if _webhook_belongs_to_bot(webhook, bot_user)]
    for webhook in owned_by_bot:
        if webhook.name == WEBHOOK_NAME:
            return webhook

    for webhook in owned_by_bot:
        if webhook.name in LEGACY_WEBHOOK_NAMES:
            return webhook

    return None


def _webhook_belongs_to_bot(webhook: discord.Webhook, bot_user: discord.abc.User) -> bool:
    webhook_user = getattr(webhook, "user", None)
    webhook_user_id = getattr(webhook_user, "id", None)
    return webhook_user_id == bot_user.id


async def _record_ownership(
    recorder: OwnershipRecorder,
    sent_message: discord.Message,
    *,
    source_message: discord.Message,
    message_type: str,
    details: str | None = None,
    transcript: str | None = None,
) -> None:
    message_id = getattr(sent_message, "id", None)
    channel_id = getattr(getattr(sent_message, "channel", None), "id", None)
    if channel_id is None:
        channel_id = getattr(getattr(source_message, "channel", None), "id", None)
    guild_id = getattr(getattr(sent_message, "guild", None), "id", None)
    if guild_id is None:
        guild_id = getattr(getattr(source_message, "guild", None), "id", None)
    author_id = getattr(getattr(source_message, "author", None), "id", None)
    if not all(isinstance(value, int) and value > 0 for value in (message_id, channel_id, author_id)):
        raise ValueError("Discord did not return trusted message ownership coordinates")
    values = {
        "message_id": message_id,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "original_author_id": author_id,
        "message_type": message_type,
    }
    if details is not None:
        values["details"] = details
    if transcript is not None:
        values["transcript"] = transcript
    result = recorder(**values)
    if inspect.isawaitable(result):
        await result


async def _delete_message_silently(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
