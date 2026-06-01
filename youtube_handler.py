import discord

from services.downloaders import DownloadResult, download_video
from services.media_embeds import build_media_metadata_embed

YOUTUBE_COLOR = 0xFF0000


def download_youtube_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_video(video_url, output_folder=output_folder)


def build_youtube_embed(result: DownloadResult, original_url: str, *, include_details: bool = False) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="YouTube",
        color=YOUTUBE_COLOR,
        include_details=include_details,
    )
