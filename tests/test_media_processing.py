import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from handlers.media import MediaProcessingConfig, process_media_links
from services.downloaders import DownloadResult, detect_media_type


class FakeSentMessage:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        message = FakeSentMessage()
        record = {"args": args, "kwargs": kwargs, "message": message}
        if "file" in kwargs:
            record["filename"] = kwargs["file"].filename
            kwargs["file"].close()
        self.sent.append(record)
        return message


class FakeMessage:
    def __init__(self):
        self.id = 123
        self.author = SimpleNamespace(id=456)
        self.channel = FakeChannel()
        self.deleted = False

    async def delete(self):
        self.deleted = True


def media_config(upload_limit_bytes: int) -> MediaProcessingConfig:
    return MediaProcessingConfig(
        temp_directory=tempfile.gettempdir(),
        upload_limit_bytes=upload_limit_bytes,
        ytdlp_timeout_seconds=5,
        ffmpeg_timeout_seconds=5,
        ffprobe_timeout_seconds=5,
        ffmpeg_headroom_ratio=0.95,
        use_nvidia_gpu=False,
    )


def write_temp_file(folder: str, suffix: str, data: bytes) -> str:
    handle, path = tempfile.mkstemp(suffix=suffix, dir=folder)
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    return path


class MediaProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_image_uploads_with_image_label(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_temp_file(folder, ".jpg", b"image bytes")
            message = FakeMessage()
            views = []

            def downloader(url, output_folder=None):
                return DownloadResult(success=True, filepath=path, title="Still image", media_type="image")

            def compressor(*args, **kwargs):
                raise AssertionError("image downloads should not use video compression")

            def view_factory(url):
                view = SimpleNamespace(original_author_id=None, message=None)
                views.append(view)
                return view

            processed = await process_media_links(
                message=message,
                urls=["https://www.instagram.com/p/abc123/"],
                source_name="Instagram",
                icon="IG",
                url_validator=lambda url: url,
                downloader=downloader,
                compressor=compressor,
                view_factory=view_factory,
                semaphore=asyncio.Semaphore(1),
                config=media_config(upload_limit_bytes=1024),
                default_media_label="media",
            )

            self.assertEqual(processed, 1)
            self.assertTrue(message.deleted)
            self.assertEqual(views[0].original_author_id, message.author.id)
            upload = message.channel.sent[1]["kwargs"]
            self.assertIn("Instagram image shared", upload["content"])
            self.assertEqual(message.channel.sent[1]["filename"], os.path.basename(path))

    async def test_oversized_instagram_image_does_not_call_video_compressor(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_temp_file(folder, ".jpg", b"image bytes")
            message = FakeMessage()
            compression_calls = []

            def downloader(url, output_folder=None):
                return DownloadResult(success=True, filepath=path, title="Still image", media_type="image")

            def compressor(*args, **kwargs):
                compression_calls.append((args, kwargs))
                return None

            with self.assertLogs("handlers.media", level="WARNING") as logs:
                processed = await process_media_links(
                    message=message,
                    urls=["https://www.instagram.com/p/abc123/"],
                    source_name="Instagram",
                    icon="IG",
                    url_validator=lambda url: url,
                    downloader=downloader,
                    compressor=compressor,
                    view_factory=lambda url: SimpleNamespace(original_author_id=None, message=None),
                    semaphore=asyncio.Semaphore(1),
                    config=media_config(upload_limit_bytes=1),
                    default_media_label="media",
                )

            self.assertEqual(processed, 0)
            self.assertFalse(message.deleted)
            self.assertEqual(compression_calls, [])
            self.assertEqual(len(message.channel.sent), 1)
            self.assertIn("Instagram image exceeds upload limit", logs.output[0])


class MediaTypeDetectionTests(unittest.TestCase):
    def test_detect_media_type_from_file_extension(self):
        self.assertEqual(detect_media_type("post.jpg", {}), "image")
        self.assertEqual(detect_media_type("clip.mp4", {}), "video")

    def test_detect_media_type_from_mime_type(self):
        self.assertEqual(detect_media_type(None, {"mime_type": "image/jpeg"}), "image")
        self.assertEqual(detect_media_type(None, {"mime_type": "video/mp4"}), "video")


if __name__ == "__main__":
    unittest.main()
