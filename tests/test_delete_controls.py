import asyncio
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import views
from persistence import SQLiteStateStore


def http_error(status, code):
    cls = {403: discord.Forbidden, 404: discord.NotFound}.get(status, discord.HTTPException)
    return cls(SimpleNamespace(status=status, reason='Test'), {'code': code, 'message': 'Test'})


class DeleteControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SQLiteStateStore(':memory:')
        self.addCleanup(self.store.close)
        for name, value in {'_state': self.store, '_is_admin': lambda uid: uid == 999}.items():
            patcher = patch.object(views, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.claim_publication(50, 'link')
        self.store.record_message_ownership(message_id=100, channel_id=200, guild_id=300,
                                           original_author_id=400, message_type='twitter_card',
                                           source_id=50, link_key='link')
        guild = SimpleNamespace(id=300, owner_id=800, get_member=lambda uid: None)
        self.message = SimpleNamespace(id=100, channel=SimpleNamespace(id=200), guild=guild,
                                       webhook_id=None, delete=AsyncMock())
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=400), guild=guild, message=self.message,
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock(),
            client=SimpleNamespace(user=SimpleNamespace(id=42), fetch_webhook=AsyncMock()),
        )

    def ownership(self):
        return self.store.get_message_ownership(100, 200, 300)

    async def click(self, view):
        items = view.walk_children() if isinstance(view, discord.ui.LayoutView) else view.children
        button = next(item for item in items if getattr(item, 'label', None) == 'Delete')
        await button.callback(self.interaction)

    async def test_all_native_cards_have_persistent_delete_button_and_restart_ownership(self):
        for cls in (views.TikTokCardView, views.TwitterCardView, views.InstagramCardView, views.YouTubeCardView):
            with self.subTest(platform=cls.__name__):
                self.store.record_message_ownership(message_id=100, channel_id=200, guild_id=300,
                                                   original_author_id=400, message_type='twitter_card')
                view = cls.persistent_placeholder()
                self.assertTrue(view.is_persistent())
                async def delete():
                    self.interaction.response.defer.assert_awaited_with(ephemeral=True, thinking=True)
                    self.assertIsNotNone(self.ownership())
                self.message.delete.side_effect = delete
                await self.click(view)
                self.assertIsNone(self.ownership())
                result = self.interaction.edit_original_response.call_args.kwargs
                self.assertEqual(result['content'], 'Message deleted.')
                self.assertEqual(result['allowed_mentions'].to_dict(), discord.AllowedMentions.none().to_dict())
        self.assertEqual(self.store.publication_status(50, 'link'), 'unowned')
        self.assertFalse(self.store.claim_publication(50, 'link'))

    async def test_legacy_buttons_use_same_handler(self):
        for cls in (views.MessageControlView, views.TikTokControlView, views.InstagramControlView, views.YouTubeControlView):
            view = cls() if cls is views.MessageControlView else cls(original_url='https://example.com')
            view.original_author_id = 400
            await self.click(view)
        self.assertEqual(self.message.delete.await_count, 4)

    async def test_other_user_denied_and_missing_ownership_fails_closed(self):
        for missing in (False, True):
            if missing:
                self.store.delete_message_ownership(100)
                self.interaction.user.id = 400
            else:
                self.interaction.user.id = 401
            await self.click(views.TwitterCardView.persistent_placeholder())
            self.message.delete.assert_not_awaited()
            self.assertIn('not allowed', self.interaction.edit_original_response.call_args.kwargs['content'])

    async def test_bot_and_server_owner_override(self):
        self.store.delete_message_ownership(100)
        for user in (800, 999):
            self.interaction.user.id = user
            await self.click(views.TwitterCardView.persistent_placeholder())
        self.assertEqual(self.message.delete.await_count, 2)

    async def test_server_admin_can_delete_without_cached_member(self):
        member = Mock(spec=discord.Member)
        member.id = 700
        member.guild_permissions = SimpleNamespace(administrator=True)
        self.interaction.user = member
        self.store.delete_message_ownership(100)
        await self.click(views.TwitterCardView.persistent_placeholder())
        self.message.delete.assert_awaited_once()

    async def test_database_read_failure_denies_unknown_user(self):
        with patch.object(self.store, 'get_message_ownership', side_effect=sqlite3.OperationalError()):
            await self.click(views.TwitterCardView.persistent_placeholder())
        self.message.delete.assert_not_awaited()
        self.assertIsNotNone(self.ownership())

    async def test_concurrent_clicks_resolve_deleted_and_already_deleted(self):
        second = SimpleNamespace(**vars(self.interaction))
        second.response = SimpleNamespace(defer=AsyncMock())
        second.edit_original_response = AsyncMock()
        self.message.delete.side_effect = [None, http_error(404, 10008)]
        await asyncio.gather(views._delete_control(self.interaction, 400), views._delete_control(second, 400))
        self.assertIsNone(self.ownership())
        results = [i.edit_original_response.call_args.kwargs['content'] for i in (self.interaction, second)]
        self.assertEqual(results, ['Message deleted.', 'Message already deleted.'])

    async def test_failed_delete_retains_ownership(self):
        for error in (http_error(403, 50013), http_error(429, 0), http_error(500, 0),
                      http_error(404, 10015), asyncio.TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.message.delete.side_effect = error
                await self.click(views.TwitterCardView.persistent_placeholder())
                self.assertIsNotNone(self.ownership())
                self.assertNotEqual(self.interaction.edit_original_response.call_args.kwargs['content'], 'Message deleted.')

    async def test_already_deleted_cleans_ownership(self):
        self.message.delete.side_effect = http_error(404, 10008)
        await self.click(views.TwitterCardView.persistent_placeholder())
        self.assertIsNone(self.ownership())
        self.assertEqual(self.interaction.edit_original_response.call_args.kwargs['content'], 'Message already deleted.')

    async def test_webhook_after_restart_uses_owned_webhook_when_channel_delete_forbidden(self):
        self.message.webhook_id = 777
        self.message.delete.side_effect = http_error(403, 50013)
        webhook = SimpleNamespace(channel_id=200, user=SimpleNamespace(id=42), token='test', delete_message=AsyncMock())
        self.interaction.client.fetch_webhook.return_value = webhook
        await self.click(views.TwitterCardView.persistent_placeholder())
        self.interaction.client.fetch_webhook.assert_awaited_once_with(777)
        webhook.delete_message.assert_awaited_once_with(100)
        self.assertIsNone(self.ownership())

    async def test_untrusted_webhook_is_not_used(self):
        self.message.webhook_id = 777
        self.message.delete.side_effect = http_error(403, 50013)
        webhook = SimpleNamespace(channel_id=200, user=SimpleNamespace(id=43), token='test', delete_message=AsyncMock())
        self.interaction.client.fetch_webhook.return_value = webhook
        await self.click(views.TwitterCardView.persistent_placeholder())
        webhook.delete_message.assert_not_awaited()
        self.assertIsNotNone(self.ownership())

    async def test_live_emulated_post_uses_webhook_message(self):
        view = views.TwitterCardView.persistent_placeholder()
        view.message = Mock(spec=discord.WebhookMessage)
        view.message.id = 100
        view.message.delete = AsyncMock()
        await self.click(view)
        view.message.delete.assert_awaited_once()
        self.message.delete.assert_not_awaited()
        self.assertIsNone(self.ownership())

    async def test_ack_failure_does_not_delete_and_cleanup_failure_reports_actual_deletion(self):
        self.interaction.response.defer.side_effect = http_error(404, 10062)
        await self.click(views.TwitterCardView.persistent_placeholder())
        self.message.delete.assert_not_awaited()
        self.interaction.response.defer.side_effect = None
        with patch.object(self.store, 'delete_message_ownership', side_effect=sqlite3.OperationalError()):
            await self.click(views.TwitterCardView.persistent_placeholder())
        self.message.delete.assert_awaited_once()
        self.assertIsNotNone(self.ownership())
        self.assertIn('Message deleted.', self.interaction.edit_original_response.call_args.kwargs['content'])

    async def test_feedback_failure_does_not_repeat_deletion(self):
        self.interaction.edit_original_response.side_effect = http_error(404, 10062)
        await self.click(views.TwitterCardView.persistent_placeholder())
        self.message.delete.assert_awaited_once()
        self.interaction.edit_original_response.assert_awaited_once()
        self.assertIsNone(self.ownership())
