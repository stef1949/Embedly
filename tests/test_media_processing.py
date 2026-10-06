import asyncio
import os
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

from services.validation import validate_media
from services.worker import run_blocking
from workflow_helpers import PNG, close_module

tearDownModule = close_module


class ValidationTests(unittest.TestCase):
    def test_real_image_validated_and_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'media.png'
            for payload, expected in ((PNG, True), (b'', False), (b'<html>login</html>', False), (PNG[:20], False)):
                path.write_bytes(payload)
                self.assertEqual(validate_media(str(path), 'image'), expected)

    def test_missing_and_fake_video_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'media.mp4'
            self.assertFalse(validate_media(str(path), 'video'))
            path.write_bytes(b'not video')
            self.assertFalse(validate_media(str(path), 'video'))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX process-group regression')
    async def test_timeout_stops_descendant_before_late_write(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = str(Path(folder) / 'late.txt')
            pidfile = str(Path(folder) / 'child.pid')
            code = (
                'import os,time,pathlib; '
                f'pathlib.Path({pidfile!r}).write_text(str(os.getpid())); '
                'time.sleep(1); '
                f'pathlib.Path({marker!r}).write_text("late")'
            )
            with self.assertRaises(asyncio.TimeoutError):
                await run_blocking(subprocess.run, [sys.executable, '-c', code], timeout_seconds=.5)
            self.assertTrue(Path(pidfile).exists(), 'The descendant must have started')
            await asyncio.sleep(.7)
            self.assertFalse(Path(marker).exists())

    async def test_real_validation_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'media.png'
            path.write_bytes(PNG)
            self.assertTrue(await run_blocking(validate_media, str(path), 'image', timeout_seconds=5))

    async def test_timeout_terminates_and_reaps_worker(self):
        real_spawn = asyncio.create_subprocess_exec
        processes = []
        async def spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            return process
        with patch('services.worker.asyncio.create_subprocess_exec', side_effect=spawn):
            with self.assertRaises(asyncio.TimeoutError):
                await run_blocking(time.sleep, 10, timeout_seconds=.2)
        self.assertIsNotNone(processes[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(processes[0].pid, 0)

    async def test_cancellation_reaps_worker(self):
        real_spawn = asyncio.create_subprocess_exec
        processes = []
        started = asyncio.Event()
        async def spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            return process
        with patch('services.worker.asyncio.create_subprocess_exec', side_effect=spawn):
            task = asyncio.create_task(run_blocking(time.sleep, 10, timeout_seconds=20))
            await started.wait()
            await asyncio.sleep(.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)

from dataclasses import replace
import shutil
import subprocess
from services.downloaders import DownloadResult
from workflow_helpers import WorkflowFixture, URLS, embedbot


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class VideoPreparationTests(WorkflowFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.video = Path(self.folder.name) / 'real.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x32:d=0.1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.video)], check=True, timeout=10)

    async def test_real_video_decodes_in_subprocess(self):
        self.assertTrue(await run_blocking(validate_media, str(self.video), 'video', timeout_seconds=10))

    async def test_compression_failure_or_invalid_output_cannot_publish(self):
        for output in (None, b'', b'not a video'):
            with self.subTest(output=output):
                embedbot.runtime_state.user_rate_limit.clear()
                self.patch_object(embedbot, 'media_config', replace(embedbot.media_config, upload_limit_bytes=100))
                async def worker(func, *args, **kwargs):
                    if func is validate_media:
                        return validate_media(*args)
                    if func is embedbot.compress_video_to_limit_safe:
                        if output is None:
                            return None
                        path = Path(args[0]).parent / 'compressed.mp4'
                        path.write_bytes(output)
                        return str(path)
                    path = Path(kwargs['output_folder']) / 'video.mp4'
                    shutil.copyfile(self.video, path)
                    return DownloadResult(True, str(path), media_type='video')
                with patch('handlers.media.run_blocking', side_effect=worker):
                    message = self.message(URLS['youtube'])
                    await embedbot.on_message(message)
                message.reply.assert_not_awaited()
                message.delete.assert_not_awaited()

    async def test_oversized_image_is_preserved_without_transcode(self):
        self.patch_object(embedbot, 'media_config', replace(embedbot.media_config, upload_limit_bytes=1))
        message = self.message(URLS['instagram'])
        await embedbot.on_message(message)
        message.reply.assert_not_awaited()
        self.assertEqual(self.download_calls, [URLS['instagram']])

    async def test_small_hevc_is_validated_and_published_without_conversion(self):
        hevc = Path(self.folder.name) / 'hevc.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=0.1',
                        '-c:v', 'libx265', '-x265-params', 'pools=1:frame-threads=1:log-level=error',
                        '-pix_fmt', 'yuv420p', str(hevc)], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertLess(hevc.stat().st_size, embedbot.media_config.upload_limit_bytes)
        self.assertTrue(validate_media(str(hevc), 'video'))
        message = self.message(URLS['tiktok'])
        original_reply = message.reply.side_effect
        async def reply(**kwargs):
            filename = kwargs['file'].fp.name
            self.assertTrue(validate_media(filename, 'video'))
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                    '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', filename],
                                   capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(probe.stdout.strip(), 'hevc')
            self.assertEqual(Path(filename).read_bytes(), hevc.read_bytes())
            message.delete.assert_not_awaited()
            return await original_reply(**kwargs)
        message.reply.side_effect = reply
        async def worker(func, *args, **kwargs):
            kwargs.pop('timeout_seconds', None)
            if func is validate_media:
                return await asyncio.to_thread(func, *args, **kwargs)
            path = Path(kwargs['output_folder']) / 'hevc.mp4'
            shutil.copyfile(hevc, path)
            return DownloadResult(True, str(path), media_type='video')
        with patch('handlers.media.run_blocking', side_effect=worker):
            await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.delete.assert_awaited_once()
