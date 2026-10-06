from handlers.media import process_native_media_links
from tiktok_handler import extract_tiktok_post
from views import TikTokCardView


async def process_tiktok_links(**kwargs):
    return await process_native_media_links(
        source_name="TikTok", platform_key="tiktok", post_factory=extract_tiktok_post,
        card_view_factory=TikTokCardView, **kwargs,
    )
