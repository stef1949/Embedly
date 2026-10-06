from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import warnings

from PIL import Image


def validate_media(filepath: str, media_type: str, timeout_seconds: int = 30) -> bool:
    """Fail closed on empty, undecodable or unsupported media, including output."""
    path = Path(filepath)
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        return False
    try:
        if media_type == "image":
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP", "AVIF"}:
                        return False
                    image.verify()
                with Image.open(path) as image:
                    for frame in range(getattr(image, "n_frames", 1)):
                        image.seek(frame)
                        image.load()
            return True
        if media_type != "video" or path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
            return False
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=timeout_seconds,
        )
        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            return False
        codecs = {"h264", "hevc", "vp8", "vp9", "av1"}
        if not any(s.get("codec_type") == "video" and s.get("codec_name") in codecs
                   and s.get("width", 0) > 0 and s.get("height", 0) > 0 for s in info.get("streams", [])):
            return False
        subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
            capture_output=True, check=True, timeout=timeout_seconds,
        )
        return True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return False
