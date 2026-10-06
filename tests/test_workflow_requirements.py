"""Requirement-level review tests, without network access or bot credentials.

Expected failures document confirmed defects, not accepted behavior. Remove each
marker when its production fix lands. Existing tests still require link fallbacks.
"""

import asyncio
import base64
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

import test_message_replacement as fixtures
from handlers.media import run_blocking
from services.downloaders import DownloadResult

embedbot = fixtures.embedbot


def tearDownModule():
    # This module also runs alone, when the imported fixture module is not run.
    fixtures.tearDownModule()


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


class WorkflowRequirementsTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MessageReplacementTests.setUp
    message = fixtures.MessageReplacementTests.message

    def download(self, payload=PNG):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "media.png"
        path.write_bytes(payload)
        return DownloadResult(success=True, filepath=str(path), media_type="image")

    async def instagram(self, message, result):
        with patch.object(embedbot, "download_instagram_media", Mock(return_value=result)):
            await embedbot.on_message(message)

    async def test_publication_and_ownership_precede_source_delete(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        result = self.download()

        async def reply(**kwargs):
            message.delete.assert_not_awaited()
            self.assertIsNone(embedbot.state.get_message_ownership(999, 321, 789))
            self.assertEqual(kwargs["file"].fp.read(), PNG)
            return SimpleNamespace(id=999, delete=AsyncMock())

        async def delete():
            self.assertIsNotNone(embedbot.state.get_message_ownership(999, 321, 789))

        message.reply.side_effect = reply
        message.delete.side_effect = delete
        await self.instagram(message, result)
        message.delete.assert_awaited_once()
        self.assertFalse(Path(result.filepath).exists())
        message.channel.send.return_value.delete.assert_awaited()

    @unittest.expectedFailure
    async def test_failed_download_must_not_publish_or_delete(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        await self.instagram(message, DownloadResult(success=False, error="unavailable"))
        message.reply.assert_not_awaited()
        message.delete.assert_not_awaited()

    @unittest.expectedFailure
    async def test_empty_media_must_not_publish(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        await self.instagram(message, self.download(b""))
        message.reply.assert_not_awaited()
        message.delete.assert_not_awaited()

    @unittest.expectedFailure
    async def test_html_disguised_as_image_must_not_publish(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        await self.instagram(message, self.download(b"<html>Login required</html>"))
        message.reply.assert_not_awaited()
        message.delete.assert_not_awaited()

    @unittest.expectedFailure
    async def test_posting_failure_must_not_be_replaced_by_fallback(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        message.reply.side_effect = [fixtures.http_error(), SimpleNamespace(id=998)]
        await self.instagram(message, self.download())
        self.assertEqual(message.reply.await_count, 1)
        message.delete.assert_not_awaited()

    async def test_forbidden_or_rate_limited_publication_preserves_source(self):
        for status, error_type in ((403, discord.Forbidden), (429, discord.HTTPException)):
            with self.subTest(status=status):
                embedbot.runtime_state.user_rate_limit.clear()
                message = self.message(fixtures.INSTAGRAM_URL)
                message.reply.side_effect = error_type(
                    SimpleNamespace(status=status, reason="blocked"), "blocked"
                )
                await self.instagram(message, self.download())
                message.delete.assert_not_awaited()
                self.assertIsNone(embedbot.state.get_message_ownership(999, 321, 789))

    @unittest.expectedFailure
    async def test_partial_download_batch_must_preserve_source(self):
        message = self.message(f"{fixtures.INSTAGRAM_URL} https://www.instagram.com/p/other/")
        message.reply.side_effect = [
            SimpleNamespace(id=991, delete=AsyncMock()),
            SimpleNamespace(id=992, delete=AsyncMock()),
        ]
        with patch.object(embedbot, "download_instagram_media", side_effect=[
            self.download(), DownloadResult(success=False, error="failed"),
        ]):
            await embedbot.on_message(message)
        message.delete.assert_not_awaited()
        self.assertEqual(message.reply.await_count, 1)

    async def test_persistent_ownership_failure_rolls_back_and_preserves_source(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        with patch.object(embedbot.state, "record_message_ownership", side_effect=sqlite3.OperationalError("offline")):
            await self.instagram(message, self.download())
        message.delete.assert_not_awaited()
        message.reply.return_value.delete.assert_awaited()
        self.assertEqual(embedbot.links_processed, 0)

    @unittest.expectedFailure
    async def test_transient_ownership_failure_must_not_delete_source(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        record = embedbot.state.record_message_ownership
        count = 0

        def transient(**kwargs):
            nonlocal count
            count += 1
            if count == 1:
                raise sqlite3.OperationalError("temporarily busy")
            return record(**kwargs)

        with patch.object(embedbot.state, "record_message_ownership", side_effect=transient):
            await self.instagram(message, self.download())
        message.delete.assert_not_awaited()

    @unittest.expectedFailure
    async def test_progress_must_not_be_a_public_channel_message(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        await self.instagram(message, self.download())
        message.channel.send.assert_not_awaited()

    @unittest.expectedFailure
    async def test_timeout_must_not_publish_fallback(self):
        message = self.message(fixtures.INSTAGRAM_URL)
        with patch("handlers.media.run_blocking", AsyncMock(side_effect=asyncio.TimeoutError)):
            await embedbot.on_message(message)
        message.reply.assert_not_awaited()
        message.delete.assert_not_awaited()

    @unittest.expectedFailure
    async def test_retry_after_cooldown_must_not_duplicate_successful_link(self):
        message = self.message(fixtures.TWITTER_URL)
        # Source can remain after missing Manage Messages permission.
        message.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="denied"), "denied")
        await embedbot.on_message(message)
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        self.assertEqual(message.reply.await_count, 1)

    @unittest.expectedFailure
    async def test_overlapping_retry_after_cooldown_must_not_duplicate(self):
        message = self.message(fixtures.TWITTER_URL)
        started, release = asyncio.Event(), asyncio.Event()

        async def reply(**kwargs):
            started.set()
            await release.wait()
            return SimpleNamespace(id=999, delete=AsyncMock())

        message.reply.side_effect = reply
        first = asyncio.create_task(embedbot.on_message(message))
        try:
            await asyncio.wait_for(started.wait(), 2)
            embedbot.runtime_state.user_rate_limit.clear()
            second = asyncio.create_task(embedbot.on_message(message))
            release.set()
            await asyncio.gather(first, second)
        finally:
            release.set()
            await first
        self.assertEqual(message.reply.await_count, 1)

    @unittest.expectedFailure
    async def test_adjacent_angle_bracket_links_detected_individually(self):
        message = self.message(f"<{fixtures.INSTAGRAM_URL}><https://www.instagram.com/p/other/>")
        process = AsyncMock(return_value=0)
        with patch.object(embedbot, "process_native_media_links", process):
            await embedbot.on_message(message)
        self.assertEqual(process.call_args.kwargs["urls"], [
            fixtures.INSTAGRAM_URL, "https://www.instagram.com/p/other/",
        ])

    @unittest.expectedFailure
    async def test_twitter_must_not_publish_without_downloaded_media(self):
        message = self.message(fixtures.TWITTER_URL)
        await embedbot.on_message(message)
        self.assertIn("file", message.reply.call_args.kwargs)

    @unittest.expectedFailure
    async def test_malformed_unrelated_url_must_not_abort_supported_links(self):
        message = self.message(f"https://[invalid {fixtures.INSTAGRAM_URL}")
        process = AsyncMock(return_value=0)
        try:
            with patch.object(embedbot, "process_native_media_links", process):
                await embedbot.on_message(message)
        except ValueError:
            self.fail("Malformed unrelated URL aborted the complete message")
        process.assert_awaited_once()

    @unittest.expectedFailure
    async def test_repeated_link_in_one_message_must_not_duplicate(self):
        message = self.message(f"{fixtures.TWITTER_URL} {fixtures.TWITTER_URL}")
        await embedbot.on_message(message)
        self.assertEqual(message.reply.await_count, 1)


class WorkerLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timed_out_worker_continues_and_writes_after_return(self):
        """Characterizes the leak; cancellation-safe worker ownership is absent."""
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "late.mp4"

            def worker():
                started.set()
                try:
                    release.wait(2)
                    output.write_bytes(b"late output")
                finally:
                    finished.set()

            try:
                with self.assertRaises(asyncio.TimeoutError):
                    await run_blocking(worker, timeout_seconds=0.05)
                self.assertTrue(started.is_set())
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                await asyncio.to_thread(finished.wait, 2)
            self.assertTrue(output.exists())
