import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from workflow_helpers import WorkflowFixture, close_module

tearDownModule = close_module

from handlers.twitter import (
    LEGACY_WEBHOOK_NAMES,
    WEBHOOK_NAME,
    _find_reusable_webhook,
    _get_or_create_channel_webhook,
    _webhook_belongs_to_bot,
    _webhook_cache,
    _webhook_retry_after,
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
        webhook = SimpleNamespace(id=999, name=name, user=SimpleNamespace(id=42), type=discord.WebhookType.incoming, token="test-token")
        self.created.append(webhook)
        return webhook


class TwitterWebhookTests(WorkflowFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _webhook_cache.clear()
        _webhook_retry_after.clear()

    def test_webhook_belongs_to_bot(self):
        webhook = SimpleNamespace(user=SimpleNamespace(id=42), type=discord.WebhookType.incoming, token="test-token")
        self.assertTrue(_webhook_belongs_to_bot(webhook, SimpleNamespace(id=42)))
        self.assertFalse(_webhook_belongs_to_bot(webhook, SimpleNamespace(id=43)))

    async def test_find_reusable_webhook_prefers_current_name(self):
        legacy = SimpleNamespace(name=next(iter(LEGACY_WEBHOOK_NAMES)), user=SimpleNamespace(id=42), type=discord.WebhookType.incoming, token="test-token")
        current = SimpleNamespace(name=WEBHOOK_NAME, user=SimpleNamespace(id=42), type=discord.WebhookType.incoming, token="test-token")
        channel = FakeChannel(webhooks=[legacy, current])

        webhook = await _find_reusable_webhook(channel, bot_user=SimpleNamespace(id=42))

        self.assertIs(webhook, current)

    async def test_get_or_create_reuses_existing_webhook(self):
        existing = SimpleNamespace(name=WEBHOOK_NAME, user=SimpleNamespace(id=42), type=discord.WebhookType.incoming, token="test-token")
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

    async def test_reuses_renamed_owned_hook_but_not_other_owners_or_unusable_hooks(self):
        def hook(owner=42, token="test-token", kind=discord.WebhookType.incoming):
            return SimpleNamespace(name="Renamed", user=SimpleNamespace(id=owner), token=token, type=kind)
        owned = hook()
        channel = FakeChannel(webhooks=[hook(owner=43), hook(token=None),
                                        hook(kind=discord.WebhookType.channel_follower)])
        self.assertIsNone(await _find_reusable_webhook(channel, bot_user=SimpleNamespace(id=42)))
        channel._webhooks.append(owned)
        self.assertIs(await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42)), owned)
        self.assertEqual(channel.created, [])

    async def test_concurrent_messages_create_only_one_webhook(self):
        channel = FakeChannel()
        create = channel.create_webhook
        async def delayed_create(name):
            await asyncio.sleep(0.01)
            return await create(name)
        channel.create_webhook = delayed_create
        results = await asyncio.gather(*(
            _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42))
            for _ in range(10)
        ))
        self.assertEqual(len(channel.created), 1)
        self.assertTrue(all(result is results[0] for result in results))

    async def test_full_channel_backs_off_then_recovers(self):
        channel = FakeChannel()
        error = discord.HTTPException(SimpleNamespace(status=400, reason="Full"),
                                      {"code": 30007, "message": "Maximum webhooks"})
        channel.create_webhook = AsyncMock(side_effect=error)
        with patch("handlers.twitter.time.monotonic", return_value=100):
            for _ in range(2):
                self.assertIsNone(await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42)))
        channel.create_webhook.assert_awaited_once()
        owned = SimpleNamespace(name="Renamed", user=SimpleNamespace(id=42),
                                type=discord.WebhookType.incoming, token="test-token")
        channel._webhooks.append(owned)
        with patch("handlers.twitter.time.monotonic", return_value=161):
            self.assertIs(await _get_or_create_channel_webhook(channel, bot_user=SimpleNamespace(id=42)), owned)
        channel.create_webhook.assert_awaited_once()

    async def test_full_channel_posts_validated_card_and_records_ownership(self):
        for posting_fails in (False, True):
            with self.subTest(posting_fails=posting_fails):
                _webhook_retry_after.clear()
                message = self.emulated_message()
                message.channel.webhooks = AsyncMock(return_value=[])
                message.channel.create_webhook = AsyncMock(side_effect=discord.HTTPException(
                    SimpleNamespace(status=400, reason="Full"),
                    {"code": 30007, "message": "Maximum webhooks"}))
                if posting_fails:
                    message.reply.side_effect = discord.HTTPException(
                        SimpleNamespace(status=403, reason="Forbidden"), {"code": 50013, "message": "Missing permissions"})
                recorder = Mock()
                processed = await send_twitter_rewrite_message(
                    message=message, rewrite_result=rewrite_twitter_urls("https://x.com/user/status/123"),
                    should_emulate=True, icon="X", ownership_recorder=recorder,
                )
                self.assertEqual(processed, 0 if posting_fails else 1)
                message.reply.assert_awaited_once()
                self.assertIsInstance(message.reply.call_args.kwargs["view"], discord.ui.LayoutView)
                self.assertNotIn("content", message.reply.call_args.kwargs)
                if posting_fails:
                    recorder.assert_not_called()
                else:
                    recorder.assert_called_once()

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

    async def test_webhook_send_failure_does_not_try_bot_or_fallback(self):
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

        self.assertEqual(processed, 0)
        message.reply.assert_not_awaited()

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
