import discord

from services.downloaders import DownloadResult, download_video
from services.media_embeds import build_media_metadata_embed

INSTAGRAM_COLOR = 0xE4405F


def download_instagram_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_video(video_url, output_folder=output_folder)


def build_instagram_embed(result: DownloadResult, original_url: str) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="Instagram",
        color=INSTAGRAM_COLOR,
    )
