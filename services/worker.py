"""Bounded subprocess execution for download, probe and transcode operations."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
import importlib
import json
import os
from pathlib import Path
import signal
import sys

try:
    from asyncio import timeout as operation_timeout
except ImportError:  # Python 3.10 compatibility
    from async_timeout import timeout as operation_timeout


async def run_blocking(func, *args, timeout_seconds=120, **kwargs):
    payload = json.dumps({"module": func.__module__, "name": func.__name__, "args": args, "kwargs": kwargs})
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "services.worker",
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != "nt",
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(payload.encode()), timeout_seconds)
        if process.returncode:
            raise RuntimeError("Media worker failed")
        value = json.loads(stdout)
        if value.get("download_result"):
            from services.downloaders import DownloadResult
            return DownloadResult(**value["value"])
        return value["value"]
    finally:
        # Kill the entire group, including yt-dlp/ffmpeg descendants, before the
        # caller removes its directory or releases its concurrency slot.
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        await asyncio.shield(process.wait())


def main():
    from contextlib import redirect_stdout
    request = json.load(sys.stdin)
    # Only local, explicitly selected callables enter this process.
    function = getattr(importlib.import_module(request["module"]), request["name"])
    with redirect_stdout(sys.stderr):
        result = function(*request["args"], **request["kwargs"])
    json.dump({"download_result": is_dataclass(result), "value": asdict(result) if is_dataclass(result) else result}, sys.stdout)


if __name__ == "__main__":
    main()
