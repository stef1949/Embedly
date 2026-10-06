import asyncio
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from unittest.mock import Mock
from services.downloaders import DownloadResult
from utils.urls import extract_supported_links
from workflow_helpers import WorkflowFixture, URLS, PNG, embedbot, close_module

tearDownModule = close_module


def http_error(status=500):
    return discord.HTTPException(SimpleNamespace(status=status, reason='failed'), 'failed')


class MessageReplacementTests(WorkflowFixture, unittest.IsolatedAsyncioTestCase):
    async def test_all_platforms_publish_validated_media_and_persist_before_deletion(self):
        message = self.message(' '.join(URLS.values()))
        async def delete():
            self.assertEqual(len(self.sent), 4)
            for url in URLS.values():
                self.assertEqual(self.store.publication_status(message.id, url), 'published')
            for sent in self.sent:
                self.assertIsNotNone(self.store.get_message_ownership(sent.id, 321, 789))
        message.delete.side_effect = delete
        await embedbot.on_message(message)
        message.delete.assert_awaited_once()
        self.assertEqual(message.reply.await_count, 4)
        message.channel.send.assert_not_awaited()
        self.assertTrue(all(not p.exists() for p in self.files))
        for call in message.reply.call_args_list:
            self.assertIn('file', call.kwargs)
            self.assertIn('Shared by <@456>', str(call.kwargs['view'].to_components()))

    async def test_download_failure_all_platforms_never_publish(self):
        self.results = {url: DownloadResult(False, error='failed') for url in URLS.values()}
        message = self.message(' '.join(URLS.values()))
        await embedbot.on_message(message)
        message.reply.assert_not_awaited()
        message.channel.send.assert_not_awaited()
        message.delete.assert_not_awaited()
        message.edit.assert_not_awaited()

    async def test_invalid_empty_html_and_truncated_media_never_publish(self):
        for payload in (b'', b'<html>login</html>', PNG[:20]):
            with self.subTest(payload=payload[:20]):
                embedbot.runtime_state.user_rate_limit.clear()
                self.results = {url: payload for url in URLS.values()}
                message = self.message(' '.join(URLS.values()))
                await embedbot.on_message(message)
                message.reply.assert_not_awaited()
                message.delete.assert_not_awaited()

    async def test_timeout_never_posts_fallback(self):
        self.results = {url: asyncio.TimeoutError() for url in URLS.values()}
        message = self.message(' '.join(URLS.values()))
        await embedbot.on_message(message)
        message.reply.assert_not_awaited()
        message.channel.send.assert_not_awaited()
        message.delete.assert_not_awaited()

    async def test_posting_failure_403_429_500_keeps_source_without_fallback(self):
        for status in (403, 429, 500):
            with self.subTest(status=status):
                embedbot.runtime_state.user_rate_limit.clear()
                message = self.message(URLS['instagram'])
                message.id = status
                message.reply.side_effect = http_error(status)
                await embedbot.on_message(message)
                message.reply.assert_awaited_once()
                message.channel.send.assert_not_awaited()
                message.delete.assert_not_awaited()

    async def test_partial_failure_retry_skips_already_published_links(self):
        self.results[URLS['youtube']] = DownloadResult(False)
        message = self.message(' '.join(URLS.values()))
        await embedbot.on_message(message)
        message.delete.assert_not_awaited()
        self.assertEqual(message.reply.await_count, 3)
        self.results.clear()
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        self.assertEqual(message.reply.await_count, 4)
        self.assertEqual(self.download_calls.count(URLS['instagram']), 1)
        message.delete.assert_awaited_once()

    async def test_ownership_failure_rolls_back_and_preserves_source(self):
        message = self.message(URLS['instagram'])
        with patch.object(self.store, 'record_message_ownership', side_effect=sqlite3.OperationalError('busy')):
            await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        self.sent[0].delete.assert_awaited_once()
        message.delete.assert_not_awaited()
        self.assertIsNone(self.store.publication_status(message.id, URLS['instagram']))
        self.assertIsNone(self.store.get_message_ownership(1000, 321, 789))

    async def test_failed_rollback_blocks_duplicate_retries(self):
        message = self.message(URLS['instagram'])
        orphan = SimpleNamespace(id=1000, delete=AsyncMock(side_effect=http_error(403)))
        message.reply.side_effect = None
        message.reply.return_value = orphan
        with patch.object(self.store, 'record_message_ownership', side_effect=sqlite3.OperationalError('busy')):
            await embedbot.on_message(message)
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.delete.assert_not_awaited()
        self.assertEqual(self.store.publication_status(message.id, URLS['instagram']), 'pending')

    async def test_ambiguous_send_blocks_duplicate_retry(self):
        message = self.message(URLS['twitter'])
        message.reply.side_effect = asyncio.TimeoutError()
        await embedbot.on_message(message)
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.delete.assert_not_awaited()
        self.assertEqual(self.store.publication_status(message.id, URLS['twitter']), 'pending')

    async def test_concurrent_processing_sends_once(self):
        message = self.message(URLS['instagram'])
        started, release = asyncio.Event(), asyncio.Event()
        original = message.reply.side_effect
        async def reply(**kwargs):
            started.set()
            await release.wait()
            return await original(**kwargs)
        message.reply.side_effect = reply
        first = asyncio.create_task(embedbot.process_message(message))
        try:
            await asyncio.wait_for(started.wait(), 2)
            embedbot.runtime_state.user_rate_limit.clear()
            self.assertEqual(await embedbot.process_message(message), 'busy')
        finally:
            release.set()
            await first
        message.reply.assert_awaited_once()
        message.delete.assert_awaited_once()

    async def test_duplicates_tracking_parameters_and_adjacent_links(self):
        message = self.message(f"<{URLS['instagram']}?utm_source=a><{URLS['instagram']}> {URLS['youtube']}?feature=share")
        await embedbot.on_message(message)
        self.assertEqual(message.reply.await_count, 2)
        message.delete.assert_awaited_once()

    async def test_malformed_unrelated_url_does_not_block_valid_link(self):
        message = self.message('https://[invalid ' + URLS['instagram'])
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.delete.assert_awaited_once()

    async def test_no_links_do_not_consume_rate_limit(self):
        message = self.message('hello https://example.com')
        await embedbot.on_message(message)
        self.assertEqual(embedbot.runtime_state.global_request_timestamps, [])
        message.reply.assert_not_awaited()

    async def test_disabled_server_and_whitelist_still_block(self):
        for settings in ({'enabled': False}, {'restricted_to_channels': True, 'whitelisted_channels': {1}}):
            embedbot.server_settings[789] = settings
            message = self.message(URLS['instagram'])
            await embedbot.on_message(message)
            message.reply.assert_not_awaited()

    async def test_cooldown_for_one_platform_does_not_skip_others(self):
        embedbot.runtime_state.allow_user_action(456, 'twitter', 10)
        message = self.message(' '.join(URLS.values()))
        await embedbot.on_message(message)
        self.assertEqual(message.reply.await_count, 3)
        message.delete.assert_not_awaited()

    async def test_suppress_only_after_complete_publication(self):
        embedbot.server_settings[789] = {'source_behavior': 'suppress'}
        message = self.message(URLS['instagram'])
        async def edit(**kwargs):
            self.assertEqual(self.store.publication_status(123, URLS['instagram']), 'published')
        message.edit.side_effect = edit
        await embedbot.on_message(message)
        message.edit.assert_awaited_once_with(suppress=True)
        message.delete.assert_not_awaited()

    async def test_partial_failure_never_suppresses_source(self):
        embedbot.server_settings[789] = {'source_behavior': 'suppress'}
        self.results[URLS['youtube']] = DownloadResult(False)
        message = self.message(' '.join(URLS.values()))
        await embedbot.on_message(message)
        message.edit.assert_not_awaited()
        message.delete.assert_not_awaited()

    async def test_keep_setting_preserves_original(self):
        embedbot.server_settings[789] = {'source_behavior': 'keep'}
        message = self.message(URLS['instagram'])
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.edit.assert_not_awaited()
        message.delete.assert_not_awaited()

    async def test_source_edit_during_download_prevents_cleanup(self):
        message = self.message(URLS['instagram'])
        message.channel.fetch_message.return_value = SimpleNamespace(content=message.content + ' new text', author=message.author)
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()
        message.delete.assert_not_awaited()
        message.edit.assert_not_awaited()

    async def test_cleanup_permission_failure_does_not_duplicate_retry(self):
        message = self.message(URLS['instagram'])
        message.delete.side_effect = http_error(403)
        await embedbot.on_message(message)
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        message.reply.assert_awaited_once()

    async def test_media_details_and_spoilers_retained(self):
        embedbot.user_media_details_preferences[456] = True
        message = self.message('||' + URLS['instagram'] + '||')
        await embedbot.on_message(message)
        view = message.reply.call_args.kwargs['view']
        self.assertTrue(view.children[0].spoiler)
        self.assertTrue(message.reply.call_args.kwargs['file'].spoiler)

    async def test_settings_commands_still_change_preferences(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=456), channel=None,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        for enabled in (True, False):
            await embedbot.emulate.callback(interaction, enable=enabled)
            await embedbot.media_details.callback(interaction, enable=enabled)
            self.assertIs(embedbot.user_emulation_preferences[456], enabled)
            self.assertIs(embedbot.user_media_details_preferences[456], enabled)

    async def test_private_command_resolves_success_and_failure_privately(self):
        for success in (True, False):
            embedbot.runtime_state.user_rate_limit.clear()
            message = self.message(URLS['instagram'])
            message.id = 100 if success else 200
            if not success:
                self.results[URLS['instagram']] = DownloadResult(False)
            interaction = SimpleNamespace(user=SimpleNamespace(id=456),
                response=SimpleNamespace(send_message=AsyncMock()), edit_original_response=AsyncMock())
            await embedbot.download_links_privately.callback(interaction, message)
            interaction.response.send_message.assert_awaited_once_with('Your link is being downloaded', ephemeral=True)
            self.assertIn('published' if success else 'preserved', interaction.edit_original_response.call_args.kwargs['content'])
            message.channel.send.assert_not_awaited()

    async def test_private_command_requires_submitter(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(send_message=AsyncMock()))
        message = self.message(URLS['instagram'])
        await embedbot.download_links_privately.callback(interaction, message)
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        message.reply.assert_not_awaited()

    async def test_emulate_preference_controls_media_card_identity(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                embedbot.runtime_state.user_rate_limit.clear()
                embedbot.user_emulation_preferences[456] = enabled
                message = self.message(URLS['instagram'])
                message.id = 124 + int(enabled)
                channel = Mock(spec=discord.TextChannel)
                channel.id = 321
                channel.permissions_for.return_value = SimpleNamespace(manage_webhooks=True)
                message.channel = channel
                sent = SimpleNamespace(id=800 + int(enabled), delete=AsyncMock())
                webhook = SimpleNamespace(send=AsyncMock(return_value=sent))
                with patch('handlers.twitter._get_or_create_channel_webhook', AsyncMock(return_value=webhook)) as get_webhook:
                    await embedbot.on_message(message)
                if enabled:
                    get_webhook.assert_awaited_once()
                    webhook.send.assert_awaited_once()
                    self.assertEqual(webhook.send.call_args.kwargs['username'], 'Submitter')
                    self.assertEqual(webhook.send.call_args.kwargs['avatar_url'], 'https://example.com/avatar.png')
                    message.reply.assert_not_awaited()
                else:
                    get_webhook.assert_not_awaited()
                    webhook.send.assert_not_awaited()
                    message.reply.assert_awaited_once()

    async def test_emulate_command_updates_preference_and_reports_media_cards(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=456), channel=None,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        await embedbot.emulate.callback(interaction, enable=True)
        self.assertTrue(embedbot.user_emulation_preferences[456])
        self.assertIn('media cards', interaction.followup.send.call_args.args[0])

    async def test_private_command_waits_for_automatic_job_outcome(self):
        message = self.message(URLS['instagram'])
        started, release = asyncio.Event(), asyncio.Event()
        original = message.reply.side_effect
        async def reply(**kwargs):
            started.set()
            await release.wait()
            return await original(**kwargs)
        message.reply.side_effect = reply
        interaction = SimpleNamespace(user=message.author,
            response=SimpleNamespace(send_message=AsyncMock()), edit_original_response=AsyncMock())
        automatic = asyncio.create_task(embedbot.on_message(message))
        await started.wait()
        private = asyncio.create_task(embedbot.download_links_privately.callback(interaction, message))
        try:
            await asyncio.sleep(0)
            interaction.edit_original_response.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(automatic, private)
        message.reply.assert_awaited_once()
        self.assertIn('published', interaction.edit_original_response.call_args.kwargs['content'])

    async def test_cancelled_send_leaves_claim_and_preserves_source(self):
        message = self.message(URLS['instagram'])
        started = asyncio.Event()
        async def reply(**kwargs):
            started.set()
            await asyncio.Event().wait()
        message.reply.side_effect = reply
        task = asyncio.create_task(embedbot.process_message(message))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        message.delete.assert_not_awaited()
        self.assertEqual(self.store.publication_status(message.id, URLS['instagram']), 'pending')
        self.assertNotIn(message.id, embedbot.runtime_state.active_sources)
        self.assertTrue(all(not p.parent.exists() for p in self.files))
