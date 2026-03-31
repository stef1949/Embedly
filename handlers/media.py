from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

import discord

from services.downloaders import DownloadResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaProcessingConfig:
    temp_directory: str
    upload_limit_bytes: int
    ytdlp_timeout_seconds: int
    ffmpeg_timeout_seconds: int
    ffprobe_timeout_seconds: int
    ffmpeg_headroom_ratio: float
    use_nvidia_gpu: bool


async def delete_message_silently(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def maybe_delete_original_message(message: discord.Message, context: str) -> None:
    try:
        await message.delete()
        logger.info("Deleted original %s message %s", context, message.id)
    except discord.Forbidden:
        logger.warning("Missing permissions to delete %s message %s", context, message.id)
    except discord.HTTPException as exc:
        logger.error("Failed to delete %s message %s: %s", context, message.id, exc)


def cleanup_file(filepath: str) -> None:
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as exc:
        logger.warning("Failed to clean up file %s: %s", filepath, exc)


async def run_blocking(func: Callable, *args, timeout_seconds: int | None = None, **kwargs):
    if timeout_seconds:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout_seconds)
    return await asyncio.to_thread(func, *args, **kwargs)


async def process_media_links(
    *,
    message: discord.Message,
    urls: Sequence[str],
    source_name: str,
    icon: str,
    url_validator: Callable[[str], str],
    downloader: Callable[..., DownloadResult],
    compressor: Callable[..., str | None],
    view_factory: Callable[[str], discord.ui.View],
    semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig,
) -> int:
    processed = 0
    for source_url in urls:
        validated_url = url_validator(source_url)
        processing_msg = await message.channel.send(f"⏳ Downloading {source_name} video from <@{message.author.id}>...")

        filepath: str | None = None
        original_filepath: str | None = None

        try:
            async with semaphore:
                result = await run_blocking(
                    downloader,
                    validated_url,
                    output_folder=config.temp_directory,
                    timeout_seconds=config.ytdlp_timeout_seconds,
                )

            if not result.success or not result.filepath:
                logger.error("%s download failed for %s: %s", source_name, validated_url, result.error or "unknown")
                continue

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
                    logger.warning("%s compression failed for %s", source_name, filepath)
                    continue
                filepath = compressed_path

                if os.path.getsize(filepath) > config.upload_limit_bytes:
                    logger.warning("Compressed %s video still exceeds upload limit: %s", source_name, filepath)
                    continue

            media_view = view_factory(validated_url)
            media_view.original_author_id = message.author.id
            with open(filepath, "rb") as media_file:
                file = discord.File(media_file, filename=os.path.basename(filepath))
                await delete_message_silently(processing_msg)
                sent_message = await message.channel.send(
                    content=f"{icon} **{source_name} video shared by <@{message.author.id}>:**\n{result.title}",
                    file=file,
                    view=media_view,
                )
                media_view.message = sent_message

            processed += 1
            await maybe_delete_original_message(message, source_name)
        except asyncio.TimeoutError:
            logger.error("%s operation timed out for URL: %s", source_name, validated_url)
        except (discord.HTTPException, discord.Forbidden, OSError, IOError) as exc:
            logger.error("Error processing %s video %s: %s", source_name, validated_url, exc)
        finally:
            await delete_message_silently(processing_msg)
            if filepath:
                cleanup_file(filepath)
            if original_filepath and original_filepath != filepath:
                cleanup_file(original_filepath)

    return processed
