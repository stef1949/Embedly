import os
import unittest
from unittest.mock import patch

from config import load_config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "abc"}, clear=True):
            cfg = load_config()
            self.assertEqual(cfg.rate_limit_seconds, 10)
            self.assertEqual(cfg.upload_limit_bytes, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
