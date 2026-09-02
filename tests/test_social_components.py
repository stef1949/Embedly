import asyncio
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from handlers.media import MediaProcessingConfig, process_native_media_links
from handlers.twitter import send_twitter_rewrite_message
from persistence import MessageOwnership
from services.downloaders import DownloadResult
from social_cards import (
    INSTAGRAM_FALLBACK_ICON,
    TWITTER_FALLBACK_ICON,
    extract_instagram_post,
    extract_twitter_post,
    extract_youtube_post,
    resolve_platform_icon,
)
from utils.urls import RewriteResult
from views import (
    InstagramCardView,
    InstagramControlView,
    TwitterCardView,
    YouTubeCardView,
    configure_view_context,
)


class SocialMetadataTests(unittest.TestCase):
    def test_instagram_metadata_is_validated_escaped_and_compacted(self):
        result = DownloadResult(
            success=True,
            filepath="instagram.mp4",
            metadata={
                "uploader": "**Creator** <@123456789012345678>",
                "uploader_id": "creator.name",
                "uploader_url": "https://evil.invalid/creator",
                "webpage_url": "https://evil.invalid/post",
                "description": "hello @everyone [link](https://evil.invalid)",
                "like_count": 1234,
                "comment_count": 1_000_000,
                "view_count": 999,
                "__embedly_transcript": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCaption @everyone",
            },
        )

        post = extract_instagram_post(result, "https://www.instagram.com/reel/abc123/")

        self.assertEqual(post.original_url, "https://www.instagram.com/reel/abc123/")
        self.assertEqual(post.creator_url, "https://www.instagram.com/creator.name/")
        self.assertIn(r"\*\*Creator\*\*", post.creator_display)
        self.assertNotIn("<@123456789012345678>", post.creator_display)
        self.assertEqual(post.engagement_text, "♥ 1.2K   💬 1M   ▶ 999")
        self.assertNotIn("@everyone", post.information_text)
        self.assertNotIn("@everyone", post.transcript)

    def test_youtube_missing_metadata_has_safe_fallbacks(self):
        post = extract_youtube_post(
            DownloadResult(success=True, filepath="youtube.mp4", metadata={}),
            "https://youtu.be/abc123",
        )

        self.assertEqual(post.display_name, "YouTube creator")
        self.assertEqual(post.engagement_text, "Engagement statistics unavailable")
        self.assertIsNone(post.transcript)

    def test_youtube_handle_is_derived_only_from_trusted_metadata(self):
        post = extract_youtube_post(
            DownloadResult(
                success=True,
                metadata={
                    "channel": "Example Channel",
                    "channel_url": "https://www.youtube.com/@example",
                    "view_count": 1_000_000,
                },
            ),
            "https://www.youtube.com/watch?v=abc123",
        )

        self.assertEqual(post.handle, "example")
        self.assertEqual(post.creator_url, "https://www.youtube.com/@example")
        self.assertEqual(post.engagement_text, "▶ 1M")

    def test_twitter_card_rejects_untrusted_hosts(self):
        with self.assertRaises(ValueError):
            extract_twitter_post("https://evil.invalid/user/status/1")

    def test_platform_emoji_requires_full_custom_emoji_syntax(self):
        custom = "<:instagram:1544047809169195039>"
        self.assertEqual(resolve_platform_icon(custom, INSTAGRAM_FALLBACK_ICON), custom)
        self.assertEqual(resolve_platform_icon("**unsafe**", TWITTER_FALLBACK_ICON), TWITTER_FALLBACK_ICON)


class SocialLayoutTests(unittest.TestCase):
    def test_instagram_and_youtube_layouts_reference_the_uploaded_attachment(self):
        cases = (
            (
                InstagramCardView,
                extract_instagram_post(
                    DownloadResult(success=True, metadata={"uploader": "Creator"}),
                    "https://www.instagram.com/reel/abc123/",
                ),
                "instagram_media.mp4",
                "Open in Instagram",
            ),
            (
                YouTubeCardView,
                extract_youtube_post(
                    DownloadResult(success=True, metadata={"channel": "Creator"}),
                    "https://www.youtube.com/watch?v=abc123",
                ),
                "youtube_media.mp4",
                "Open in YouTube",
            ),
        )
        for view_class, post, filename, open_label in cases:
            with self.subTest(platform=post.platform_key):
                attachment = discord.File(io.BytesIO(b"video"), filename=filename)
                try:
                    view = view_class(post=post, media=attachment, icon="ICON", timeout=None)
                    components = view.to_components()
                finally:
                    attachment.close()

                self.assertTrue(view.is_persistent())
                self.assertEqual([component["type"] for component in components], [17])
                children = components[0]["components"]
                self.assertEqual([component["type"] for component in children], [10, 12, 14, 10, 10, 1])
                self.assertEqual(children[1]["items"][0]["media"]["url"], f"attachment://{filename}")
                self.assertIn(open_label, children[3]["content"])
                self.assertEqual(children[5]["components"][1]["label"], "Transcript")

    def test_twitter_layout_is_native_link_card_and_preserves_spoiler(self):
        view = TwitterCardView(
            post=extract_twitter_post("https://vxtwitter.com/creator/status/123"),
            icon="ICON",
            spoiler=True,
            timeout=None,
        )
        components = view.to_components()
        children = components[0]["components"]

        self.assertTrue(view.is_persistent())
        self.assertTrue(components[0]["spoiler"])
        self.assertEqual([component["type"] for component in children], [10, 14, 10, 10, 1])
        self.assertIn("Open on X", children[2]["content"])
        self.assertEqual(children[4]["components"][0]["custom_id"], "embedly:twitter:information")

    def test_optional_media_details_add_one_compact_text_row(self):
        post = extract_youtube_post(
            DownloadResult(
                success=True,
                metadata={
                    "upload_date": "20260901",
                    "duration": 65,
                    "width": 1920,
                    "height": 1080,
                },
            ),
            "https://www.youtube.com/watch?v=abc123",
        )
        attachment = discord.File(io.BytesIO(b"video"), filename="youtube_media.mp4")
        try:
            view = YouTubeCardView(
                post=post,
                media=attachment,
                icon="ICON",
                include_details=True,
            )
            children = view.to_components()[0]["components"]
        finally:
            attachment.close()

        self.assertEqual([component["type"] for component in children], [10, 12, 14, 10, 10, 10, 1])
        self.assertEqual(children[5]["content"], "Posted 2026-09-01  •  1:05  •  1920×1080")


class FakeSentMessage:
    next_id = 5000

    def __init__(self, channel, guild):
        self.id = FakeSentMessage.next_id
        FakeSentMessage.next_id += 1
        self.channel = channel
        self.guild = guild
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, guild):
        self.id = 200
        self.guild = guild
        self.sent = []

    async def send(self, *args, **kwargs):
        sent = FakeSentMessage(self, self.guild)
        self.sent.append({"args": args, "kwargs": kwargs, "message": sent})
        return sent


class FakeSourceMessage:
    def __init__(self):
        self.id = 10
        self.guild = SimpleNamespace(id=300)
        self.author = SimpleNamespace(
            id=400,
            display_name="Source user",
            display_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
        )
        self.channel = FakeChannel(self.guild)
        self.replies = []
        self.deleted = False

    async def reply(self, *args, **kwargs):
        sent = FakeSentMessage(self.channel, self.guild)
        self.replies.append({"args": args, "kwargs": kwargs, "message": sent})
        return sent

    async def delete(self):
        self.deleted = True


def processing_config(folder):
    return MediaProcessingConfig(
        temp_directory=folder,
        upload_limit_bytes=1024 * 1024,
        ytdlp_timeout_seconds=5,
        ffmpeg_timeout_seconds=5,
        ffprobe_timeout_seconds=5,
        ffmpeg_headroom_ratio=0.95,
        use_nvidia_gpu=False,
    )


class SocialSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_native_card_uses_only_file_and_view(self):
        message = FakeSourceMessage()
        ownership_calls = []
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as video:
                video.write(b"video")

            processed = await process_native_media_links(
                message=message,
                urls=["https://www.instagram.com/reel/abc123/"],
                source_name="Instagram",
                platform_key="instagram",
                icon="ICON",
                url_validator=lambda url: url,
                downloader=lambda *args, **kwargs: DownloadResult(
                    success=True,
                    filepath=path,
                    metadata={"uploader": "Creator", "like_count": 1234},
                ),
                compressor=lambda *args, **kwargs: None,
                post_factory=extract_instagram_post,
                card_view_factory=InstagramCardView,
                fallback_view_factory=lambda url: InstagramControlView(url),
                ownership_recorder=lambda **kwargs: ownership_calls.append(kwargs),
                semaphore=asyncio.Semaphore(1),
                config=processing_config(folder),
            )

        self.assertEqual(processed, 1)
        self.assertTrue(message.deleted)
        kwargs = message.replies[0]["kwargs"]
        self.assertNotIn("content", kwargs)
        self.assertNotIn("embed", kwargs)
        self.assertFalse(kwargs["mention_author"])
        self.assertIsInstance(kwargs["file"], discord.File)
        self.assertIsInstance(kwargs["view"], discord.ui.LayoutView)
        gallery_url = kwargs["view"].to_components()[0]["components"][1]["items"][0]["media"]["url"]
        self.assertEqual(gallery_url, f"attachment://{kwargs['file'].filename}")
        self.assertEqual(ownership_calls[0]["message_type"], "instagram_card")
        self.assertEqual(ownership_calls[0]["original_author_id"], 400)

    async def test_media_download_failure_uses_validated_link_fallback(self):
        message = FakeSourceMessage()

        processed = await process_native_media_links(
            message=message,
            urls=["https://www.instagram.com/p/abc123/"],
            source_name="Instagram",
            platform_key="instagram",
            icon="ICON",
            url_validator=lambda url: url,
            downloader=lambda *args, **kwargs: DownloadResult(success=False, error="failed"),
            compressor=lambda *args, **kwargs: None,
            post_factory=extract_instagram_post,
            card_view_factory=InstagramCardView,
            fallback_view_factory=lambda url: InstagramControlView(url),
            ownership_recorder=lambda **kwargs: None,
            semaphore=asyncio.Semaphore(1),
            config=processing_config(tempfile.gettempdir()),
        )

        self.assertEqual(processed, 1)
        self.assertTrue(message.deleted)
        self.assertIn("https://www.instagram.com/p/abc123/", message.replies[0]["kwargs"]["content"])

    async def test_media_ownership_failure_removes_replacements_and_preserves_source(self):
        message = FakeSourceMessage()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as video:
                video.write(b"video")

            processed = await process_native_media_links(
                message=message,
                urls=["https://www.instagram.com/reel/abc123/"],
                source_name="Instagram",
                platform_key="instagram",
                icon="ICON",
                url_validator=lambda url: url,
                downloader=lambda *args, **kwargs: DownloadResult(success=True, filepath=path),
                compressor=lambda *args, **kwargs: None,
                post_factory=extract_instagram_post,
                card_view_factory=InstagramCardView,
                fallback_view_factory=lambda url: InstagramControlView(url),
                ownership_recorder=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
                semaphore=asyncio.Semaphore(1),
                config=processing_config(folder),
            )

        self.assertEqual(processed, 0)
        self.assertFalse(message.deleted)
        self.assertTrue(all(reply["message"].deleted for reply in message.replies))

    async def test_delete_source_can_be_coordinated_by_caller(self):
        message = FakeSourceMessage()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as video:
                video.write(b"video")

            processed = await process_native_media_links(
                message=message,
                urls=["https://www.youtube.com/watch?v=abc123"],
                source_name="YouTube",
                platform_key="youtube",
                icon="ICON",
                url_validator=lambda url: url,
                downloader=lambda *args, **kwargs: DownloadResult(success=True, filepath=path),
                compressor=lambda *args, **kwargs: None,
                post_factory=extract_youtube_post,
                card_view_factory=YouTubeCardView,
                fallback_view_factory=lambda url: InstagramControlView(url),
                ownership_recorder=lambda **kwargs: None,
                semaphore=asyncio.Semaphore(1),
                config=processing_config(folder),
                delete_source=False,
            )

        self.assertEqual(processed, 1)
        self.assertFalse(message.deleted)

    async def test_twitter_native_send_has_no_content_or_embed(self):
        message = FakeSourceMessage()
        ownership_calls = []

        processed = await send_twitter_rewrite_message(
            message=message,
            rewrite_result=RewriteResult(
                rewritten_urls=["https://vxtwitter.com/creator/status/123"],
                spoiler_urls=[],
            ),
            should_emulate=True,
            icon="ICON",
            ownership_recorder=lambda **kwargs: ownership_calls.append(kwargs),
        )

        self.assertEqual(processed, 1)
        self.assertFalse(message.deleted)
        kwargs = message.replies[0]["kwargs"]
        self.assertNotIn("content", kwargs)
        self.assertNotIn("embed", kwargs)
        self.assertFalse(kwargs["mention_author"])
        self.assertIsInstance(kwargs["view"], TwitterCardView)
        self.assertEqual(ownership_calls[0]["message_type"], "twitter_card")

    async def test_twitter_card_failure_retains_legacy_rewrite_fallback(self):
        message = FakeSourceMessage()
        with patch("handlers.twitter.TwitterCardView", side_effect=TypeError("unsupported")):
            processed = await send_twitter_rewrite_message(
                message=message,
                rewrite_result=RewriteResult(
                    rewritten_urls=["https://vxtwitter.com/creator/status/123"],
                    spoiler_urls=[],
                ),
                should_emulate=False,
                icon="ICON",
                ownership_recorder=lambda **kwargs: None,
            )

        self.assertEqual(processed, 1)
        self.assertEqual(len(message.channel.sent), 1)
        self.assertIn("https://vxtwitter.com/creator/status/123", message.channel.sent[0]["args"][0])

    async def test_twitter_ownership_failure_removes_all_replacements(self):
        message = FakeSourceMessage()

        processed = await send_twitter_rewrite_message(
            message=message,
            rewrite_result=RewriteResult(
                rewritten_urls=["https://vxtwitter.com/creator/status/123"],
                spoiler_urls=[],
            ),
            should_emulate=False,
            icon="ICON",
            ownership_recorder=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
        )

        self.assertEqual(processed, 0)
        self.assertTrue(message.replies[0]["message"].deleted)
        self.assertTrue(message.channel.sent[0]["message"].deleted)
        self.assertFalse(message.deleted)


class FakeStore:
    def __init__(self, ownership):
        self.ownership = ownership

    def get_message_ownership(self, message_id, channel_id, guild_id):
        if (message_id, channel_id, guild_id) == (100, 200, 300):
            return self.ownership
        return None

    def delete_message_ownership(self, message_id):
        return True


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content, **kwargs):
        self.messages.append((content, kwargs))


class SocialCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ownership = MessageOwnership(
            message_id=100,
            channel_id=200,
            guild_id=300,
            original_author_id=400,
            message_type="twitter_card",
            details="Trusted details",
            transcript=None,
        )
        configure_view_context(
            is_admin=lambda user_id: False,
            user_emulation_preferences={},
            default_emulation=True,
            fetch_user=self._fetch_user,
            state=FakeStore(ownership),
        )

    async def _fetch_user(self, user_id):
        return SimpleNamespace(id=user_id, name="user")

    def interaction(self, user_id):
        guild = SimpleNamespace(id=300, owner_id=999, get_member=lambda member_id: None)
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            guild=guild,
            guild_id=300,
            channel_id=200,
            message=SimpleNamespace(id=100, channel=SimpleNamespace(id=200), guild=guild),
            response=FakeResponse(),
        )

    async def test_persisted_details_and_unavailable_transcript_are_authorized(self):
        view = TwitterCardView.persistent_placeholder()
        interaction = self.interaction(400)

        await view.show_information(interaction)
        await view.show_transcript(interaction)

        self.assertEqual(interaction.response.messages[0][0], "Trusted details")
        self.assertEqual(interaction.response.messages[1][0], "Transcript unavailable for this post")
        self.assertTrue(all(kwargs["ephemeral"] for _, kwargs in interaction.response.messages))

    async def test_unknown_user_is_denied(self):
        view = TwitterCardView.persistent_placeholder()
        interaction = self.interaction(401)

        await view.show_information(interaction)

        self.assertEqual(interaction.response.messages[0][0], "You are not allowed to use this control.")


if __name__ == "__main__":
    unittest.main()
