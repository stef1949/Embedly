import unittest
from types import SimpleNamespace

from security import can_manage_bot_message, extract_author_id


class SecurityTests(unittest.TestCase):
    def test_extract_author_id_does_not_trust_message_content(self):
        message = SimpleNamespace(content='Link shared by <@123456789012345678>')
        self.assertIsNone(extract_author_id(message))

    def test_extract_author_id_accepts_trusted_fallback(self):
        message = SimpleNamespace(content='forged <@999>')
        self.assertEqual(extract_author_id(message, 42), 42)

    def test_can_manage_by_original_author(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            guild=None,
        )
        self.assertTrue(can_manage_bot_message(interaction, 42, is_bot_admin=lambda _: False))


if __name__ == '__main__':
    unittest.main()
