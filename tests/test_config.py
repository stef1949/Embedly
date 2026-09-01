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
            self.assertFalse(cfg.use_nvidia_gpu)
            self.assertEqual(cfg.state_database_path, "embedly_state.sqlite3")
            self.assertEqual(cfg.tiktok_emoji, "")
            self.assertEqual(cfg.ownership_retention_days, 30)

    def test_invalid_headroom_falls_back(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "abc", "FFMPEG_HEADROOM_RATIO": "2.5"}, clear=True):
            cfg = load_config()
            self.assertEqual(cfg.ffmpeg_headroom_ratio, 0.95)

    def test_invalid_log_level_falls_back(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "abc", "LOG_LEVEL": "verbose"}, clear=True):
            cfg = load_config()
            self.assertEqual(cfg.log_level, "INFO")


if __name__ == "__main__":
    unittest.main()
