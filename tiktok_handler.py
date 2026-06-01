import discord

from services.downloaders import DownloadResult, download_video
from services.media_embeds import build_media_metadata_embed

TIKTOK_COLOR = 0x25F4EE


def download_tiktok_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_video(video_url, output_folder=output_folder)


def build_tiktok_embed(result: DownloadResult, original_url: str, *, include_details: bool = False) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="TikTok",
        color=TIKTOK_COLOR,
        include_details=include_details,
    )
