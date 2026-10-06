from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import weakref
import discord

from handlers.media import MediaProcessingConfig, process_native_media_links
from services.downloaders import download_media
from services.transcode import compress_video_to_limit
from social_cards import extract_twitter_media_post
from utils.urls import parse_supported_url
from views import TwitterCardView

logger = logging.getLogger(__name__)
WEBHOOK_NAME = "Embedly"
LEGACY_WEBHOOK_NAMES = {"TempWebhook"}
_webhook_cache: dict[int, discord.Webhook] = {}
_webhook_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
_webhook_retry_after: dict[int, float] = {}
WEBHOOK_LIMIT_BACKOFF_SECONDS = 60
NO_MENTIONS = discord.AllowedMentions.none()


async def send_twitter_rewrite_message(
    *, message, rewrite_result, should_emulate, icon, ownership_recorder,
    config=None, semaphore=None, publication_store=None,
):
    config = config or MediaProcessingConfig(tempfile.gettempdir(), 8*1024*1024, 120, 120, 15, .95, False)
    semaphore = semaphore or asyncio.Semaphore(1)
    count = 0
    seen = set()
    for url, spoiler in [*((u, True) for u in rewrite_result.spoiler_urls),
                         *((u, False) for u in rewrite_result.rewritten_urls)]:
        canonical = parse_supported_url(url).url
        if canonical in seen:
            continue
        seen.add(canonical)
        count += await process_native_media_links(
            message=message, urls=[canonical], source_name="Twitter/X", platform_key="twitter",
            icon=icon, url_validator=lambda u: parse_supported_url(u).url,
            downloader=download_media, compressor=compress_video_to_limit,
            post_factory=extract_twitter_media_post, card_view_factory=TwitterCardView,
            ownership_recorder=ownership_recorder, semaphore=semaphore, config=config,
            publication_store=publication_store, should_emulate=should_emulate, spoiler=spoiler,
        )
    return count


async def _send_with_optional_emulation(
    *,
    message: discord.Message,
    content: str | None,
    view: discord.ui.View | discord.ui.LayoutView,
    emulate: bool,
    file: discord.File | None = None,
) -> discord.Message:
    if emulate and isinstance(message.channel, discord.TextChannel):
        perms = message.channel.permissions_for(message.guild.me)
        if perms.manage_webhooks:
            try:
                webhook = await _get_or_create_channel_webhook(message.channel, bot_user=message.guild.me)
                if webhook:
                    # Components V2 cards must be sent without legacy content.
                    content_kwargs = {"content": content} if content is not None else {}
                    sent = await webhook.send(
                        **content_kwargs,
                        username=message.author.display_name,
                        avatar_url=message.author.display_avatar.url,
                        view=view,
                        wait=True,
                        file=file,
                        allowed_mentions=NO_MENTIONS,
                    )
                    return sent
            except (discord.HTTPException, ValueError) as exc:
                logger.warning("Webhook send failed for %s: %s", message.id, exc)
                _webhook_cache.pop(message.channel.id, None)
                raise

    if content is None:
        return await message.reply(file=file, view=view, mention_author=False, allowed_mentions=NO_MENTIONS)

    user_id_mention = f"<@{message.author.id}>"
    return await message.channel.send(f"**Link shared by {user_id_mention}:**\n{content}", view=view)


async def _get_or_create_channel_webhook(
    channel: discord.TextChannel,
    *,
    bot_user: discord.abc.User,
) -> discord.Webhook | None:
    # Keep a strong reference while awaiting; concurrent messages share this lock.
    lock = _webhook_locks.setdefault(channel.id, asyncio.Lock())
    async with lock:
        cached = _webhook_cache.get(channel.id)
        if cached:
            return cached
        now = time.monotonic()
        for channel_id, deadline in list(_webhook_retry_after.items()):
            if deadline <= now:
                del _webhook_retry_after[channel_id]
        if channel.id in _webhook_retry_after:
            return None

        reusable_webhook = await _find_reusable_webhook(channel, bot_user=bot_user)
        if reusable_webhook:
            _webhook_cache[channel.id] = reusable_webhook
            return reusable_webhook

        try:
            webhook = await channel.create_webhook(name=WEBHOOK_NAME)
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == 30007:
                _webhook_retry_after[channel.id] = now + WEBHOOK_LIMIT_BACKOFF_SECONDS
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

    owned_by_bot = [
        webhook for webhook in webhooks
        if _webhook_belongs_to_bot(webhook, bot_user)
        and webhook.type is discord.WebhookType.incoming
        and webhook.token
    ]
    for webhook in owned_by_bot:
        if webhook.name == WEBHOOK_NAME:
            return webhook

    for webhook in owned_by_bot:
        if webhook.name in LEGACY_WEBHOOK_NAMES:
            return webhook

    # Older deployments or administrators may have renamed our webhook.
    # Never use or delete another owner's webhook to make room.
    return next(iter(owned_by_bot), None)


def _webhook_belongs_to_bot(webhook: discord.Webhook, bot_user: discord.abc.User) -> bool:
    webhook_user = getattr(webhook, "user", None)
    webhook_user_id = getattr(webhook_user, "id", None)
    return webhook_user_id == bot_user.id
