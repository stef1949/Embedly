"""Durable retry/ownership contract; no expected-failure exemptions."""
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from persistence import SQLiteStateStore
from workflow_helpers import WorkflowFixture, URLS, embedbot, close_module

tearDownModule = close_module


class DurablePublicationTests(WorkflowFixture, unittest.IsolatedAsyncioTestCase):
    async def test_restart_skips_published_and_quarantines_uncertain_links(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite3'
            first = SQLiteStateStore(path)
            try:
                with patch.object(embedbot, 'state', first):
                    message = self.message(URLS['instagram'])
                    await embedbot.on_message(message)
                first.claim_publication(124, URLS['youtube'])
            finally:
                first.close()
            second = SQLiteStateStore(path)
            try:
                with patch.object(embedbot, 'state', second):
                    embedbot.runtime_state.user_rate_limit.clear()
                    await embedbot.on_message(message)
                    message.reply.assert_awaited_once()
                    uncertain = self.message(URLS['youtube'])
                    uncertain.id = 124
                    await embedbot.on_message(uncertain)
                    uncertain.reply.assert_not_awaited()
                    uncertain.delete.assert_not_awaited()
            finally:
                second.close()

    async def test_claim_is_exclusive_across_database_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (SQLiteStateStore(Path(folder) / 'state.sqlite3') for _ in range(2))
            try:
                self.assertTrue(first.claim_publication(123, URLS['instagram']))
                self.assertFalse(second.claim_publication(123, URLS['instagram']))
            finally:
                first.close()
                second.close()

    async def test_ownership_and_publication_roll_back_together(self):
        self.store.claim_publication(123, URLS['instagram'])
        self.store._connection.execute("CREATE TRIGGER deny_publication BEFORE UPDATE ON link_publications BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_message_ownership(message_id=999, channel_id=321, guild_id=789,
                original_author_id=456, message_type='instagram_card', source_id=123, link_key=URLS['instagram'])
        self.assertIsNone(self.store.get_message_ownership(999, 321, 789))
        self.assertEqual(self.store.publication_status(123, URLS['instagram']), 'pending')

    async def test_isolated_directories_and_cleanup(self):
        await embedbot.on_message(self.message(' '.join(URLS.values())))
        self.assertEqual(len({p.parent for p in self.files}), 4)
        self.assertTrue(all(not p.parent.exists() for p in self.files))

    async def test_expired_ownership_never_authorizes_source_cleanup(self):
        message = self.message(URLS['instagram'])
        embedbot.server_settings[789] = {'source_behavior': 'keep'}
        await embedbot.on_message(message)
        self.store.delete_message_ownership(self.sent[0].id)
        embedbot.server_settings[789] = {'source_behavior': 'delete'}
        embedbot.runtime_state.user_rate_limit.clear()
        await embedbot.on_message(message)
        message.delete.assert_not_awaited()
        message.reply.assert_awaited_once()
