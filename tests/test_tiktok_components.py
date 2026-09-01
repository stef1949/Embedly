import asyncio
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from handlers.media import MediaProcessingConfig
from handlers.tiktok import process_tiktok_links
from persistence import MessageOwnership
from services.downloaders import DownloadResult
from tiktok_handler import (
    extract_tiktok_post,
    format_compact_number,
    resolve_tiktok_icon,
    download_tiktok_video,
)
from views import TikTokCardView, configure_view_context


def build_result(**metadata):
    return DownloadResult(
        success=True,
        filepath="video.mp4",
        title="Fallback title",
        metadata=metadata,
    )


def build_post(**overrides):
    metadata = {
        "uploader": "Keta Creator",
        "uploader_id": "keta.creator",
        "webpage_url": "https://www.tiktok.com/@keta.creator/video/123",
        "description": "A test caption",
        "like_count": 784600,
        "comment_count": 6200,
        "share_count": 329700,
    }
    metadata.update(overrides)
    return extract_tiktok_post(
        build_result(**metadata),
        "https://www.tiktok.com/@keta.creator/video/123",
    )


class TikTokMetadataTests(unittest.TestCase):
    def test_compact_number_formatting(self):
        self.assertEqual(format_compact_number(999), "999")
        self.assertEqual(format_compact_number(1234), "1.2K")
        self.assertEqual(format_compact_number(1_000_000), "1M")
        self.assertEqual(format_compact_number(784_600), "784.6K")
        self.assertEqual(format_compact_number(999_999), "1M")

    def test_missing_metadata_has_safe_fallbacks(self):
        post = extract_tiktok_post(
            DownloadResult(success=True, filepath="video.mp4", metadata={}),
            "https://www.tiktok.com/@fallback/video/123",
        )

        self.assertEqual(post.display_name, "TikTok creator")
        self.assertEqual(post.handle, "fallback")
        self.assertEqual(post.engagement_text, "Engagement statistics unavailable")
        self.assertIsNone(post.transcript)

    def test_untrusted_metadata_urls_are_ignored(self):
        post = build_post(
            webpage_url="https://evil.invalid/phish",
            uploader_url="https://evil.invalid/creator",
        )

        self.assertEqual(post.original_url, "https://www.tiktok.com/@keta.creator/video/123")
        self.assertEqual(post.creator_url, "https://www.tiktok.com/@keta.creator")

    def test_user_markdown_and_mentions_are_escaped(self):
        post = build_post(
            uploader="**bold** <@123456789012345678>",
            description="ping @everyone and [link](https://evil.invalid)",
        )

        self.assertIn(r"\*\*bold\*\*", post.creator_display)
        self.assertNotIn("<@123456789012345678>", post.creator_display)
        self.assertIn(r"\[link]", post.information_text)
        self.assertNotIn("@everyone", post.information_text)

    def test_transcript_uses_downloaded_caption_data(self):
        post = build_post(
            requested_subtitles={
                "en": {
                    "data": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello @everyone\n"
                }
            }
        )

        self.assertIn("Hello", post.transcript)
        self.assertNotIn("@everyone", post.transcript)

    def test_custom_emoji_requires_discord_custom_emoji_syntax(self):
        self.assertEqual(resolve_tiktok_icon("<:tiktok:123456789012345678>"), "<:tiktok:123456789012345678>")
        self.assertEqual(resolve_tiktok_icon("**unsafe**"), "🎵")

    def test_tiktok_download_consumes_available_caption_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = os.path.join(folder, "123.mp4")
            subtitle_path = os.path.join(folder, "123.en.vtt")

            class FakeYoutubeDL:
                instances = []

                def __init__(self, params):
                    self.params = params
                    self.instances.append(self)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def extract_info(self, url, download):
                    if not download:
                        return {
                            "id": "123",
                            "title": "Captioned post",
                            "ext": "mp4",
                            "subtitles": {"en": [{"ext": "vtt", "url": "https://example.invalid/captions"}]},
                        }
                    with open(video_path, "wb") as video:
                        video.write(b"video")
                    with open(subtitle_path, "w", encoding="utf-8") as subtitle:
                        subtitle.write("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nDownloaded caption")
                    return {
                        "id": "123",
                        "title": "Captioned post",
                        "ext": "mp4",
                        "requested_subtitles": {"en": {"ext": "vtt", "filepath": subtitle_path}},
                    }

                def prepare_filename(self, info):
                    return video_path

            with patch("services.downloaders.yt_dlp.YoutubeDL", FakeYoutubeDL):
                result = download_tiktok_video(
                    "https://www.tiktok.com/@creator/video/123",
                    output_folder=folder,
                )

            self.assertTrue(result.success)
            self.assertIn("Downloaded caption", result.metadata["__embedly_transcript"])
            self.assertFalse(os.path.exists(subtitle_path))
            self.assertTrue(FakeYoutubeDL.instances[0].params["writesubtitles"])
            os.remove(video_path)


class TikTokLayoutTests(unittest.TestCase):
    def test_components_v2_layout_order_and_attachment_reference(self):
        attachment = discord.File(io.BytesIO(b"video"), filename="tiktok_video.mp4")
        try:
            view = TikTokCardView(
                post=build_post(),
                media=attachment,
                icon="🎵",
                timeout=None,
            )
            components = view.to_components()
        finally:
            attachment.close()

        self.assertTrue(view.is_persistent())
        self.assertEqual([component["type"] for component in components], [17])
        children = components[0]["components"]
        self.assertEqual([component["type"] for component in children], [10, 12, 14, 10, 10, 1])
        self.assertEqual(
            children[1]["items"][0]["media"]["url"],
            "attachment://tiktok_video.mp4",
        )
        self.assertTrue(children[2]["divider"])
        self.assertIn("Open in TikTok", children[3]["content"])
        self.assertIn("About Embedly", children[3]["content"])
        self.assertEqual(children[4]["content"], "♥ 784.6K   💬 6.2K   🔁 329.7K")
        self.assertEqual(children[5]["components"][1]["label"], "Transcript")


class FakeSentMessage:
    next_id = 1000

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
    def __init__(self, guild):
        self.id = 10
        self.guild = guild
        self.author = SimpleNamespace(id=400)
        self.channel = FakeChannel(guild)
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


class TikTokSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_card_reply_has_no_legacy_content_or_embed(self):
        guild = SimpleNamespace(id=300)
        message = FakeSourceMessage(guild)
        ownership_calls = []

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as video:
                video.write(b"video")

            def downloader(url, output_folder=None):
                return DownloadResult(
                    success=True,
                    filepath=path,
                    metadata={
                        "uploader": "Creator",
                        "uploader_id": "creator",
                        "webpage_url": url,
                        "like_count": 1234,
                    },
                )

            def record_ownership(**kwargs):
                self.assertFalse(message.deleted)
                ownership_calls.append(kwargs)

            processed = await process_tiktok_links(
                message=message,
                urls=["https://www.tiktok.com/@creator/video/123"],
                url_validator=lambda url: url,
                downloader=downloader,
                compressor=lambda *args, **kwargs: None,
                fallback_view_factory=lambda url: SimpleNamespace(original_author_id=None, message=None),
                ownership_recorder=record_ownership,
                semaphore=asyncio.Semaphore(1),
                config=processing_config(folder),
                icon="🎵",
            )

        self.assertEqual(processed, 1)
        self.assertTrue(message.deleted)
        self.assertEqual(len(message.replies), 1)
        kwargs = message.replies[0]["kwargs"]
        self.assertNotIn("content", kwargs)
        self.assertNotIn("embed", kwargs)
        self.assertFalse(kwargs["mention_author"])
        self.assertIsInstance(kwargs["file"], discord.File)
        self.assertIsInstance(kwargs["view"], discord.ui.LayoutView)
        gallery_url = kwargs["view"].to_components()[0]["components"][1]["items"][0]["media"]["url"]
        self.assertEqual(gallery_url, f"attachment://{kwargs['file'].filename}")
        self.assertEqual(ownership_calls[0]["message_type"], "tiktok_card")
        self.assertEqual(ownership_calls[0]["original_author_id"], 400)

    async def test_download_failure_uses_tnktok_reply_fallback(self):
        guild = SimpleNamespace(id=300)
        message = FakeSourceMessage(guild)

        processed = await process_tiktok_links(
            message=message,
            urls=["https://www.tiktok.com/@creator/video/123"],
            url_validator=lambda url: url,
            downloader=lambda *args, **kwargs: DownloadResult(success=False, error="failed"),
            compressor=lambda *args, **kwargs: None,
            fallback_view_factory=lambda url: SimpleNamespace(original_author_id=None, message=None),
            ownership_recorder=lambda **kwargs: None,
            semaphore=asyncio.Semaphore(1),
            config=processing_config(tempfile.gettempdir()),
            icon="🎵",
        )

        self.assertEqual(processed, 1)
        self.assertTrue(message.deleted)
        self.assertEqual(len(message.replies), 1)
        self.assertIn("https://tnktok.com/@creator/video/123", message.replies[0]["kwargs"]["content"])
        self.assertFalse(message.replies[0]["kwargs"]["mention_author"])

    async def test_source_is_preserved_when_ownership_cannot_be_recorded(self):
        guild = SimpleNamespace(id=300)
        message = FakeSourceMessage(guild)

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as video:
                video.write(b"video")

            def fail_recording(**kwargs):
                raise RuntimeError("database unavailable")

            processed = await process_tiktok_links(
                message=message,
                urls=["https://www.tiktok.com/@creator/video/123"],
                url_validator=lambda url: url,
                downloader=lambda *args, **kwargs: DownloadResult(success=True, filepath=path),
                compressor=lambda *args, **kwargs: None,
                fallback_view_factory=lambda url: SimpleNamespace(original_author_id=None, message=None),
                ownership_recorder=fail_recording,
                semaphore=asyncio.Semaphore(1),
                config=processing_config(folder),
                icon="🎵",
            )

        self.assertEqual(processed, 0)
        self.assertFalse(message.deleted)
        self.assertTrue(all(reply["message"].deleted for reply in message.replies))


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


class TikTokCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ownership = MessageOwnership(
            message_id=100,
            channel_id=200,
            guild_id=300,
            original_author_id=400,
            message_type="tiktok_card",
            details="Available post details",
            transcript=None,
        )
        configure_view_context(
            is_admin=lambda user_id: False,
            user_emulation_preferences={},
            default_emulation=True,
            fetch_user=self._fetch_user,
            state=FakeStore(self.ownership),
        )

    async def _fetch_user(self, user_id):
        return SimpleNamespace(id=user_id, name="user")

    def interaction(self, user_id=400):
        guild = SimpleNamespace(id=300, owner_id=999, get_member=lambda member_id: None)
        channel = SimpleNamespace(id=200)
        message = SimpleNamespace(id=100, channel=channel, guild=guild)
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            guild=guild,
            guild_id=300,
            channel_id=200,
            message=message,
            response=FakeResponse(),
        )

    async def test_information_and_unavailable_transcript_are_ephemeral(self):
        view = TikTokCardView.persistent_placeholder()
        interaction = self.interaction()

        await view.show_information(interaction)
        await view.show_transcript(interaction)

        self.assertEqual(interaction.response.messages[0][0], "Available post details")
        self.assertEqual(interaction.response.messages[1][0], "Transcript unavailable for this post")
        self.assertTrue(all(kwargs["ephemeral"] for _, kwargs in interaction.response.messages))

    async def test_available_persisted_transcript_is_returned(self):
        ownership = MessageOwnership(
            message_id=100,
            channel_id=200,
            guild_id=300,
            original_author_id=400,
            message_type="tiktok_card",
            details="Available post details",
            transcript="Downloaded caption text",
        )
        configure_view_context(
            is_admin=lambda user_id: False,
            user_emulation_preferences={},
            default_emulation=True,
            fetch_user=self._fetch_user,
            state=FakeStore(ownership),
        )
        view = TikTokCardView.persistent_placeholder()
        interaction = self.interaction()

        await view.show_transcript(interaction)

        self.assertEqual(interaction.response.messages[0][0], "Downloaded caption text")

    async def test_unknown_user_is_denied_from_callbacks(self):
        view = TikTokCardView.persistent_placeholder()
        interaction = self.interaction(user_id=401)

        await view.show_information(interaction)

        self.assertEqual(interaction.response.messages[0][0], "You are not allowed to use this control.")


if __name__ == "__main__":
    unittest.main()
