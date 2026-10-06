import unittest
from unittest.mock import patch

from utils.urls import extract_supported_links, parse_supported_url, contains_unhandled_youtube_link
from youtube_handler import download_youtube_video
from services.downloaders import DownloadResult
from workflow_helpers import WorkflowFixture, URLS, embedbot, close_module

tearDownModule = close_module

REGULAR_LINKS = (
    'https://www.youtube.com/watch?v=abcdefghijk',
    'https://youtu.be/abcdefghijk?si=share',
    'https://m.youtube.com/watch?v=abcdefghijk',
    'https://music.youtube.com/watch?v=abcdefghijk',
    'https://www.youtube.com/live/abcdefghijk',
    'https://www.youtube.com/embed/abcdefghijk',
    'https://www.youtube.com/playlist?list=example',
    'https://www.youtube-nocookie.com/embed/abcdefghijk',
)


class ShortsUrlTests(unittest.TestCase):
    def test_only_explicit_shorts_are_supported_and_canonicalized(self):
        short = 'https://www.youtube.com/shorts/abcdefghijk'
        content = f'<{short}?si=one> ||https://m.youtube.com/shorts/abcdefghijk/?feature=share||'
        links = extract_supported_links(content)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, short)
        self.assertTrue(links[0].spoiler)
        self.assertFalse(contains_unhandled_youtube_link(content))
        for url in REGULAR_LINKS:
            with self.subTest(url=url):
                self.assertEqual(extract_supported_links(url), [])
                self.assertTrue(contains_unhandled_youtube_link(f'[Video]({url})'))
                with self.assertRaises(ValueError):
                    parse_supported_url(url)

    def test_downloader_rejects_regular_video_before_network_request(self):
        with patch('youtube_handler.download_video') as download:
            for url in REGULAR_LINKS:
                self.assertFalse(download_youtube_video(url).success)
            download.assert_not_called()
            download.return_value = DownloadResult(True)
            self.assertTrue(download_youtube_video(URLS['youtube'] + '?si=share').success)
            download.assert_called_once_with(URLS['youtube'], output_folder=None, download_subtitles=True)


class ShortsWorkflowTests(WorkflowFixture, unittest.IsolatedAsyncioTestCase):
    async def test_regular_youtube_is_untouched_in_every_source_mode(self):
        for mode in ('delete', 'suppress', 'keep'):
            embedbot.server_settings[789] = {'source_behavior': mode}
            for url in REGULAR_LINKS:
                message = self.message(url)
                self.assertEqual(await embedbot.process_message(message), 'no_links')
                message.reply.assert_not_awaited()
                message.delete.assert_not_awaited()
                message.edit.assert_not_awaited()
        self.assertEqual(self.download_calls, [])

    async def test_mixed_message_preserves_all_source_embeds_even_after_success_and_retry(self):
        source_id = 100
        for mode in ('delete', 'suppress'):
            embedbot.server_settings[789] = {'source_behavior': mode}
            for url in REGULAR_LINKS:
                source_id += 1
                message = self.message(f"{URLS['youtube']} {URLS['instagram']} [Video]({url})")
                message.id = source_id
                with patch.object(embedbot.runtime_state, 'allow_user_action', return_value=True), patch.object(embedbot, 'check_global_rate_limit', return_value=True):
                    self.assertEqual(await embedbot.process_message(message), 'published_source_retained')
                    self.assertEqual(await embedbot.process_message(message), 'published_source_retained')
                self.assertEqual(message.reply.await_count, 2)
                message.delete.assert_not_awaited()
                message.edit.assert_not_awaited()
        self.assertTrue(all('/shorts/' in url or 'instagram.com' in url for url in self.download_calls))

    async def test_short_only_keeps_configured_cleanup(self):
        for index, mode in enumerate(('delete', 'suppress')):
            message = self.message(URLS['youtube'])
            message.id = 200 + index
            embedbot.server_settings[789] = {'source_behavior': mode}
            with patch.object(embedbot.runtime_state, 'allow_user_action', return_value=True), patch.object(embedbot, 'check_global_rate_limit', return_value=True):
                self.assertEqual(await embedbot.process_message(message), 'complete')
            message.reply.assert_awaited_once()
            if mode == 'delete':
                message.delete.assert_awaited_once()
                message.edit.assert_not_awaited()
            else:
                message.delete.assert_not_awaited()
                message.edit.assert_awaited_once_with(suppress=True)
