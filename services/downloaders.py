from __future__ import annotations

import glob
import logging
import os
import tempfile
from dataclasses import dataclass

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    success: bool
    filepath: str | None = None
    title: str = "Unknown Title"
    error: str | None = None


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


def download_video(video_url: str, output_folder: str | None = None, use_nvidia_gpu: bool = False) -> DownloadResult:
    output_folder = output_folder or tempfile.gettempdir()
    os.makedirs(output_folder, exist_ok=True)
    ydl_opts = _build_opts(output_folder, use_nvidia_gpu)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            metadata = ydl.extract_info(video_url, download=False)
            title = metadata.get("title", "Unknown Title")
            info = ydl.extract_info(video_url, download=True)
            filepath = ydl.prepare_filename(info)
            if not os.path.exists(filepath):
                video_id = info.get("id", "")
                matches = glob.glob(f"{output_folder}/{video_id}.*")
                if matches:
                    filepath = matches[0]
                else:
                    return DownloadResult(success=False, title=title, error=f"Downloaded file missing for {video_id}")
            return DownloadResult(success=True, filepath=filepath, title=title)
    except yt_dlp.utils.DownloadError as exc:
        logger.error("yt-dlp download failed for %s: %s", video_url, exc)
        return DownloadResult(success=False, error=f"DownloadError: {exc}")
    except Exception as exc:
        logger.error("Download failed for %s: %s", video_url, exc)
        return DownloadResult(success=False, error=str(exc))
