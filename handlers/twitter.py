from __future__ import annotations

import logging
import discord

from utils.urls import RewriteResult
from views import MessageControlView

logger = logging.getLogger(__name__)

WEBHOOK_NAME = "Embedly"
LEGACY_WEBHOOK_NAMES = {"TempWebhook"}
_webhook_cache: dict[int, discord.Webhook] = {}


async def send_twitter_rewrite_message(
    *,
    message: discord.Message,
    rewrite_result: RewriteResult,
    should_emulate: bool,
) -> int:
    links_processed = 0

    if rewrite_result.spoiler_urls:
        links_processed += len(rewrite_result.spoiler_urls)
        spoiler_view = MessageControlView(timeout=604800)
        spoiler_view.original_author_id = message.author.id
        spoiler_response = "\n".join(rewrite_result.spoiler_urls)
        embed = discord.Embed(
            title="Spoiler Embed",
            description="This tweet is hidden behind a spoiler. Click to reveal.",
            color=0x1DA1F2,
        )
        embed.add_field(name="Link", value=spoiler_response, inline=False)
        sent = await message.channel.send(content="||spoiler||", embed=embed, view=spoiler_view)
        spoiler_view.message = sent

    if rewrite_result.rewritten_urls:
        links_processed += len(rewrite_result.rewritten_urls)
        view = MessageControlView(timeout=604800)
        view.original_author_id = message.author.id
        response = "\n".join(rewrite_result.rewritten_urls)
        sent = await _send_with_optional_emulation(message=message, content=response, view=view, emulate=should_emulate)
        view.message = sent

    return links_processed


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
