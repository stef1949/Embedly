import unittest

from services.transcode import calculate_target_bitrates


class TranscodeTests(unittest.TestCase):
    def test_bitrate_bounds(self):
        video, audio = calculate_target_bitrates(8 * 1024 * 1024, 30.0, 0.95)
        self.assertGreaterEqual(audio, 96000)
        self.assertGreaterEqual(video, 300000)

    def test_zero_duration_safe(self):
        video, audio = calculate_target_bitrates(8 * 1024 * 1024, 0.0, 0.95)
        self.assertGreater(video, 0)
        self.assertEqual(audio, 96000)


if __name__ == "__main__":
    unittest.main()
