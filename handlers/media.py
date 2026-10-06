from __future__ import annotations

import asyncio
import inspect
import logging
import os
from pathlib import Path
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import discord

from services.validation import validate_media
from services.worker import run_blocking, operation_timeout

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


async def delete_message_silently(message) -> bool:
    if message is None:
        return False
    try:
        await asyncio.wait_for(message.delete(), 30)
        return True
    except discord.NotFound:
        return True
    except (discord.HTTPException, asyncio.TimeoutError):
        logger.warning("Could not remove message %s; retaining workflow state", getattr(message, "id", None))
        return False


async def maybe_delete_original_message(message, context: str) -> bool:
    return await delete_message_silently(message)


def cleanup_file(filepath: str) -> None:
    try:
        Path(filepath).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not clean media file")


async def process_native_media_links(
    *, message, urls: Sequence[str], source_name: str, platform_key: str,
    icon: str, url_validator: Callable, downloader: Callable, compressor: Callable,
    post_factory: Callable, card_view_factory: Callable,
    ownership_recorder: OwnershipRecorder, semaphore: asyncio.Semaphore,
    config: MediaProcessingConfig, include_details: bool = False,
    default_media_label: str = "video", fallback_view_factory=None,
    delete_source: bool = False, publication_store=None,
    should_emulate: bool = False, spoiler: bool = False,
) -> int:
    """Only validated, uploaded, ownership-recorded media counts as success.

    Source cleanup belongs exclusively to the coordinator. The compatibility
    delete_source/fallback arguments never permit per-handler cleanup or links.
    """
    processed = 0
    for source_url in dict.fromkeys(urls):
        sent = None
        claimed = False
        sending = False
        validated_url = source_url
        try:
            validated_url = url_validator(source_url)
            if publication_store:
                status = publication_store.publication_status(message.id, validated_url)
                if status == "published":
                    processed += 1
                    continue
                if status is not None:
                    continue
            # Semaphore acquisition is part of the bounded preparation phase.
            async with operation_timeout(config.ytdlp_timeout_seconds + 3 * config.ffmpeg_timeout_seconds + 45):
                async with semaphore:
                    with tempfile.TemporaryDirectory(prefix="embedly-", dir=config.temp_directory) as folder:
                        result = await run_blocking(
                            downloader, validated_url, output_folder=folder,
                            timeout_seconds=config.ytdlp_timeout_seconds,
                        )
                        if not result.success or not result.filepath:
                            logger.info("%s download failed; source preserved", source_name)
                            continue
                        filepath = result.filepath
                        if not _owned_file(filepath, folder):
                            raise ValueError("Downloader returned a file outside its job directory")
                        valid = await run_blocking(
                            validate_media, filepath, result.media_type, config.ffmpeg_timeout_seconds,
                            timeout_seconds=config.ffmpeg_timeout_seconds,
                        )
                        if not valid:
                            logger.warning("%s media failed validation; source preserved", source_name)
                            continue
                        if os.path.getsize(filepath) > config.upload_limit_bytes:
                            if result.media_type != "video":
                                continue
                            filepath = await run_blocking(
                                compressor, filepath, config.upload_limit_bytes,
                                ffprobe_timeout_seconds=config.ffprobe_timeout_seconds,
                                ffmpeg_timeout_seconds=config.ffmpeg_timeout_seconds,
                                headroom_ratio=config.ffmpeg_headroom_ratio,
                                use_nvidia_gpu=config.use_nvidia_gpu,
                                timeout_seconds=config.ffmpeg_timeout_seconds,
                            )
                            if not filepath or not _owned_file(filepath, folder):
                                continue
                            if not await run_blocking(
                                validate_media, filepath, "video", config.ffmpeg_timeout_seconds,
                                timeout_seconds=config.ffmpeg_timeout_seconds,
                            ):
                                continue
                        if os.path.getsize(filepath) > config.upload_limit_bytes:
                            continue
                        post = post_factory(result, validated_url)
                        attachment = discord.File(filepath, filename=f"{platform_key}_media{Path(filepath).suffix.lower()}", spoiler=spoiler)
                        try:
                            view = card_view_factory(post=post, media=attachment, icon=icon,
                                                     include_details=include_details, timeout=None)
                            view.original_author_id = message.author.id
                            view.children[0].add_item(discord.ui.TextDisplay(f"Shared by <@{message.author.id}>"))
                            if spoiler:
                                view.children[0].spoiler = True
                            if publication_store:
                                claimed = publication_store.claim_publication(message.id, validated_url)
                                if not claimed:
                                    continue
                            sending = True
                            # A send timeout is ambiguous. Never retry it automatically.
                            async with operation_timeout(45):
                                if should_emulate:
                                    from handlers.twitter import _send_with_optional_emulation
                                    sent = await _send_with_optional_emulation(
                                        message=message, content=None, view=view,
                                        emulate=should_emulate, file=attachment,
                                    )
                                else:
                                    sent = await message.reply(file=attachment, view=view,
                                                               mention_author=False, allowed_mentions=NO_MENTIONS)
                            view.message = sent
                            await _record_ownership(
                                ownership_recorder, sent, source_message=message,
                                message_type=f"{platform_key}_card",
                                details=getattr(post, "information_text", None),
                                transcript=getattr(post, "transcript", None),
                            )
                            processed += 1
                        finally:
                            attachment.close()
        except asyncio.CancelledError:
            if sent is not None and await delete_message_silently(sent) and claimed:
                publication_store.abandon_publication(message.id, validated_url)
            raise
        except Exception as exc:
            logger.warning(
                "%s replacement failed (%s, HTTP status=%s, Discord code=%s); preserving source",
                source_name, type(exc).__name__, getattr(exc, "status", None), getattr(exc, "code", None),
            )
            rolled_back = sent is not None and await delete_message_silently(sent)
            definitive_rejection = isinstance(exc, discord.HTTPException) and 400 <= exc.status < 500
            if claimed and (rolled_back or not sending or definitive_rejection):
                publication_store.abandon_publication(message.id, validated_url)
    return processed


def _owned_file(filepath: str, folder: str) -> bool:
    path = Path(filepath)
    return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(Path(folder).resolve())


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
