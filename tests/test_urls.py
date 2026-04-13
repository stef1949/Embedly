import unittest
from urllib.parse import urlparse

from utils.urls import rewrite_twitter_urls, is_tiktok_url, is_instagram_url, validate_tiktok_url, validate_instagram_url


class UrlTests(unittest.TestCase):
    def test_rewrite_twitter_variants(self):
        text = "check https://twitter.com/a/status/1 and https://mobile.x.com/b/status/2"
        result = rewrite_twitter_urls(text)
        self.assertEqual(len(result.rewritten_urls), 2)
        self.assertTrue(all(urlparse(url).hostname == "vxtwitter.com" for url in result.rewritten_urls))

    def test_spoiler_link(self):
        text = "||https://x.com/a/status/1||"
        result = rewrite_twitter_urls(text)
        self.assertEqual(result.rewritten_urls, [])
        self.assertEqual(len(result.spoiler_urls), 1)

    def test_strip_trailing_punctuation(self):
        text = "look (https://x.com/a/status/1), then https://twitter.com/b/status/2!"
        result = rewrite_twitter_urls(text)
        self.assertEqual(len(result.rewritten_urls), 2)
        self.assertTrue(all(url.endswith(("/1", "/2")) for url in result.rewritten_urls))

    def test_platform_matchers(self):
        self.assertTrue(is_tiktok_url("https://www.tiktok.com/@user/video/1"))
        self.assertTrue(is_instagram_url("https://www.instagram.com/reel/abc"))

    def test_validation_sanitizes(self):
        self.assertEqual(validate_tiktok_url("https://www.tiktok.com/@user/video/1!!!"), "https://www.tiktok.com/@user/video/1")
        self.assertEqual(validate_instagram_url("https://www.instagram.com/reel/abc123),"), "https://www.instagram.com/reel/abc123")


if __name__ == "__main__":
    unittest.main()
