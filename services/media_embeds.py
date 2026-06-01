from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import discord

from services.downloaders import DownloadResult

MAX_EMBED_TITLE_LENGTH = 256
MAX_EMBED_DESCRIPTION_LENGTH = 3000
MAX_EMBED_FIELD_LENGTH = 1024

DEFAULT_ENGAGEMENT_STATS = (
    ("Likes", "❤️", ("like_count",)),
    ("Comments", "💬", ("comment_count", "comments_count")),
    ("Messages", "✉️", ("message_count",)),
    ("Views", "👁️", ("view_count",)),
    ("Shares", "🔗", ("share_count",)),
    ("Reposts", "🔁", ("repost_count",)),
    ("Favorites", "⭐", ("favorite_count",)),
)


def build_media_metadata_embed(
    result: DownloadResult,
    original_url: str,
    *,
    platform_name: str,
    color: int,
    include_details: bool = False,
) -> discord.Embed:
    metadata = result.metadata or {}
    embed_url = _first_text(metadata, ("webpage_url", "original_url", "url")) or original_url
    raw_title = _first_text(metadata, ("title", "fulltitle")) or result.title or f"{platform_name} media"
    title = _truncate(raw_title, MAX_EMBED_TITLE_LENGTH)
    description = _first_text(metadata, ("description", "caption"))

    embed = discord.Embed(title=title, url=embed_url, color=color)
    if description and description != raw_title:
        embed.description = _truncate(description, MAX_EMBED_DESCRIPTION_LENGTH)

    engagement = _format_engagement(metadata)
    if engagement:
        embed.add_field(name="Engagement", value=engagement, inline=False)

    if include_details:
        details = _format_details(metadata)
        if details:
            embed.add_field(name="Details", value=details, inline=False)

    thumbnail_url = _first_text(metadata, ("thumbnail",))
    if thumbnail_url and thumbnail_url.startswith(("http://", "https://")):
        embed.set_thumbnail(url=thumbnail_url)

    return embed


def _format_engagement(metadata: dict[str, Any]) -> str:
    items = []
    for label, icon, keys in DEFAULT_ENGAGEMENT_STATS:
        value = _first_number(metadata, keys)
        if value is not None:
            items.append(f"{icon} {label}: {_format_count(value)}")
    return _truncate(" | ".join(items), MAX_EMBED_FIELD_LENGTH)


def _format_details(metadata: dict[str, Any]) -> str:
    lines = []
    creator = _format_creator(metadata)
    if creator:
        lines.append(f"Creator: {creator}")

    posted = _format_posted_date(metadata)
    if posted:
        lines.append(f"Posted: {posted}")

    duration = _format_duration(_first_number(metadata, ("duration",)))
    if duration:
        lines.append(f"Duration: {duration}")

    width = _first_number(metadata, ("width",))
    height = _first_number(metadata, ("height",))
    if width and height:
        lines.append(f"Size: {int(width)}x{int(height)}")

    return _truncate("\n".join(lines), MAX_EMBED_FIELD_LENGTH)


def _format_creator(metadata: dict[str, Any]) -> str | None:
    uploader = _first_text(metadata, ("uploader", "creator", "channel"))
    uploader_id = _first_text(metadata, ("uploader_id", "channel_id"))
    if uploader and uploader_id and uploader_id not in uploader:
        return f"{uploader} (@{uploader_id.lstrip('@')})"
    return uploader or (f"@{uploader_id.lstrip('@')}" if uploader_id else None)


def _first_text(metadata: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_number(metadata: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _format_count(value: float) -> str:
    return f"{int(value):,}"


def _format_duration(seconds: float | None) -> str | None:
    if seconds is None or seconds <= 0:
        return None
    total_seconds = int(seconds)
    minutes, second = divmod(total_seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def _format_posted_date(metadata: dict[str, Any]) -> str | None:
    timestamp = _first_number(metadata, ("timestamp", "release_timestamp"))
    if timestamp is not None and timestamp > 0:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    upload_date = _first_text(metadata, ("upload_date", "release_date", "modified_date"))
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return upload_date


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return f"{text[:max_length - 3]}..."
