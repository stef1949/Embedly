import unittest
from types import SimpleNamespace

from handlers.tiktok import try_kktiktok_embed


class FakeSentMessage:
    def __init__(self, message_id=1):
        self.id = message_id
        self.embeds = []
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, rendered_embeds):
        self.rendered_embeds = rendered_embeds
        self.sent = []

    async def send(self, **kwargs):
        sent = FakeSentMessage()
        self.sent.append((kwargs, sent))
        return sent

    async def fetch_message(self, message_id):
        return SimpleNamespace(embeds=self.rendered_embeds)


class TikTokEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def test_kktiktok_embed_succeeds_when_discord_renders_embed(self):
        message = SimpleNamespace(author=SimpleNamespace(id=456), channel=FakeChannel([object()]))
        view = SimpleNamespace(original_author_id=None, message=None)

        succeeded = await try_kktiktok_embed(
            message=message,
            source_url="https://www.tiktok.com/@user/video/123",
            view_factory=lambda url: view,
            embed_wait_seconds=0,
        )

        self.assertTrue(succeeded)
        self.assertIn("https://tnktok.com/@user/video/123", message.channel.sent[0][0]["content"])
        self.assertEqual(view.original_author_id, 456)
        self.assertFalse(message.channel.sent[0][1].deleted)

    async def test_kktiktok_embed_is_deleted_when_discord_does_not_render_embed(self):
        message = SimpleNamespace(author=SimpleNamespace(id=456), channel=FakeChannel([]))

        succeeded = await try_kktiktok_embed(
            message=message,
            source_url="https://vm.tiktok.com/ZM123456/",
            view_factory=lambda url: SimpleNamespace(original_author_id=None, message=None),
            embed_wait_seconds=0,
        )

        self.assertFalse(succeeded)
        self.assertTrue(message.channel.sent[0][1].deleted)


if __name__ == "__main__":
    unittest.main()
