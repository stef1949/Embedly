from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


@dataclass(frozen=True)
class BotConfig:
    discord_token: str
    rate_limit_seconds: int = 10
    global_rate_limit_per_minute: int = 30
    ytdlp_timeout_seconds: int = 120
    ffprobe_timeout_seconds: int = 15
    ffmpeg_timeout_seconds: int = 120
    upload_limit_bytes: int = 8 * 1024 * 1024
    default_emulation: bool = True
    log_level: str = "INFO"
    temp_directory: str = "/tmp"
    use_nvidia_gpu: bool = False
    ffmpeg_headroom_ratio: float = 0.95
    media_concurrency: int = 3



def load_config() -> BotConfig:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("No Discord token provided. Please set DISCORD_BOT_TOKEN.")

    headroom_raw = os.getenv("FFMPEG_HEADROOM_RATIO", "0.95")
    try:
        headroom = float(headroom_raw)
    except ValueError:
        headroom = 0.95
    if not (0.5 <= headroom <= 0.99):
        headroom = 0.95

    return BotConfig(
        discord_token=token,
        rate_limit_seconds=_get_int("RATE_LIMIT_SECONDS", 10, 1),
        global_rate_limit_per_minute=_get_int("GLOBAL_RATE_LIMIT", 30, 1),
        ytdlp_timeout_seconds=_get_int("YTDLP_TIMEOUT_SECONDS", 120, 10),
        ffprobe_timeout_seconds=_get_int("FFPROBE_TIMEOUT_SECONDS", 15, 1),
        ffmpeg_timeout_seconds=_get_int("FFMPEG_TIMEOUT_SECONDS", 120, 10),
        upload_limit_bytes=_get_int("UPLOAD_LIMIT_BYTES", 8 * 1024 * 1024, 1024),
        default_emulation=_get_bool("DEFAULT_EMULATION", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        temp_directory=os.getenv("TEMP_DIRECTORY", "/tmp"),
        use_nvidia_gpu=_get_bool("USE_NVIDIA_GPU", False),
        ffmpeg_headroom_ratio=headroom,
        media_concurrency=_get_int("MEDIA_CONCURRENCY", 3, 1),
    )
