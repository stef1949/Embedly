import asyncio
import io
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from config import BotConfig
from persistence import SQLiteStateStore
from runtime_state import RuntimeState
from services.downloaders import DownloadResult
from services.validation import validate_media

with (
    patch('config.load_config', return_value=BotConfig(discord_token='test-token', state_database_path=':memory:')),
    patch('logging.FileHandler', return_value=logging.NullHandler()),
    patch('logging.basicConfig'),
):
    import embedbot

buffer = io.BytesIO()
Image.new('RGB', (2, 2), 'red').save(buffer, format='PNG')
PNG = buffer.getvalue()
URLS = {
    'twitter': 'https://x.com/user/status/123',
    'tiktok': 'https://www.tiktok.com/@user/video/123',
    'instagram': 'https://www.instagram.com/p/abc123/',
    'youtube': 'https://www.youtube.com/watch?v=abc123',
}


def close_module():
    embedbot.state.close()


class WorkflowFixture:
    def setUp(self):
        self.store = SQLiteStateStore(':memory:')
        self.addCleanup(self.store.close)
        self.results = {}
        self.files = []
        self.download_calls = []
        self.sent = []
        for name, value in {
            'state': self.store, 'runtime_state': RuntimeState(), 'links_processed': 0,
            'DEFAULT_EMULATION': False, 'user_emulation_preferences': {},
            'user_media_details_preferences': {}, 'server_settings': {},
            'BANNED_USERS': set(), 'SERVER_BLACKLIST': set(), 'logger': Mock(),
            'media_semaphore': asyncio.Semaphore(2),
        }.items():
            self.patch_object(embedbot, name, value)
        patcher = patch('handlers.media.run_blocking', AsyncMock(side_effect=self.worker))
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_object(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def worker(self, func, *args, timeout_seconds=None, **kwargs):
        if func is validate_media:
            return validate_media(*args, **kwargs)
        url = args[0]
        self.download_calls.append(url)
        result = self.results.get(url, PNG)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, DownloadResult):
            return result
        path = Path(kwargs['output_folder']) / 'media.png'
        path.write_bytes(result)
        self.files.append(path)
        return DownloadResult(True, str(path), media_type='image')

    def message(self, content):
        async def reply(**kwargs):
            sent = SimpleNamespace(id=1000 + len(self.sent), delete=AsyncMock())
            self.sent.append(sent)
            return sent
        message = SimpleNamespace(
            id=123, content=content, author=SimpleNamespace(
                id=456, bot=False, display_name='Submitter',
                display_avatar=SimpleNamespace(url='https://example.com/avatar.png'),
            ),
            guild=SimpleNamespace(id=789, me=SimpleNamespace(id=42)), webhook_id=None,
            channel=SimpleNamespace(id=321, send=AsyncMock()),
            reply=AsyncMock(side_effect=reply), delete=AsyncMock(), edit=AsyncMock(),
        )
        message.channel.fetch_message = AsyncMock(return_value=message)
        return message
