import unittest
from unittest.mock import patch

from instagram_handler import (
    _decode_json_string,
    _fetch_instagram_image_metadata,
    _parse_meta_tags,
    download_instagram_media,
)
from instagram_handler import build_instagram_embed
from services.downloaders import DownloadResult


class InstagramEmbedTests(unittest.TestCase):
    def test_build_instagram_embed_includes_horizontal_engagement(self):
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
        self.assertEqual(fields["Engagement"], "❤️ Likes: 12,345 | 💬 Comments: 67 | 👁️ Views: 890")
        self.assertNotIn("\n", fields["Engagement"])
        self.assertNotIn("Details", fields)

    def test_build_instagram_embed_includes_details_when_enabled(self):
        result = DownloadResult(
            success=True,
            title="Fallback title",
            metadata={
                "title": "Post title",
                "uploader": "testuser",
                "uploader_id": "testuser",
                "upload_date": "20260529",
                "duration": 95,
                "width": 1080,
                "height": 1920,
            },
        )

        embed = build_instagram_embed(
            result,
            "https://www.instagram.com/reel/abc123/",
            include_details=True,
        )
        fields = {field.name: field.value for field in embed.fields}

        self.assertIn("Creator: testuser", fields["Details"])
        self.assertIn("Posted: 2026-05-29", fields["Details"])
        self.assertIn("Duration: 1:35", fields["Details"])
        self.assertIn("Size: 1080x1920", fields["Details"])

    def test_build_instagram_embed_omits_empty_optional_fields(self):
        result = DownloadResult(success=True, title="Post title")

        embed = build_instagram_embed(result, "https://www.instagram.com/p/abc123/")

        self.assertEqual(embed.title, "Post title")
        self.assertEqual(len(embed.fields), 0)

    def test_download_instagram_media_falls_back_to_image_when_no_video_exists(self):
        video_result = DownloadResult(success=False, error="DownloadError: There is no video in this post")
        image_result = DownloadResult(
            success=True,
            filepath="post.jpg",
            title="Image post",
            media_type="image",
        )

        with patch("instagram_handler.download_media", return_value=video_result) as download_media:
            with patch("instagram_handler.download_instagram_image", return_value=image_result) as download_image:
                result = download_instagram_media("https://www.instagram.com/p/abc123/")

        self.assertIs(result, image_result)
        download_media.assert_called_once_with("https://www.instagram.com/p/abc123/", output_folder=None)
        download_image.assert_called_once_with("https://www.instagram.com/p/abc123/", output_folder=None)

    def test_download_instagram_media_does_not_fallback_for_unrelated_errors(self):
        video_result = DownloadResult(success=False, error="DownloadError: private post")

        with patch("instagram_handler.download_media", return_value=video_result):
            with patch("instagram_handler.download_instagram_image") as download_image:
                result = download_instagram_media("https://www.instagram.com/p/abc123/")

        self.assertIs(result, video_result)
        download_image.assert_not_called()

    def test_parse_image_metadata_helpers(self):
        html_text = (
            '<meta property="og:image" content="https://cdn.example/post.jpg?x=1&amp;y=2">'
            '<meta property="og:title" content="Instagram photo">'
        )

        meta = _parse_meta_tags(html_text)

        self.assertEqual(meta["og:image"], "https://cdn.example/post.jpg?x=1&y=2")
        self.assertEqual(meta["og:title"], "Instagram photo")
        self.assertEqual(_decode_json_string(r"https:\/\/cdn.example\/post.jpg"), "https://cdn.example/post.jpg")

    def test_image_metadata_fetch_tries_embed_page_after_page_error(self):
        html_text = '<meta property="og:image" content="https://cdn.example/post.jpg">'

        with patch("instagram_handler._fetch_html", side_effect=[OSError("blocked"), html_text]) as fetch_html:
            metadata = _fetch_instagram_image_metadata("https://www.instagram.com/p/abc123/")

        self.assertEqual(metadata["thumbnail"], "https://cdn.example/post.jpg")
        self.assertEqual(fetch_html.call_count, 2)


if __name__ == "__main__":
    unittest.main()
