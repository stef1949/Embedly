import unittest
from types import SimpleNamespace

from handlers.twitter import (
    LEGACY_WEBHOOK_NAMES,
    WEBHOOK_NAME,
    _find_reusable_webhook,
    _get_or_create_channel_webhook,
    _webhook_belongs_to_bot,
    _webhook_cache,
)


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


if __name__ == "__main__":
    unittest.main()
