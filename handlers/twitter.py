from __future__ import annotations

import logging
import discord

from utils.urls import RewriteResult
from views import MessageControlView

logger = logging.getLogger(__name__)


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
            webhook = None
            try:
                webhook = await message.channel.create_webhook(name="TempWebhook")
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
            finally:
                if webhook:
                    try:
                        await webhook.delete()
                    except discord.HTTPException as exc:
                        logger.warning("Webhook delete failed for %s: %s", message.id, exc)

    user_id_mention = f"<@{message.author.id}>"
    return await message.channel.send(f"**Link shared by {user_id_mention}:**\n{content}", view=view)
