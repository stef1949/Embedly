import unittest

from services.downloaders import DownloadResult
from tiktok_handler import build_tiktok_embed
from youtube_handler import build_youtube_embed


class MediaEmbedHandlerTests(unittest.TestCase):
    def test_build_tiktok_embed_includes_metadata(self):
        result = DownloadResult(
            success=True,
            title="Fallback title",
            metadata={
                "title": "TikTok title",
                "description": "TikTok caption",
                "webpage_url": "https://www.tiktok.com/@user/video/123",
                "like_count": 1234,
                "comment_count": 56,
                "repost_count": 7,
                "uploader": "user",
                "upload_date": "20260529",
                "duration": 12,
            },
        )

        embed = build_tiktok_embed(result, "https://www.tiktok.com/@user/video/123")
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(embed.title, "TikTok title")
        self.assertEqual(embed.description, "TikTok caption")
        self.assertEqual(fields["Engagement"], "❤️ Likes: 1,234 | 💬 Comments: 56 | 🔁 Reposts: 7")
        self.assertNotIn("\n", fields["Engagement"])
        self.assertNotIn("Details", fields)

        embed = build_tiktok_embed(result, "https://www.tiktok.com/@user/video/123", include_details=True)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Creator: user", fields["Details"])
        self.assertIn("Posted: 2026-05-29", fields["Details"])
        self.assertIn("Duration: 0:12", fields["Details"])

    def test_build_youtube_embed_includes_metadata(self):
        result = DownloadResult(
            success=True,
            title="Fallback title",
            metadata={
                "title": "YouTube title",
                "description": "YouTube description",
                "webpage_url": "https://www.youtube.com/watch?v=abc123",
                "like_count": 9876,
                "comment_count": 54,
                "view_count": 321000,
                "channel": "Channel Name",
                "upload_date": "20260529",
                "duration": 3661,
                "thumbnail": "https://example.com/thumb.jpg",
            },
        )

        embed = build_youtube_embed(result, "https://www.youtube.com/watch?v=abc123")
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(embed.title, "YouTube title")
        self.assertEqual(embed.description, "YouTube description")
        self.assertEqual(embed.thumbnail.url, "https://example.com/thumb.jpg")
        self.assertEqual(fields["Engagement"], "❤️ Likes: 9,876 | 💬 Comments: 54 | 👁️ Views: 321,000")
        self.assertNotIn("\n", fields["Engagement"])
        self.assertNotIn("Details", fields)

        embed = build_youtube_embed(result, "https://www.youtube.com/watch?v=abc123", include_details=True)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Creator: Channel Name", fields["Details"])
        self.assertIn("Posted: 2026-05-29", fields["Details"])
        self.assertIn("Duration: 1:01:01", fields["Details"])


if __name__ == "__main__":
    unittest.main()
