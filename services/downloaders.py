from __future__ import annotations

import glob
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "avif", "heic", "heif"}
VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm", "mkv", "avi", "flv", "wmv"}


@dataclass(frozen=True)
class DownloadResult:
    success: bool
    filepath: str | None = None
    title: str = "Unknown Title"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    media_type: str = "video"


def _build_opts(output_folder: str, use_nvidia_gpu: bool) -> dict:
    opts = {
        "format": "best",
        "outtmpl": f"{output_folder}/%(id)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if use_nvidia_gpu:
        opts["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
        opts["postprocessor_args"] = [
            "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-b:v", "5M",
            "-maxrate", "8M", "-bufsize", "10M", "-c:a", "copy",
        ]
    return opts


def _extension_from_path(filepath: str | None) -> str:
    if not filepath:
        return ""
    return os.path.splitext(filepath)[1].lower().lstrip(".")


def _metadata_extension(metadata: dict[str, Any]) -> str:
    ext = metadata.get("ext")
    return str(ext).lower().lstrip(".") if ext else ""


def detect_media_type(filepath: str | None, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    ext = _extension_from_path(filepath) or _metadata_extension(metadata)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"

    mime_type = str(metadata.get("mime_type") or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"

    return "video"


def download_media(media_url: str, output_folder: str | None = None, use_nvidia_gpu: bool = False) -> DownloadResult:
    output_folder = output_folder or tempfile.gettempdir()
    os.makedirs(output_folder, exist_ok=True)
    ydl_opts = _build_opts(output_folder, use_nvidia_gpu)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            metadata = ydl.extract_info(media_url, download=False) or {}
            title = metadata.get("title") or "Unknown Title"
            info = ydl.extract_info(media_url, download=True) or {}
            merged_metadata = {**metadata, **info}
            title = str(merged_metadata.get("title") or title or "Unknown Title")
            filepath = ydl.prepare_filename(info)
            if not os.path.exists(filepath):
                video_id = info.get("id", "")
                matches = glob.glob(f"{output_folder}/{video_id}.*")
                if matches:
                    filepath = matches[0]
                else:
                    return DownloadResult(
                        success=False,
                        title=title,
                        error=f"Downloaded file missing for {video_id}",
                        metadata=merged_metadata,
                    )
            media_type = detect_media_type(filepath, merged_metadata)
            return DownloadResult(
                success=True,
                filepath=filepath,
                title=title,
                metadata=merged_metadata,
                media_type=media_type,
            )
    except yt_dlp.utils.DownloadError as exc:
        logger.error("yt-dlp download failed for %s: %s", media_url, exc)
        return DownloadResult(success=False, error=f"DownloadError: {exc}")
    except Exception as exc:
        logger.error("Download failed for %s: %s", media_url, exc)
        return DownloadResult(success=False, error=str(exc))


def download_video(video_url: str, output_folder: str | None = None, use_nvidia_gpu: bool = False) -> DownloadResult:
    return download_media(video_url, output_folder=output_folder, use_nvidia_gpu=use_nvidia_gpu)
