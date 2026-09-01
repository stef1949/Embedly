import unittest

from persistence import SQLiteStateStore


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStateStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_message_ownership_requires_exact_discord_coordinates(self):
        self.store.record_message_ownership(
            message_id=100,
            channel_id=200,
            guild_id=300,
            original_author_id=400,
            message_type="tiktok_card",
            details="Post details",
            transcript="Caption text",
        )

        ownership = self.store.get_message_ownership(100, 200, 300)
        self.assertIsNotNone(ownership)
        self.assertEqual(ownership.original_author_id, 400)
        self.assertEqual(ownership.details, "Post details")
        self.assertEqual(ownership.transcript, "Caption text")
        self.assertIsNone(self.store.get_message_ownership(100, 201, 300))
        self.assertIsNone(self.store.get_message_ownership(100, 200, 301))

    def test_conflicting_owner_cannot_replace_existing_record(self):
        self.store.record_message_ownership(
            message_id=100,
            channel_id=200,
            guild_id=None,
            original_author_id=400,
            message_type="tiktok_card",
        )

        with self.assertRaises(RuntimeError):
            self.store.record_message_ownership(
                message_id=100,
                channel_id=200,
                guild_id=None,
                original_author_id=999,
                message_type="tiktok_card",
            )

        ownership = self.store.get_message_ownership(100, 200, None)
        self.assertEqual(ownership.original_author_id, 400)

    def test_prune_removes_only_expired_records(self):
        self.store.record_message_ownership(
            message_id=100,
            channel_id=200,
            guild_id=300,
            original_author_id=400,
            message_type="tiktok_card",
        )

        self.assertEqual(self.store.prune_message_ownership(0), 0)
        self.assertEqual(self.store.prune_message_ownership(9_999_999_999), 1)
        self.assertIsNone(self.store.get_message_ownership(100, 200, 300))


if __name__ == "__main__":
    unittest.main()
