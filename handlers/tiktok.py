from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sqlite3
from collections.abc import Callable, Sequence

import discord

from handlers.media import (
    MediaProcessingConfig,
    cleanup_file,
    delete_message_silently,
    maybe_delete_original_message,
    run_blocking,
)
from services.downloaders import DownloadResult
from tiktok_handler import extract_tiktok_post
from utils.urls import rewrite_tiktok_url
from views import TikTokCardView

logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
OwnershipRecorder = Callable[..., object]


async def process_tiktok_links(
    *,
    message: discord.Message,
    urls: Sequence[str],
    url_validator: Callable[[str], str],
    downloader: Callable[..., DownloadResult],
    compressor: Callable[..., str | None],
    fallback_view_factory: Callable[[str], discord.ui.View],
    ownership_recorder: OwnershipRecorder,
    semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig,
    icon: str,
    delete_source: bool = True,
) -> int:
    """Replace TikTok links with native Components V2 cards.

    Each replacement is sent as a reply and persisted before the source
    message is deleted. Failed downloads or card sends use a validated tnktok
    link instead.
    """

    processed = 0
    for source_url in urls:
        validated_url = url_validator(source_url)
        native_sent = await _send_native_card(
            message=message,
            validated_url=validated_url,
            downloader=downloader,
            compressor=compressor,
            ownership_recorder=ownership_recorder,
            semaphore=semaphore,
            config=config,
            icon=icon,
        )
        if native_sent:
            processed += 1
            continue

        fallback_sent = await send_tiktok_fallback_link(
            message=message,
            source_url=validated_url,
            view_factory=fallback_view_factory,
            ownership_recorder=ownership_recorder,
        )
        if fallback_sent:
            processed += 1

    if delete_source and urls and processed == len(urls):
        await maybe_delete_original_message(message, "TikTok")
    return processed


async def _send_native_card(
    *,
    message: discord.Message,
    validated_url: str,
    downloader: Callable[..., DownloadResult],
    compressor: Callable[..., str | None],
    ownership_recorder: OwnershipRecorder,
    semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig,
    icon: str,
) -> bool:
    processing_message = None
    filepath: str | None = None
    original_filepath: str | None = None
    sent_message: discord.Message | None = None

    try:
        processing_message = await message.channel.send(
            "⏳ Downloading TikTok video…",
            allowed_mentions=NO_MENTIONS,
        )
        async with semaphore:
            result = await run_blocking(
                downloader,
                validated_url,
                output_folder=config.temp_directory,
                timeout_seconds=config.ytdlp_timeout_seconds,
            )

        if not result.success or not result.filepath:
            logger.warning("TikTok download failed; using link fallback")
            return False

        original_filepath = result.filepath
        filepath = result.filepath
        if os.path.getsize(filepath) > config.upload_limit_bytes:
            compressed_path = await run_blocking(
                compressor,
                filepath,
                config.upload_limit_bytes,
                ffprobe_timeout_seconds=config.ffprobe_timeout_seconds,
                ffmpeg_timeout_seconds=config.ffmpeg_timeout_seconds,
                headroom_ratio=config.ffmpeg_headroom_ratio,
                use_nvidia_gpu=config.use_nvidia_gpu,
                timeout_seconds=config.ffmpeg_timeout_seconds,
            )
            if not compressed_path:
                logger.warning("TikTok compression failed; using link fallback")
                return False
            filepath = compressed_path
            if os.path.getsize(filepath) > config.upload_limit_bytes:
                logger.warning("Compressed TikTok video exceeds the upload limit; using link fallback")
                return False

        post = extract_tiktok_post(result, validated_url)
        attachment = discord.File(filepath, filename=_attachment_filename(filepath))
        try:
            view = TikTokCardView(post=post, media=attachment, icon=icon, timeout=604800)
            view.original_author_id = message.author.id
            await delete_message_silently(processing_message)
            processing_message = None
            sent_message = await message.reply(
                file=attachment,
                view=view,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            view.message = sent_message
        finally:
            attachment.close()

        try:
            await _record_ownership(
                ownership_recorder,
                sent_message,
                source_message=message,
                message_type="tiktok_card",
                details=post.information_text,
                transcript=post.transcript,
            )
        except (sqlite3.Error, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error("TikTok ownership persistence failed (%s); using link fallback", type(exc).__name__)
            await delete_message_silently(sent_message)
            return False
        return True
    except asyncio.TimeoutError:
        logger.warning("TikTok processing timed out; using link fallback")
        return False
    except (discord.HTTPException, discord.Forbidden, OSError, TypeError, ValueError) as exc:
        logger.warning("TikTok card could not be created (%s); using link fallback", type(exc).__name__)
        if sent_message is not None:
            await delete_message_silently(sent_message)
        return False
    finally:
        await delete_message_silently(processing_message)
        if filepath:
            cleanup_file(filepath)
        if original_filepath and original_filepath != filepath:
            cleanup_file(original_filepath)


async def send_tiktok_fallback_link(
    *,
    message: discord.Message,
    source_url: str,
    view_factory: Callable[[str], discord.ui.View],
    ownership_recorder: OwnershipRecorder,
) -> bool:
    rewritten_url = rewrite_tiktok_url(source_url)
    view = view_factory(source_url)
    view.original_author_id = message.author.id
    sent_message = None
    try:
        sent_message = await message.reply(
            content=f"🎵 **TikTok link:**\n{rewritten_url}",
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )
        view.message = sent_message
        await _record_ownership(
            ownership_recorder,
            sent_message,
            source_message=message,
            message_type="tiktok_fallback",
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
        logger.warning("Could not send TikTok fallback (%s)", type(exc).__name__)
        if sent_message is not None:
            await _delete_silently(sent_message)
        return False


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

    result = recorder(
        message_id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        original_author_id=author_id,
        message_type=message_type,
        details=details,
        transcript=transcript,
    )
    if inspect.isawaitable(result):
        await result


def _attachment_filename(filepath: str) -> str:
    extension = os.path.splitext(filepath)[1].casefold()
    if extension not in {".mp4", ".mov", ".m4v", ".webm"}:
        extension = ".mp4"
    return f"tiktok_video{extension}"


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
            content=f"🎵 **TikTok link:**\n{rewritten_url}",
            view=view,
            allowed_mentions=NO_MENTIONS,
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
