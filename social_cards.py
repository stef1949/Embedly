from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from services.downloaders import DownloadResult
from tiktok_handler import (
    TIKTOK_ABOUT_URL,
    _count,
    _extract_transcript,
    _first_text,
    _format_upload_date,
    _non_negative_number,
    _truncate,
    escape_discord_text,
    format_compact_number,
)
from utils.urls import (
    TWITTER_HOSTS,
    is_instagram_url,
    is_youtube_url,
    validate_instagram_url,
    validate_youtube_url,
)

ABOUT_EMBEDLY_URL = TIKTOK_ABOUT_URL
INSTAGRAM_COLOR = 0xE4405F
TWITTER_COLOR = 0x1D9BF0
YOUTUBE_COLOR = 0xFF0000

INSTAGRAM_FALLBACK_ICON = "📸"
TWITTER_FALLBACK_ICON = "𝕏"
YOUTUBE_FALLBACK_ICON = "▶️"

_CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d{17,20}>")
_HANDLE_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,80}")


@dataclass(frozen=True)
class SocialPost:
    platform_key: str
    platform_name: str
    open_label: str
    accent_colour: int
    display_name: str
    handle: str | None
    creator_url: str
    original_url: str
    description: str | None
    duration_seconds: int | None
    upload_date: str | None
    width: int | None
    height: int | None
    engagement: tuple[tuple[str, int], ...]
    transcript: str | None

    @property
    def creator_display(self) -> str:
        name = escape_discord_text(self.display_name)
        if not self.handle:
            return f"**{name}**  [View creator]({self.creator_url})"
        handle = escape_discord_text(f"@{self.handle}")
        return f"**{name}**  [{handle}]({self.creator_url})"

    @property
    def engagement_text(self) -> str:
        items = [f"{icon} {format_compact_number(value)}" for icon, value in self.engagement]
        return "   ".join(items) or "Engagement statistics unavailable"

    @property
    def information_text(self) -> str:
        lines = [
            f"**Platform:** {self.platform_name}",
            f"**Creator:** {escape_discord_text(self.display_name)}",
        ]
        if self.handle:
            lines.append(f"**Handle:** {escape_discord_text(f'@{self.handle}')}")
        if self.description:
            lines.append(f"**Description:** {escape_discord_text(self.description)}")
        if self.upload_date:
            lines.append(f"**Posted:** {escape_discord_text(self.upload_date)}")
        if self.duration_seconds is not None:
            lines.append(f"**Duration:** {_format_duration(self.duration_seconds)}")
        if self.width and self.height:
            lines.append(f"**Media:** {self.width}×{self.height}")
        lines.append(f"**Original:** {self.original_url}")
        return _truncate("\n".join(lines), 1900)

    @property
    def detail_summary(self) -> str | None:
        items: list[str] = []
        if self.upload_date:
            items.append(f"Posted {escape_discord_text(self.upload_date)}")
        if self.duration_seconds is not None:
            items.append(_format_duration(self.duration_seconds))
        if self.width and self.height:
            items.append(f"{self.width}×{self.height}")
        return "  •  ".join(items) or None


def resolve_platform_icon(configured_emoji: str | None, fallback: str) -> str:
    candidate = (configured_emoji or "").strip()
    if _CUSTOM_EMOJI_PATTERN.fullmatch(candidate):
        return candidate
    return fallback


def extract_instagram_post(result: DownloadResult, validated_url: str) -> SocialPost:
    metadata = result.metadata or {}
    original_url = _validated_metadata_url(
        metadata,
        validated_url,
        validator=validate_instagram_url,
        predicate=is_instagram_url,
    )
    handle = _extract_instagram_handle(metadata, original_url)
    display_name = _first_text(metadata, ("uploader", "creator", "channel"), 80) or "Instagram creator"
    creator_url = _creator_url(
        metadata,
        handle,
        original_url,
        validator=validate_instagram_url,
        predicate=is_instagram_url,
        derived_url=f"https://www.instagram.com/{handle}/" if handle else None,
    )
    return _media_post(
        result=result,
        metadata=metadata,
        platform_key="instagram",
        platform_name="Instagram",
        open_label="Open in Instagram",
        accent_colour=INSTAGRAM_COLOR,
        display_name=display_name,
        handle=handle,
        creator_url=creator_url,
        original_url=original_url,
        engagement_fields=(
            ("♥", ("like_count",)),
            ("💬", ("comment_count", "comments_count")),
            ("▶", ("view_count", "play_count")),
            ("🔁", ("repost_count", "share_count")),
        ),
    )


def extract_youtube_post(result: DownloadResult, validated_url: str) -> SocialPost:
    metadata = result.metadata or {}
    original_url = _validated_metadata_url(
        metadata,
        validated_url,
        validator=validate_youtube_url,
        predicate=is_youtube_url,
    )
    handle = _extract_youtube_handle(metadata)
    display_name = _first_text(metadata, ("channel", "uploader", "creator"), 80) or "YouTube creator"
    creator_url = _creator_url(
        metadata,
        handle,
        original_url,
        validator=validate_youtube_url,
        predicate=is_youtube_url,
        derived_url=f"https://www.youtube.com/@{handle}" if handle else None,
    )
    return _media_post(
        result=result,
        metadata=metadata,
        platform_key="youtube",
        platform_name="YouTube",
        open_label="Open in YouTube",
        accent_colour=YOUTUBE_COLOR,
        display_name=display_name,
        handle=handle,
        creator_url=creator_url,
        original_url=original_url,
        engagement_fields=(
            ("▶", ("view_count",)),
            ("♥", ("like_count",)),
            ("💬", ("comment_count", "comments_count")),
        ),
    )


def extract_twitter_post(rewritten_url: str) -> SocialPost:
    parsed = urlsplit(rewritten_url)
    host = (parsed.hostname or "").casefold().strip(".")
    if parsed.scheme not in {"http", "https"} or host not in {
        "vxtwitter.com",
        "www.vxtwitter.com",
        *TWITTER_HOSTS,
    }:
        raise ValueError("Twitter/X card URL is not trusted")
    original_url = urlunsplit(("https", "x.com", parsed.path, parsed.query, ""))
    path_parts = [part for part in parsed.path.split("/") if part]
    handle = path_parts[0] if path_parts and _HANDLE_PATTERN.fullmatch(path_parts[0]) else None
    creator_url = f"https://x.com/{handle}" if handle else "https://x.com/"
    return SocialPost(
        platform_key="twitter",
        platform_name="Twitter/X",
        open_label="Open on X",
        accent_colour=TWITTER_COLOR,
        display_name="X post" if handle is None else handle,
        handle=handle,
        creator_url=creator_url,
        original_url=original_url,
        description=None,
        duration_seconds=None,
        upload_date=None,
        width=None,
        height=None,
        engagement=(),
        transcript=None,
    )


def placeholder_post(platform_key: str) -> SocialPost:
    if platform_key == "instagram":
        return extract_instagram_post(
            DownloadResult(success=True, metadata={}),
            "https://www.instagram.com/p/placeholder/",
        )
    if platform_key == "youtube":
        return extract_youtube_post(
            DownloadResult(success=True, metadata={}),
            "https://www.youtube.com/watch?v=placeholder",
        )
    if platform_key == "twitter":
        return extract_twitter_post("https://vxtwitter.com/embedly/status/1")
    raise ValueError(f"Unsupported platform card: {platform_key}")


def _media_post(
    *,
    result: DownloadResult,
    metadata: dict[str, Any],
    platform_key: str,
    platform_name: str,
    open_label: str,
    accent_colour: int,
    display_name: str,
    handle: str | None,
    creator_url: str,
    original_url: str,
    engagement_fields: tuple[tuple[str, tuple[str, ...]], ...],
) -> SocialPost:
    duration = _non_negative_number(metadata, ("duration",))
    width = _non_negative_number(metadata, ("width",))
    height = _non_negative_number(metadata, ("height",))
    engagement = tuple(
        (icon, count)
        for icon, keys in engagement_fields
        if (count := _count(metadata, keys)) is not None
    )
    return SocialPost(
        platform_key=platform_key,
        platform_name=platform_name,
        open_label=open_label,
        accent_colour=accent_colour,
        display_name=display_name,
        handle=handle,
        creator_url=creator_url,
        original_url=original_url,
        description=_first_text(metadata, ("description", "caption", "title"), 1000),
        duration_seconds=int(duration) if duration is not None else None,
        upload_date=_format_upload_date(metadata),
        width=int(width) if width else None,
        height=int(height) if height else None,
        engagement=engagement,
        transcript=_extract_transcript(metadata, result.filepath),
    )


def _validated_metadata_url(
    metadata: dict[str, Any],
    fallback: str,
    *,
    validator: Callable[[str], str],
    predicate: Callable[[str], bool],
) -> str:
    for key in ("webpage_url", "original_url"):
        candidate = _first_text(metadata, (key,), 1000)
        if candidate:
            clean = validator(candidate)
            if urlsplit(clean).scheme == "https" and predicate(clean):
                return clean
    clean_fallback = validator(fallback)
    if urlsplit(clean_fallback).scheme != "https" or not predicate(clean_fallback):
        raise ValueError("Validated media URL is not a trusted platform URL")
    return clean_fallback


def _creator_url(
    metadata: dict[str, Any],
    handle: str | None,
    original_url: str,
    *,
    validator: Callable[[str], str],
    predicate: Callable[[str], bool],
    derived_url: str | None,
) -> str:
    for key in ("uploader_url", "channel_url", "creator_url"):
        candidate = _first_text(metadata, (key,), 1000)
        if candidate:
            clean = validator(candidate)
            if urlsplit(clean).scheme == "https" and predicate(clean):
                return clean
    if handle and derived_url:
        return derived_url
    return original_url


def _extract_instagram_handle(metadata: dict[str, Any], original_url: str) -> str | None:
    handle = _metadata_handle(metadata, ("uploader_id", "creator_id", "channel_id"))
    if handle:
        return handle
    match = re.match(r"^/stories/([^/]+)/\d+", urlsplit(original_url).path)
    if match and _HANDLE_PATTERN.fullmatch(match.group(1)):
        return match.group(1)
    return None


def _extract_youtube_handle(metadata: dict[str, Any]) -> str | None:
    for key in ("uploader_id", "channel_id"):
        value = _first_text(metadata, (key,), 80)
        if value and value.startswith("@") and _HANDLE_PATTERN.fullmatch(value[1:]):
            return value[1:]
    for key in ("uploader_url", "channel_url"):
        value = _first_text(metadata, (key,), 1000)
        if not value:
            continue
        match = re.match(r"^/@([^/]+)", urlsplit(value).path)
        if match and _HANDLE_PATTERN.fullmatch(match.group(1)):
            return match.group(1)
    return None


def _metadata_handle(metadata: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = _first_text(metadata, (key,), 80)
        if not value:
            continue
        candidate = value.lstrip("@")
        if _HANDLE_PATTERN.fullmatch(candidate):
            return candidate
    return None


def _format_duration(seconds: int) -> str:
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"
