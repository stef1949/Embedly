from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import discord

from services.downloaders import DownloadResult, download_video
from services.media_embeds import build_media_metadata_embed
from utils.urls import is_tiktok_url, validate_tiktok_url

TIKTOK_COLOR = 0x25F4EE
TIKTOK_POST_STATS = (
    ("Likes", "❤️", ("like_count",)),
    ("Comments", "💬", ("comment_count", "comments_count")),
    ("Reposts", "🔁", ("repost_count", "share_count")),
)
TIKTOK_FALLBACK_ICON = "🎵"
TIKTOK_ABOUT_URL = "https://github.com/stef1949/Embedly"
_CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d{17,20}>")
_HANDLE_PATTERN = re.compile(r"[A-Za-z0-9._]{1,64}")
_SUBTITLE_TIMING_PATTERN = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s+-->\s+"
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}.*$"
)
_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class TikTokPost:
    display_name: str
    handle: str | None
    creator_url: str
    original_url: str
    description: str | None
    duration_seconds: int | None
    upload_date: str | None
    width: int | None
    height: int | None
    like_count: int | None
    comment_count: int | None
    repost_count: int | None
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
        items = []
        if self.like_count is not None:
            items.append(f"♥ {format_compact_number(self.like_count)}")
        if self.comment_count is not None:
            items.append(f"💬 {format_compact_number(self.comment_count)}")
        if self.repost_count is not None:
            items.append(f"🔁 {format_compact_number(self.repost_count)}")
        return "   ".join(items) or "Engagement statistics unavailable"

    @property
    def information_text(self) -> str:
        lines = [f"**Creator:** {escape_discord_text(self.display_name)}"]
        if self.handle:
            lines.append(f"**Handle:** {escape_discord_text(f'@{self.handle}')}")
        if self.description:
            lines.append(f"**Description:** {escape_discord_text(self.description)}")
        if self.upload_date:
            lines.append(f"**Posted:** {escape_discord_text(self.upload_date)}")
        if self.duration_seconds is not None:
            lines.append(f"**Duration:** {_format_duration(self.duration_seconds)}")
        if self.width and self.height:
            lines.append(f"**Video:** {self.width}×{self.height}")
        lines.append(f"**Original:** {self.original_url}")
        return _truncate("\n".join(lines), 1900)


def extract_tiktok_post(result: DownloadResult, validated_url: str) -> TikTokPost:
    metadata = result.metadata or {}
    original_url = _validated_metadata_url(metadata, validated_url)
    handle = _extract_handle(metadata, original_url)
    display_name = _first_text(metadata, ("uploader", "creator", "channel"), 80)
    if not display_name:
        display_name = "TikTok creator"

    creator_url = _creator_url(metadata, handle, original_url)
    description = _first_text(metadata, ("description", "caption", "title"), 1000)
    duration = _non_negative_number(metadata, ("duration",))
    width = _non_negative_number(metadata, ("width",))
    height = _non_negative_number(metadata, ("height",))

    return TikTokPost(
        display_name=display_name,
        handle=handle,
        creator_url=creator_url,
        original_url=original_url,
        description=description,
        duration_seconds=int(duration) if duration is not None else None,
        upload_date=_format_upload_date(metadata),
        width=int(width) if width else None,
        height=int(height) if height else None,
        like_count=_count(metadata, ("like_count",)),
        comment_count=_count(metadata, ("comment_count", "comments_count")),
        repost_count=_count(metadata, ("repost_count", "share_count")),
        transcript=_extract_transcript(metadata, result.filepath),
    )


def format_compact_number(value: int | float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(number) or number <= 0:
        return "0"
    if number < 1000:
        return str(int(number))

    units = ("", "K", "M", "B")
    unit_index = min(int(math.log(number, 1000)), len(units) - 1)
    scaled = number / (1000**unit_index)
    rounded = round(scaled, 1)
    if rounded >= 1000 and unit_index < len(units) - 1:
        unit_index += 1
        rounded = round(number / (1000**unit_index), 1)
    return f"{rounded:.1f}".rstrip("0").rstrip(".") + units[unit_index]


def resolve_tiktok_icon(configured_emoji: str | None) -> str:
    candidate = (configured_emoji or "").strip()
    if _CUSTOM_EMOJI_PATTERN.fullmatch(candidate):
        return candidate
    return TIKTOK_FALLBACK_ICON


def escape_discord_text(value: str) -> str:
    cleaned = "".join(character for character in str(value) if character >= " " or character in "\n\t")
    return discord.utils.escape_mentions(discord.utils.escape_markdown(cleaned.strip()))


def download_tiktok_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_video(video_url, output_folder=output_folder, download_subtitles=True)


def build_tiktok_embed(result: DownloadResult, original_url: str, *, include_details: bool = False) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="TikTok",
        color=TIKTOK_COLOR,
        include_details=include_details,
        engagement_field_name="Post info",
        engagement_show_labels=False,
        engagement_bold_counts=True,
        engagement_separator="  ",
        engagement_stats=TIKTOK_POST_STATS,
    )


def _validated_metadata_url(metadata: dict[str, Any], fallback: str) -> str:
    for key in ("webpage_url", "original_url"):
        candidate = _first_text(metadata, (key,), 1000)
        if candidate:
            clean = validate_tiktok_url(candidate)
            parsed = urlsplit(clean)
            if parsed.scheme == "https" and is_tiktok_url(clean):
                return clean
    return validate_tiktok_url(fallback)


def _extract_handle(metadata: dict[str, Any], original_url: str) -> str | None:
    for key in ("uploader_id", "creator_id", "channel_id"):
        candidate = _first_text(metadata, (key,), 80)
        if not candidate:
            continue
        candidate = candidate.lstrip("@")
        if _HANDLE_PATTERN.fullmatch(candidate):
            return candidate

    path_match = re.match(r"^/@([^/]+)/video/\d+", urlsplit(original_url).path)
    if path_match and _HANDLE_PATTERN.fullmatch(path_match.group(1)):
        return path_match.group(1)
    return None


def _creator_url(metadata: dict[str, Any], handle: str | None, original_url: str) -> str:
    for key in ("uploader_url", "channel_url"):
        candidate = _first_text(metadata, (key,), 1000)
        if candidate:
            clean = validate_tiktok_url(candidate)
            parsed = urlsplit(clean)
            if parsed.scheme == "https" and is_tiktok_url(clean):
                return clean
    if handle:
        return f"https://www.tiktok.com/@{handle}"
    return original_url


def _first_text(metadata: dict[str, Any], keys: Iterable[str], maximum: int) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        text = " ".join(str(value).replace("\x00", "").split())
        if text:
            return _truncate(text, maximum)
    return None


def _non_negative_number(metadata: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = metadata.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number >= 0:
            return number
    return None


def _count(metadata: dict[str, Any], keys: Iterable[str]) -> int | None:
    number = _non_negative_number(metadata, keys)
    return int(number) if number is not None else None


def _format_upload_date(metadata: dict[str, Any]) -> str | None:
    timestamp = _non_negative_number(metadata, ("timestamp", "release_timestamp"))
    if timestamp:
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass

    raw_date = _first_text(metadata, ("upload_date", "release_date"), 32)
    if raw_date and len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    return raw_date


def _extract_transcript(metadata: dict[str, Any], media_filepath: str | None) -> str | None:
    embedded = metadata.get("__embedly_transcript")
    if isinstance(embedded, str) and embedded.strip():
        return _clean_transcript(embedded)

    for container_name in ("requested_subtitles", "subtitles", "automatic_captions"):
        container = metadata.get(container_name)
        if not isinstance(container, dict):
            continue
        for language in _ordered_languages(container):
            for entry in _subtitle_entries(container.get(language)):
                data = entry.get("data")
                if isinstance(data, str) and data.strip():
                    return _clean_transcript(data)
                filepath = entry.get("filepath")
                text = _read_subtitle_file(filepath, media_filepath)
                if text:
                    return _clean_transcript(text)
    return None


def _ordered_languages(container: dict[str, Any]) -> list[str]:
    return sorted(container, key=lambda language: (not language.casefold().startswith("en"), language.casefold()))


def _subtitle_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def _read_subtitle_file(filepath: Any, media_filepath: str | None) -> str | None:
    if not isinstance(filepath, str) or not media_filepath:
        return None
    candidate = Path(filepath).resolve()
    media_directory = Path(media_filepath).resolve().parent
    if candidate.parent != media_directory or candidate.suffix.casefold() not in {".vtt", ".srt", ".ttml"}:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > 128 * 1024:
            return None
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _clean_transcript(raw_text: str) -> str | None:
    lines: list[str] = []
    previous = None
    for raw_line in raw_text.replace("\r", "").split("\n"):
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.isdigit() or _SUBTITLE_TIMING_PATTERN.match(line):
            continue
        line = _TAG_PATTERN.sub("", line)
        line = " ".join(line.split())
        if line and line != previous:
            lines.append(line)
            previous = line
    if not lines:
        return None
    return _truncate(escape_discord_text(" ".join(lines)), 1900)


def _format_duration(seconds: int) -> str:
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def _truncate(text: str, maximum: int) -> str:
    if len(text) <= maximum:
        return text
    return f"{text[:maximum - 1]}…"
