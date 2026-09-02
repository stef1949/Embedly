from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import discord

from services.downloaders import DownloadResult

logger = logging.getLogger(__name__)
OwnershipRecorder = Callable[..., object]
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass(frozen=True)
class MediaProcessingConfig:
    temp_directory: str
    upload_limit_bytes: int
    ytdlp_timeout_seconds: int
    ffmpeg_timeout_seconds: int
    ffprobe_timeout_seconds: int
    ffmpeg_headroom_ratio: float
    use_nvidia_gpu: bool


async def delete_message_silently(message: discord.Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def maybe_delete_original_message(message: discord.Message, context: str) -> bool:
    try:
        await message.delete()
        logger.info("Deleted original %s message %s", context, message.id)
        return True
    except discord.NotFound:
        logger.info("Original %s message %s was already deleted", context, message.id)
    except discord.Forbidden:
        logger.warning("Missing permissions to delete %s message %s", context, message.id)
    except discord.HTTPException as exc:
        logger.error("Failed to delete %s message %s: %s", context, message.id, exc)
    return False


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


def media_label(result: DownloadResult, default: str) -> str:
    if result.media_type in {"image", "video"}:
        return result.media_type
    return default


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
    embed_factory: Callable[[DownloadResult, str], discord.Embed] | None = None,
    default_media_label: str = "video",
    ownership_recorder: OwnershipRecorder | None = None,
) -> int:
    processed = 0
    for source_url in urls:
        validated_url = url_validator(source_url)
        processing_msg = await message.channel.send(
            f"⏳ Downloading {source_name} {default_media_label} from <@{message.author.id}>..."
        )

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
            label = media_label(result, default_media_label)

            if os.path.getsize(filepath) > config.upload_limit_bytes:
                if result.media_type == "image":
                    logger.warning(
                        "%s image exceeds upload limit and cannot be video-compressed: %s",
                        source_name,
                        filepath,
                    )
                    continue

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
                    logger.warning("Compressed %s %s still exceeds upload limit: %s", source_name, label, filepath)
                    continue

            media_view = view_factory(validated_url)
            media_view.original_author_id = message.author.id
            embed = embed_factory(result, validated_url) if embed_factory else None
            with open(filepath, "rb") as media_file:
                file = discord.File(media_file, filename=os.path.basename(filepath))
                await delete_message_silently(processing_msg)
                content = f"{icon} **{source_name} {label} shared by <@{message.author.id}>:**"
                if embed is None:
                    content = f"{content}\n{result.title}"
                send_kwargs = {
                    "content": content,
                    "file": file,
                    "view": media_view,
                }
                if embed is not None:
                    send_kwargs["embed"] = embed
                sent_message = await message.channel.send(**send_kwargs)
                media_view.message = sent_message

            if ownership_recorder is not None:
                try:
                    await _record_ownership(
                        ownership_recorder,
                        sent_message,
                        source_message=message,
                        message_type=source_name.casefold(),
                    )
                except (sqlite3.Error, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.error(
                        "%s ownership persistence failed (%s); preserving source message",
                        source_name,
                        type(exc).__name__,
                    )
                    continue

            processed += 1
            await maybe_delete_original_message(message, source_name)
        except asyncio.TimeoutError:
            logger.error("%s operation timed out for URL: %s", source_name, validated_url)
        except (discord.HTTPException, discord.Forbidden, OSError) as exc:
            logger.error("Error processing %s media %s: %s", source_name, validated_url, exc)
        finally:
            await delete_message_silently(processing_msg)
            if filepath:
                cleanup_file(filepath)
            if original_filepath and original_filepath != filepath:
                cleanup_file(original_filepath)

    return processed


async def process_native_media_links(
    *,
    message: discord.Message,
    urls: Sequence[str],
    source_name: str,
    platform_key: str,
    icon: str,
    url_validator: Callable[[str], str],
    downloader: Callable[..., DownloadResult],
    compressor: Callable[..., str | None],
    post_factory: Callable[[DownloadResult, str], object],
    card_view_factory: Callable[..., discord.ui.LayoutView],
    fallback_view_factory: Callable[[str], discord.ui.View],
    ownership_recorder: OwnershipRecorder,
    semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig,
    default_media_label: str = "video",
    include_details: bool = False,
    delete_source: bool = True,
) -> int:
    """Replace downloaded media links with attachment-backed Components V2 cards."""

    processed = 0
    for source_url in urls:
        validated_url = url_validator(source_url)
        native_sent = await _send_native_media_card(
            message=message,
            validated_url=validated_url,
            source_name=source_name,
            platform_key=platform_key,
            icon=icon,
            downloader=downloader,
            compressor=compressor,
            post_factory=post_factory,
            card_view_factory=card_view_factory,
            ownership_recorder=ownership_recorder,
            semaphore=semaphore,
            config=config,
            default_media_label=default_media_label,
            include_details=include_details,
        )
        if native_sent:
            processed += 1
            continue

        fallback_sent = await _send_media_fallback_link(
            message=message,
            validated_url=validated_url,
            source_name=source_name,
            icon=icon,
            platform_key=platform_key,
            view_factory=fallback_view_factory,
            ownership_recorder=ownership_recorder,
        )
        if fallback_sent:
            processed += 1

    if delete_source and urls and processed == len(urls):
        await maybe_delete_original_message(message, source_name)
    return processed


async def _send_native_media_card(
    *,
    message: discord.Message,
    validated_url: str,
    source_name: str,
    platform_key: str,
    icon: str,
    downloader: Callable[..., DownloadResult],
    compressor: Callable[..., str | None],
    post_factory: Callable[[DownloadResult, str], object],
    card_view_factory: Callable[..., discord.ui.LayoutView],
    ownership_recorder: OwnershipRecorder,
    semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig,
    default_media_label: str,
    include_details: bool,
) -> bool:
    processing_message: discord.Message | None = None
    filepath: str | None = None
    original_filepath: str | None = None
    sent_message: discord.Message | None = None

    try:
        processing_message = await message.channel.send(
            f"⏳ Downloading {source_name} {default_media_label}…",
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
            logger.warning("%s download failed; using link fallback", source_name)
            return False

        original_filepath = result.filepath
        filepath = result.filepath
        if os.path.getsize(filepath) > config.upload_limit_bytes:
            if result.media_type == "image":
                logger.warning("%s image exceeds upload limit; using link fallback", source_name)
                return False
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
                logger.warning("%s compression failed; using link fallback", source_name)
                return False
            filepath = compressed_path
            if os.path.getsize(filepath) > config.upload_limit_bytes:
                logger.warning("Compressed %s media exceeds upload limit; using link fallback", source_name)
                return False

        post = post_factory(result, validated_url)
        attachment = discord.File(
            filepath,
            filename=_native_attachment_filename(platform_key, filepath),
        )
        try:
            view = card_view_factory(
                post=post,
                media=attachment,
                icon=icon,
                include_details=include_details,
                timeout=604800,
            )
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
                message_type=f"{platform_key}_card",
                details=getattr(post, "information_text", None),
                transcript=getattr(post, "transcript", None),
            )
        except (sqlite3.Error, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "%s ownership persistence failed (%s); using link fallback",
                source_name,
                type(exc).__name__,
            )
            await delete_message_silently(sent_message)
            return False
        return True
    except asyncio.TimeoutError:
        logger.warning("%s processing timed out; using link fallback", source_name)
        return False
    except (discord.HTTPException, discord.Forbidden, OSError, TypeError, ValueError) as exc:
        logger.warning("%s card could not be created (%s); using link fallback", source_name, type(exc).__name__)
        if sent_message is not None:
            await delete_message_silently(sent_message)
        return False
    finally:
        await delete_message_silently(processing_message)
        if filepath:
            cleanup_file(filepath)
        if original_filepath and original_filepath != filepath:
            cleanup_file(original_filepath)


async def _send_media_fallback_link(
    *,
    message: discord.Message,
    validated_url: str,
    source_name: str,
    icon: str,
    platform_key: str,
    view_factory: Callable[[str], discord.ui.View],
    ownership_recorder: OwnershipRecorder,
) -> bool:
    view = view_factory(validated_url)
    view.original_author_id = message.author.id
    sent_message: discord.Message | None = None
    try:
        sent_message = await message.reply(
            content=f"{icon} **{source_name} link:**\n{validated_url}",
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )
        view.message = sent_message
        await _record_ownership(
            ownership_recorder,
            sent_message,
            source_message=message,
            message_type=f"{platform_key}_fallback",
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
        logger.warning("Could not send %s fallback (%s)", source_name, type(exc).__name__)
        if sent_message is not None:
            await delete_message_silently(sent_message)
        return False


def _native_attachment_filename(platform_key: str, filepath: str) -> str:
    extension = os.path.splitext(filepath)[1].casefold()
    if extension not in {
        ".mp4",
        ".mov",
        ".m4v",
        ".webm",
        ".mkv",
        ".avi",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".avif",
        ".heic",
        ".heif",
    }:
        extension = ".mp4"
    return f"{platform_key}_media{extension}"


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
    result = recorder(
        **values,
    )
    if inspect.isawaitable(result):
        await result
