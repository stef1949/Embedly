import logging
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from config import BotConfig
from persistence import SQLiteStateStore
from runtime_state import RuntimeState
from services.downloaders import DownloadResult

# Import event/command wiring without credentials, log files, or a Discord connection.
with (
    patch("config.load_config", return_value=BotConfig(discord_token="test-token", state_database_path=":memory:")),
    patch("logging.FileHandler", return_value=logging.NullHandler()),
    patch("logging.basicConfig"),
):
    import embedbot


def tearDownModule():
    embedbot.state.close()


TWITTER_URL = "https://x.com/user/status/123"
TIKTOK_URL = "https://www.tiktok.com/@user/video/123"
INSTAGRAM_URL = "https://www.instagram.com/p/abc123/"
YOUTUBE_URL = "https://youtu.be/abc123"


def http_error():
    return discord.HTTPException(SimpleNamespace(status=500, reason="Send failed"), "Send failed")


class MessageReplacementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state = SQLiteStateStore(":memory:")
        self.addCleanup(state.close)
        for name, value in {
            "state": state,
            "runtime_state": RuntimeState(),
            "links_processed": 0,
            "DEFAULT_EMULATION": False,
            "user_emulation_preferences": {},
            "user_media_details_preferences": {},
            "server_settings": {},
            "BANNED_USERS": set(),
            "SERVER_BLACKLIST": set(),
            "logger": Mock(),
        }.items():
            patcher = patch.object(embedbot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def message(self, content):
        return SimpleNamespace(
            id=123,
            content=content,
            author=SimpleNamespace(id=456),
            guild=SimpleNamespace(id=789),
            channel=SimpleNamespace(id=321, send=AsyncMock(return_value=SimpleNamespace(id=998, delete=AsyncMock()))),
            reply=AsyncMock(return_value=SimpleNamespace(id=999, delete=AsyncMock())),
            delete=AsyncMock(),
        )

    async def test_twitter_deletes_only_after_send_and_ownership_saved(self):
        message = self.message(TWITTER_URL)

        async def send(*args, **kwargs):
            message.delete.assert_not_awaited()
            return SimpleNamespace(id=999)

        async def delete():
            ownership = embedbot.state.get_message_ownership(999, 321, 789)
            self.assertEqual(ownership.original_author_id, 456)

        message.reply.side_effect = send
        message.delete.side_effect = delete
        await embedbot.on_message(message)

        message.delete.assert_awaited_once()
        self.assertEqual(embedbot.links_processed, 1)
        kwargs = message.reply.call_args.kwargs
        self.assertIsInstance(kwargs["view"], embedbot.TwitterCardView)
        self.assertNotIn("content", kwargs)
        self.assertEqual(kwargs["view"].original_author_id, 456)

    async def test_twitter_fallback_retains_legacy_controls(self):
        message = self.message(TWITTER_URL)
        message.reply.side_effect = http_error()
        await embedbot.on_message(message)

        message.delete.assert_awaited_once()
        args, kwargs = message.channel.send.call_args
        self.assertIn("https://vxtwitter.com/user/status/123", args[0])
        self.assertIn("<@456>", args[0])
        self.assertEqual(kwargs["view"].original_author_id, 456)
        self.assertEqual({item.label for item in kwargs["view"].children}, {"Delete", "Toggle Emulation"})

    async def test_twitter_send_failure_keeps_original(self):
        message = self.message(TWITTER_URL)
        message.reply.side_effect = http_error()
        message.channel.send.side_effect = http_error()

        await embedbot.on_message(message)

        message.delete.assert_not_awaited()
        self.assertEqual(embedbot.links_processed, 0)

    async def test_partial_twitter_spoiler_send_keeps_original(self):
        message = self.message(f"||{TWITTER_URL}|| {TWITTER_URL}")
        message.reply.side_effect = [SimpleNamespace(id=999), http_error()]
        message.channel.send.side_effect = http_error()

        await embedbot.on_message(message)

        self.assertEqual(message.reply.await_count, 2)
        self.assertEqual(embedbot.links_processed, 1)
        message.delete.assert_not_awaited()

    async def test_all_platforms_finish_before_original_is_deleted(self):
        message = self.message(" ".join([TWITTER_URL, TIKTOK_URL, INSTAGRAM_URL, YOUTUBE_URL]))

        async def process(**kwargs):
            message.delete.assert_not_awaited()
            self.assertFalse(kwargs["delete_source"])
            return len(kwargs["urls"])

        with (
            patch.object(embedbot, "process_tiktok_links", AsyncMock(side_effect=process)),
            patch.object(embedbot, "process_native_media_links", AsyncMock(side_effect=process)) as media,
        ):
            await embedbot.on_message(message)

        self.assertEqual([call.kwargs["source_name"] for call in media.await_args_list],
                         ["Instagram", "YouTube"])
        self.assertEqual(embedbot.links_processed, 4)
        message.delete.assert_awaited_once()

    async def test_mixed_platform_failure_keeps_original(self):
        message = self.message(f"{TWITTER_URL} {INSTAGRAM_URL}")
        with patch.object(embedbot, "process_native_media_links", AsyncMock(return_value=0)):
            await embedbot.on_message(message)

        self.assertEqual(embedbot.links_processed, 1)
        message.delete.assert_not_awaited()

    async def test_partial_media_batch_keeps_original(self):
        message = self.message(f"{INSTAGRAM_URL} {INSTAGRAM_URL}")
        with patch.object(embedbot, "process_native_media_links", AsyncMock(return_value=1)):
            await embedbot.on_message(message)

        self.assertEqual(embedbot.links_processed, 1)
        message.delete.assert_not_awaited()

    async def test_partial_tiktok_card_with_failed_fallback_keeps_original(self):
        message = self.message(f"{TIKTOK_URL} {TIKTOK_URL}")
        with (
            patch("handlers.tiktok._send_native_card", AsyncMock(side_effect=[True, False])),
            patch("handlers.tiktok.send_tiktok_fallback_link", AsyncMock(return_value=False)),
        ):
            await embedbot.on_message(message)

        self.assertEqual(embedbot.links_processed, 1)
        message.delete.assert_not_awaited()

    async def test_tiktok_card_and_fallback_success_delete_once(self):
        message = self.message(f"{TIKTOK_URL} {TIKTOK_URL}")

        async def fallback(**kwargs):
            message.delete.assert_not_awaited()
            self.assertEqual(kwargs["source_url"], TIKTOK_URL)
            return True

        with (
            patch("handlers.tiktok._send_native_card", AsyncMock(side_effect=[True, False])),
            patch("handlers.tiktok.send_tiktok_fallback_link", AsyncMock(side_effect=fallback)),
        ):
            await embedbot.on_message(message)

        self.assertEqual(embedbot.links_processed, 2)
        message.delete.assert_awaited_once()

    async def test_later_rate_limited_platform_keeps_original(self):
        message = self.message(f"{TWITTER_URL} {INSTAGRAM_URL}")
        embedbot.runtime_state.allow_user_action(456, "instagram", embedbot.RATE_LIMIT_SECONDS)

        await embedbot.on_message(message)

        message.reply.assert_awaited_once()
        message.delete.assert_not_awaited()

    async def test_no_supported_links_keeps_original(self):
        message = self.message("A normal message https://example.com")
        await embedbot.on_message(message)
        message.delete.assert_not_awaited()
        message.channel.send.assert_not_awaited()
        message.reply.assert_not_awaited()

    async def test_server_settings_still_prevent_processing(self):
        for settings in ({"enabled": False}, {"restricted_to_channels": True, "whitelisted_channels": {999}}):
            with self.subTest(settings=settings):
                embedbot.server_settings[789] = settings
                message = self.message(TWITTER_URL)
                await embedbot.on_message(message)
                message.delete.assert_not_awaited()
                message.channel.send.assert_not_awaited()
                message.reply.assert_not_awaited()

    async def test_emulation_preference_is_passed_to_sender(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                embedbot.runtime_state = RuntimeState()
                embedbot.user_emulation_preferences[456] = enabled
                message = self.message(TWITTER_URL)
                with patch.object(embedbot, "send_twitter_rewrite_message", AsyncMock(return_value=1)) as send:
                    await embedbot.on_message(message)
                self.assertIs(send.call_args.kwargs["should_emulate"], enabled)
                message.delete.assert_awaited_once()

    async def test_media_details_preference_is_used_by_native_cards(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                embedbot.runtime_state = RuntimeState()
                embedbot.user_media_details_preferences[456] = enabled
                message = self.message(INSTAGRAM_URL)
                with patch.object(embedbot, "process_native_media_links", AsyncMock(return_value=1)) as process:
                    await embedbot.on_message(message)
                    self.assertIs(process.call_args.kwargs["include_details"], enabled)

    async def test_user_setting_commands_update_both_preferences(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=456),
            channel=None,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        for enabled in (True, False):
            await embedbot.emulate.callback(interaction, enable=enabled)
            await embedbot.media_details.callback(interaction, enable=enabled)
            self.assertIs(embedbot.user_emulation_preferences[456], enabled)
            self.assertIs(embedbot.user_media_details_preferences[456], enabled)
        self.assertEqual(interaction.followup.send.await_count, 4)

    async def test_server_setting_commands_still_control_channel_processing(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=456),
            guild=SimpleNamespace(id=789, name="Test server"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        channel = SimpleNamespace(id=321, mention="<#321>")
        await embedbot.configure_server.callback(interaction, enable_bot=True, allowed_channels=True)
        await embedbot.channel_whitelist.callback(interaction, channel=channel, add_to_whitelist=True)
        message = self.message(TWITTER_URL)
        await embedbot.on_message(message)
        message.delete.assert_awaited_once()

        await embedbot.channel_whitelist.callback(interaction, channel=channel, add_to_whitelist=False)
        blocked_message = self.message(TWITTER_URL)
        await embedbot.on_message(blocked_message)
        blocked_message.reply.assert_not_awaited()
        blocked_message.delete.assert_not_awaited()

    async def test_ownership_failure_keeps_original_even_after_successful_sends(self):
        message = self.message(TWITTER_URL)
        with patch.object(embedbot.state, "record_message_ownership", side_effect=sqlite3.OperationalError("Unavailable")):
            await embedbot.on_message(message)

        message.delete.assert_not_awaited()
        message.reply.return_value.delete.assert_awaited_once()
        message.channel.send.return_value.delete.assert_awaited_once()
        self.assertEqual(embedbot.links_processed, 0)

    async def test_real_media_fallbacks_are_all_recorded_before_source_deletion(self):
        message = self.message(" ".join([TIKTOK_URL, INSTAGRAM_URL, YOUTUBE_URL]))
        replies = [SimpleNamespace(id=value, delete=AsyncMock()) for value in (991, 992, 993)]
        message.reply.side_effect = replies

        async def delete():
            for sent in replies:
                ownership = embedbot.state.get_message_ownership(sent.id, 321, 789)
                self.assertEqual(ownership.original_author_id, 456)

        message.delete.side_effect = delete
        failed_download = DownloadResult(success=False, error="Unavailable")
        with (
            patch.object(embedbot, "download_tiktok_video", Mock(return_value=failed_download)),
            patch.object(embedbot, "download_instagram_media", Mock(return_value=failed_download)),
            patch.object(embedbot, "download_youtube_video", Mock(return_value=failed_download)),
        ):
            await embedbot.on_message(message)

        message.delete.assert_awaited_once()
        self.assertEqual(embedbot.links_processed, 3)


if __name__ == "__main__":
    unittest.main()
