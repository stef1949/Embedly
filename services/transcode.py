from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def get_video_duration_seconds(filepath: str, timeout_seconds: int) -> float | None:
    if not shutil.which("ffprobe"):
        logger.warning("ffprobe not found in PATH")
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_seconds,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out for %s", filepath)
        return None
    except (ValueError, subprocess.CalledProcessError) as exc:
        logger.warning("ffprobe failed for %s: %s", filepath, exc)
        return None


def calculate_target_bitrates(max_size_bytes: int, duration_seconds: float, headroom_ratio: float) -> tuple[int, int]:
    target_bits = int(max_size_bytes * 8 * headroom_ratio)
    audio = 96_000
    total = max(int(target_bits / max(duration_seconds, 1.0)), audio + 50_000)
    video = max(total - audio, 300_000)
    return video, audio


def compress_video_to_limit(
    filepath: str,
    max_size_bytes: int,
    *,
    ffprobe_timeout_seconds: int,
    ffmpeg_timeout_seconds: int,
    headroom_ratio: float,
    use_nvidia_gpu: bool,
) -> str | None:
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found in PATH")
        return None

    if os.path.getsize(filepath) <= max_size_bytes:
        return filepath

    duration = get_video_duration_seconds(filepath, ffprobe_timeout_seconds)
    if duration is None:
        return None

    video_bitrate, audio_bitrate = calculate_target_bitrates(max_size_bytes, duration, headroom_ratio)
    output_dir = os.path.dirname(filepath) or "."
    base, _ = os.path.splitext(os.path.basename(filepath))
    out = os.path.join(output_dir, f"{base}_compressed.mp4")

    allow_nvenc = use_nvidia_gpu and (os.name == "nt" or os.path.exists("/dev/nvidia0") or os.path.exists("/dev/nvidiactl"))

    def _run(codec: str, preset: str, extra: list[str] | None = None) -> None:
        args = [
            "ffmpeg", "-y", "-i", filepath, "-c:v", codec,
            *(extra or []),
            "-b:v", str(video_bitrate), "-maxrate", str(video_bitrate), "-bufsize", str(video_bitrate * 2),
            "-preset", preset, "-c:a", "aac", "-b:a", str(audio_bitrate), out,
        ]
        subprocess.run(args, capture_output=True, text=True, check=True, timeout=ffmpeg_timeout_seconds)

    try:
        if allow_nvenc:
            try:
                _run("h264_nvenc", "p4", ["-gpu", "0"])
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.warning("NVENC compression failed, retrying with libx264: %s", exc)
                _run("libx264", "veryfast")
        else:
            _run("libx264", "veryfast")
    except subprocess.TimeoutExpired:
        logger.error("Compression timed out for %s", filepath)
        return None
    except subprocess.CalledProcessError as exc:
        logger.error("Compression failed for %s: %s", filepath, exc)
        return None

    if not os.path.exists(out):
        logger.error("Compression output file missing: %s", out)
        return None

    return out
