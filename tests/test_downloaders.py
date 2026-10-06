from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.downloaders import download_media, _limit_download, MAX_DOWNLOAD_BYTES
import yt_dlp


class DownloaderOutputTests(unittest.TestCase):
    def test_subtitle_or_partial_is_not_substituted_for_missing_media(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, '123.en.vtt').write_text('WEBVTT')
            Path(folder, '123.mp4.part').write_bytes(b'partial')
            class Downloader:
                def __init__(self, params):
                    self.params = params
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def extract_info(self, url, download):
                    return {'id': '123', 'ext': 'mp4'}
                def prepare_filename(self, info):
                    return str(Path(folder, '123.mp4'))
            with patch('services.downloaders.yt_dlp.YoutubeDL', Downloader):
                result = download_media('https://youtu.be/test', output_folder=folder)
            self.assertFalse(result.success)
            self.assertIsNone(result.filepath)

    def test_transfer_budget_stops_oversized_download(self):
        with self.assertRaises(yt_dlp.utils.DownloadError):
            _limit_download({'downloaded_bytes': MAX_DOWNLOAD_BYTES + 1})
