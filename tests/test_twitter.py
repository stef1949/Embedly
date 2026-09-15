import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from handlers.twitter import (
    LEGACY_WEBHOOK_NAMES,
    WEBHOOK_NAME,
    _find_reusable_webhook,
    _get_or_create_channel_webhook,
    _webhook_belongs_to_bot,
    _webhook_cache,
    send_twitter_rewrite_message,
)
from utils.urls import rewrite_twitter_urls


class FakeChannel:
    def __init__(self, channel_id=123, webhooks=None):
        self.id = channel_id
        self._webhooks = webhooks or []
        self.created = []

    async def webhooks(self):
        return self._webhooks

    async def create_webhook(self, name):
        webhook = SimpleNamespace(id=999, name=name, user=SimpleNamespace(id=42))
        self.created.append(webhook)
        return webhook


class TwitterWebhookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _webhook_cache.clear()

    def test_webhook_belongs_to_bot(self):
        webhook = SimpleNamespace(user=SimpleNamespace(id=42))
        self.assertTrue(_webhook_belongs_to_bot(webhook, SimpleNamespace(id=42)))
        self.assertFalse(_webhook_belongs_to_bot(webhook, SimpleNamespace(id=43)))

    async def test_find_reusable_webhook_prefers_current_name(self):
        legacy = SimpleNamespace(name=next(iter(LEGACY_WEBHOOK_NAMES)), user=SimpleNamespace(id=42))
        current = SimpleNamespace(name=WEBHOOK_NAME, user=SimpleNamespace(id=42))
        channel = FakeChannel(webhooks=[legacy, current])

        webhook = await _find_reusable_webhook(channel, bot_user=SimpleNamespace(id=42))

        self.assertIs(webhook, current)

    async def test_get_or_create_reuses_existing_webhook(self):
        existing = SimpleNamespace(name=WEBHOOK_NAME, user=SimpleNamespace(id=42))
        channel = FakeChannel(webhooks=[existing])

        webhook = await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42))

        self.assertIs(webhook, existing)
        self.assertEqual(channel.created, [])

    async def test_get_or_create_creates_once_then_uses_cache(self):
        channel = FakeChannel(webhooks=[])

        first = await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42))
        second = await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42))

        self.assertIs(first, second)
        self.assertEqual(len(channel.created), 1)

    async def test_emulation_waits_for_webhook_and_uses_author_identity(self):
        message = self.emulated_message()
        webhook = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))

        with patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock(return_value=webhook)):
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=True,
                icon="X",
                ownership_recorder=Mock(),
            )

        self.assertEqual(processed, 1)
        kwargs = webhook.send.call_args.kwargs
        self.assertTrue(kwargs["wait"])
        self.assertEqual(kwargs["username"], "Original author")
        self.assertEqual(kwargs["avatar_url"], "https://example.com/avatar.png")
        self.assertEqual(kwargs["view"].original_author_id, 456)
        self.assertIs(kwargs["view"].message, webhook.send.return_value)
        self.assertIsInstance(kwargs["view"], discord.ui.LayoutView)
        self.assertNotIn("content", kwargs)
        message.reply.assert_not_awaited()
        message.channel.send.assert_not_awaited()

    async def test_emulation_falls_back_to_bot_when_webhook_send_fails(self):
        message = self.emulated_message()
        error = discord.HTTPException(SimpleNamespace(status=500, reason="Send failed"), "Send failed")
        webhook = SimpleNamespace(send=AsyncMock(side_effect=error))

        with (
            patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock(return_value=webhook)),
            self.assertLogs("handlers.twitter", level="WARNING"),
        ):
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=True,
                icon="X",
                ownership_recorder=Mock(),
            )

        self.assertEqual(processed, 1)
        message.reply.assert_awaited_once()
        self.assertIsInstance(message.reply.call_args.kwargs["view"], discord.ui.LayoutView)

    async def test_emulation_without_webhook_permissions_uses_bot(self):
        message = self.emulated_message()
        message.channel.permissions_for.return_value.manage_webhooks = False

        with patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock()) as get_webhook:
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=True,
                icon="X",
                ownership_recorder=Mock(),
            )

        self.assertEqual(processed, 1)
        get_webhook.assert_not_awaited()
        message.reply.assert_awaited_once()

    async def test_disabled_emulation_sends_native_card_as_bot(self):
        message = self.emulated_message()
        with patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock()) as get_webhook:
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=False,
                icon="X",
                ownership_recorder=Mock(),
            )
        self.assertEqual(processed, 1)
        get_webhook.assert_not_awaited()
        message.reply.assert_awaited_once()

    async def test_legacy_fallback_still_emulates_and_saves_ownership(self):
        message = self.emulated_message()
        webhook = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
        recorder = Mock()
        with (
            patch("handlers.twitter._send_native_twitter_card", AsyncMock(return_value=False)),
            patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock(return_value=webhook)),
        ):
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=True,
                icon="X",
                ownership_recorder=recorder,
            )
        self.assertEqual(processed, 1)
        kwargs = webhook.send.call_args.kwargs
        self.assertEqual(kwargs["content"], "https://vxtwitter.com/user/status/123")
        self.assertEqual(kwargs["username"], "Original author")
        self.assertTrue(kwargs["wait"])
        self.assertEqual({item.label for item in kwargs["view"].children}, {"Delete", "Toggle Emulation"})
        recorder.assert_called_once_with(
            message_id=999, channel_id=321, guild_id=789, original_author_id=456, message_type="twitter",
        )

    async def test_legacy_fallback_uses_bot_if_webhook_is_unavailable(self):
        message = self.emulated_message()
        error = discord.HTTPException(SimpleNamespace(status=500, reason="Send failed"), "Send failed")
        webhook = SimpleNamespace(send=AsyncMock(side_effect=error))
        with (
            patch("handlers.twitter._send_native_twitter_card", AsyncMock(return_value=False)),
            patch("handlers.twitter._get_or_create_channel_webhook", AsyncMock(return_value=webhook)),
            self.assertLogs("handlers.twitter", level="WARNING"),
        ):
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                should_emulate=True,
                icon="X",
                ownership_recorder=Mock(),
            )
        self.assertEqual(processed, 1)
        message.channel.send.assert_awaited_once()
        self.assertIn("<@456>", message.channel.send.call_args.args[0])

    def emulated_message(self):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 321
        channel.permissions_for.return_value = SimpleNamespace(manage_webhooks=True)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=998))
        return SimpleNamespace(
            id=123,
            author=SimpleNamespace(
                id=456,
                display_name="Original author",
                display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
            ),
            guild=SimpleNamespace(id=789, me=SimpleNamespace(id=42)),
            channel=channel,
            reply=AsyncMock(return_value=SimpleNamespace(id=999)),
        )


if __name__ == "__main__":
    unittest.main()
