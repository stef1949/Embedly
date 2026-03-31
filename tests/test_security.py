import unittest
from types import SimpleNamespace

from security import extract_author_id, can_manage_bot_message


class SecurityTests(unittest.TestCase):
    def test_extract_author_id_from_mention(self):
        message = SimpleNamespace(content='Link shared by <@123456789012345678>')
        self.assertEqual(extract_author_id(message), 123456789012345678)

    def test_can_manage_by_original_author(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            guild=None,
        )
        self.assertTrue(can_manage_bot_message(interaction, 42, is_bot_admin=lambda _: False))


if __name__ == '__main__':
    unittest.main()
