import discord

from services.downloaders import DownloadResult, download_video
from services.media_embeds import build_media_metadata_embed

from utils.urls import parse_supported_url

YOUTUBE_COLOR = 0xFF0000


def download_youtube_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    try:
        link = parse_supported_url(video_url)
        if link.platform != "youtube":
            raise ValueError("Expected a YouTube Short")
    except ValueError:
        return DownloadResult(False, error="Only explicit YouTube Shorts links are supported")
    return download_video(link.url, output_folder=output_folder, download_subtitles=True)


def build_youtube_embed(result: DownloadResult, original_url: str, *, include_details: bool = False) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="YouTube",
        color=YOUTUBE_COLOR,
        include_details=include_details,
    )
