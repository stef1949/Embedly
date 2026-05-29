import unittest

from instagram_handler import build_instagram_embed
from services.downloaders import DownloadResult


class InstagramEmbedTests(unittest.TestCase):
    def test_build_instagram_embed_includes_engagement_and_details(self):
        result = DownloadResult(
            success=True,
            title="Fallback title",
            metadata={
                "title": "Post title",
                "description": "Caption text",
                "webpage_url": "https://www.instagram.com/reel/abc123/",
                "like_count": 12345,
                "comment_count": 67,
                "view_count": 890,
                "uploader": "testuser",
                "uploader_id": "testuser",
                "upload_date": "20260529",
                "duration": 95,
                "width": 1080,
                "height": 1920,
                "thumbnail": "https://example.com/thumb.jpg",
            },
        )

        embed = build_instagram_embed(result, "https://www.instagram.com/reel/abc123/")
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(embed.title, "Post title")
        self.assertEqual(embed.description, "Caption text")
        self.assertEqual(embed.thumbnail.url, "https://example.com/thumb.jpg")
        self.assertIn("Likes: 12,345", fields["Engagement"])
        self.assertIn("Comments: 67", fields["Engagement"])
        self.assertIn("Views: 890", fields["Engagement"])
        self.assertIn("Creator: testuser", fields["Details"])
        self.assertIn("Posted: 2026-05-29", fields["Details"])
        self.assertIn("Duration: 1:35", fields["Details"])
        self.assertIn("Size: 1080x1920", fields["Details"])

    def test_build_instagram_embed_omits_empty_optional_fields(self):
        result = DownloadResult(success=True, title="Post title")

        embed = build_instagram_embed(result, "https://www.instagram.com/p/abc123/")

        self.assertEqual(embed.title, "Post title")
        self.assertEqual(len(embed.fields), 0)


if __name__ == "__main__":
    unittest.main()
