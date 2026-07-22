from __future__ import annotations

import asyncio
import logging
from typing import Callable

import discord

from utils.urls import rewrite_tiktok_url

logger = logging.getLogger(__name__)


async def try_kktiktok_embed(
    *,
    message: discord.Message,
    source_url: str,
    view_factory: Callable[[str], discord.ui.View],
    embed_wait_seconds: float = 3.0,
) -> bool:
    """Post a fixed TikTok link and return whether Discord rendered an embed for it."""
    rewritten_url = rewrite_tiktok_url(source_url)
    view = view_factory(source_url)
    view.original_author_id = message.author.id

    try:
        sent_message = await message.channel.send(
            content=f"🎵 **TikTok shared by <@{message.author.id}>:**\n{rewritten_url}",
            view=view,
        )
        view.message = sent_message

        if embed_wait_seconds > 0:
            await asyncio.sleep(embed_wait_seconds)

        rendered_message = sent_message
        try:
            rendered_message = await message.channel.fetch_message(sent_message.id)
        except (AttributeError, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.debug("Could not refresh kktiktok message %s: %s", sent_message.id, exc)

        if rendered_message.embeds:
            logger.info("TikTok embed rendered for %s", source_url)
            return True

        logger.warning("TikTok embed did not render for %s; using download fallback", source_url)
        await _delete_silently(sent_message)
        return False
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not send TikTok embed link for %s; using download fallback: %s", source_url, exc)
        return False


async def _delete_silently(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
