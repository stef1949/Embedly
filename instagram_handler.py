from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import discord

from services.downloaders import DownloadResult, download_media
from services.media_embeds import build_media_metadata_embed

INSTAGRAM_COLOR = 0xE4405F
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic", "heif"}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heif",
}
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
IMAGE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

logger = logging.getLogger(__name__)


class MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "meta":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        name = (values.get("property") or values.get("name") or "").lower()
        content = values.get("content")
        if name and content:
            self.meta[name] = html.unescape(content)


def download_instagram_media(media_url: str, output_folder: str | None = None) -> DownloadResult:
    result = download_media(media_url, output_folder=output_folder)
    if result.success or not _should_try_image_fallback(result.error):
        return result

    image_result = download_instagram_image(media_url, output_folder=output_folder)
    if image_result.success:
        return image_result

    return DownloadResult(
        success=False,
        error=f"{result.error}; image fallback failed: {image_result.error}",
        media_type="image",
    )


def download_instagram_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_instagram_media(video_url, output_folder=output_folder)


def build_instagram_embed(result: DownloadResult, original_url: str, *, include_details: bool = False) -> discord.Embed:
    return build_media_metadata_embed(
        result,
        original_url,
        platform_name="Instagram",
        color=INSTAGRAM_COLOR,
        include_details=include_details,
    )


def download_instagram_image(media_url: str, output_folder: str | None = None) -> DownloadResult:
    output_folder = output_folder or tempfile.gettempdir()
    os.makedirs(output_folder, exist_ok=True)

    try:
        metadata = _fetch_instagram_image_metadata(media_url)
        image_url = metadata["thumbnail"]
        shortcode = _instagram_shortcode(media_url) or "image"
        filepath, mime_type = _download_image_file(
            image_url=image_url,
            output_folder=output_folder,
            filename_prefix=f"instagram_{shortcode}",
            referer=metadata["webpage_url"],
        )
        metadata["mime_type"] = mime_type
        metadata["ext"] = os.path.splitext(filepath)[1].lstrip(".").lower()
        title = metadata.get("title") or "Instagram image"
        return DownloadResult(
            success=True,
            filepath=filepath,
            title=title,
            metadata=metadata,
            media_type="image",
        )
    except (HTTPError, URLError, OSError, ValueError) as exc:
        logger.error("Instagram image download failed for %s: %s", media_url, exc)
        return DownloadResult(success=False, error=f"ImageDownloadError: {exc}", media_type="image")


def _should_try_image_fallback(error: str | None) -> bool:
    return "no video" in (error or "").lower()


def _fetch_instagram_image_metadata(media_url: str) -> dict[str, str]:
    last_error: Exception | None = None
    for candidate_url in _metadata_page_urls(media_url):
        try:
            html_text = _fetch_html(candidate_url)
        except (HTTPError, URLError, OSError) as exc:
            last_error = exc
            continue

        meta = _parse_meta_tags(html_text)
        image_url = meta.get("og:image") or _extract_json_image_url(html_text)
        if image_url:
            return {
                "title": meta.get("og:title") or "Instagram image",
                "description": meta.get("og:description") or meta.get("description") or "",
                "webpage_url": media_url,
                "original_url": media_url,
                "thumbnail": image_url,
            }
    if last_error:
        raise ValueError(f"No Instagram image metadata found: {last_error}")
    raise ValueError("No Instagram image metadata found")


def _metadata_page_urls(media_url: str) -> list[str]:
    parsed = urlsplit(media_url)
    scheme = parsed.scheme or "https"
    path = parsed.path.rstrip("/")
    page_url = urlunsplit((scheme, parsed.netloc, f"{path}/", "", ""))
    embed_url = urlunsplit((scheme, parsed.netloc, f"{path}/embed/", "", ""))
    return [page_url, embed_url]


def _fetch_html(url: str) -> str:
    request = Request(url, headers=REQUEST_HEADERS)
    with urlopen(request, timeout=30) as response:
        payload = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _parse_meta_tags(html_text: str) -> dict[str, str]:
    parser = MetaTagParser()
    parser.feed(html_text)
    return parser.meta


def _extract_json_image_url(html_text: str) -> str | None:
    for pattern in (
        r'"display_url"\s*:\s*"([^"]+)"',
        r'"display_src"\s*:\s*"([^"]+)"',
    ):
        match = re.search(pattern, html_text)
        if match:
            return _decode_json_string(match.group(1))
    return None


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return html.unescape(value.replace("\\/", "/"))


def _download_image_file(
    *,
    image_url: str,
    output_folder: str,
    filename_prefix: str,
    referer: str,
) -> tuple[str, str]:
    request = Request(image_url, headers={**IMAGE_REQUEST_HEADERS, "Referer": referer})
    with urlopen(request, timeout=60) as response:
        content_type = response.headers.get_content_type().lower()
        extension = _image_extension(image_url, content_type)
        handle, filepath = tempfile.mkstemp(prefix=f"{filename_prefix}_", suffix=f".{extension}", dir=output_folder)
        with os.fdopen(handle, "wb") as output_file:
            shutil.copyfileobj(response, output_file)
    return filepath, content_type


def _image_extension(image_url: str, content_type: str) -> str:
    path_ext = os.path.splitext(urlsplit(image_url).path)[1].lower().lstrip(".")
    if path_ext in IMAGE_EXTENSIONS:
        return path_ext
    return CONTENT_TYPE_EXTENSIONS.get(content_type, "jpg")


def _instagram_shortcode(media_url: str) -> str | None:
    match = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", media_url)
    if not match:
        return None
    return re.sub(r"[^\w-]", "", match.group(1)) or None
