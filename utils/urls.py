from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

TWITTER_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com", "x.com", "www.x.com", "mobile.x.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

URL_REGEX = re.compile(r"https?://[^\s<>()\[\]{}|\"`]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,!?;:)]}"

_TIKTOK_PATHS = (
    re.compile(r"^/@[\w\.]+/video/\d+/?$", re.IGNORECASE),
    re.compile(r"^/t/[A-Za-z0-9]+/?$"),
    re.compile(r"^/[A-Za-z0-9]{8,12}/?$"),
)

_INSTAGRAM_PATHS = (
    re.compile(r"^/(?:p|reel|reels|tv)/[\w\-]+/?$", re.IGNORECASE),
    re.compile(r"^/stories/[\w\.]+/\d+/?$", re.IGNORECASE),
)

_YOUTUBE_PATHS = (
    re.compile(r"^/watch$", re.IGNORECASE),
    re.compile(r"^/(?:shorts|live|embed|v)/[\w\-]+/?$", re.IGNORECASE),
    re.compile(r"^/[\w\-]+/?$", re.IGNORECASE),
)


@dataclass(frozen=True)
class RewriteResult:
    rewritten_urls: list[str]
    spoiler_urls: list[str]


def sanitize_url(url: str) -> str:
    return re.sub(r"[^\w\./:\-\?\&\=\%\@#]", "", url)


def _normalize_host(hostname: str | None) -> str:
    return (hostname or "").lower().strip(".")


def _strip_trailing_punctuation(url: str) -> str:
    return url.rstrip(TRAILING_PUNCTUATION)


def _is_spoiler(content: str, start: int, end: int) -> bool:
    has_prefix = start >= 2 and content[start - 2:start] == "||"
    has_suffix_outside = end + 2 <= len(content) and content[end:end + 2] == "||"
    has_suffix_inside = end >= 2 and content[end - 2:end] == "||"
    return has_prefix and (has_suffix_outside or has_suffix_inside)


def rewrite_twitter_urls(content: str) -> RewriteResult:
    rewritten: list[str] = []
    spoiler: list[str] = []
    for match in URL_REGEX.finditer(content):
        raw_url = _strip_trailing_punctuation(match.group(0))
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            continue
        host = _normalize_host(parsed.hostname)
        if host in {"vxtwitter.com", "www.vxtwitter.com"}:
            continue
        if host not in TWITTER_HOSTS:
            continue

        clean = sanitize_url(raw_url)
        p = urlsplit(clean)
        replaced = urlunsplit((p.scheme, "vxtwitter.com", p.path, p.query, ""))
        if _is_spoiler(content, match.start(), match.end()):
            spoiler.append(replaced)
        else:
            rewritten.append(replaced)
    return RewriteResult(rewritten_urls=rewritten, spoiler_urls=spoiler)


@dataclass(frozen=True)
class SupportedLink:
    platform: str
    url: str
    spoiler: bool = False


def parse_supported_url(url: str, spoiler: bool = False) -> SupportedLink:
    """Strict validation for downloads; metadata/creator URL helpers are separate."""
    parsed = urlsplit(_strip_trailing_punctuation(url))
    host = _normalize_host(parsed.hostname)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
        raise ValueError("Unsupported source URL")
    path = parsed.path.rstrip("/")
    if host in TWITTER_HOSTS or host in {"vxtwitter.com", "www.vxtwitter.com"}:
        match = re.fullmatch(r"/([\w]+)/status/(\d+)(?:/(?:photo|video)/\d+)?", path)
        if not match:
            raise ValueError("Expected a Twitter/X post")
        platform, url = "twitter", f"https://x.com/{match[1]}/status/{match[2]}"
    elif host in TIKTOK_HOSTS and any(p.fullmatch(parsed.path) for p in _TIKTOK_PATHS):
        platform, url = "tiktok", urlunsplit(("https", host, path, "", ""))
    elif host in INSTAGRAM_HOSTS and any(p.fullmatch(parsed.path) for p in _INSTAGRAM_PATHS):
        platform, url = "instagram", f"https://www.instagram.com{path}/"
    elif host in YOUTUBE_HOSTS:
        match = re.fullmatch(r"/shorts/([A-Za-z0-9_-]+)", path)
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or not match:
            raise ValueError("Only explicit YouTube Shorts links are supported")
        platform, url = "youtube", f"https://www.youtube.com/shorts/{match[1]}"
    else:
        raise ValueError("Unsupported source URL")
    return SupportedLink(platform, url, spoiler)


def extract_supported_links(content: str) -> list[SupportedLink]:
    links: dict[tuple[str, str], SupportedLink] = {}
    for match in URL_REGEX.finditer(content):
        try:
            link = parse_supported_url(match[0], _is_spoiler(content, match.start(), match.end()))
        except ValueError:
            continue
        key = (link.platform, link.url)
        previous = links.get(key)
        # If any occurrence is hidden, keep the replacement hidden too.
        links[key] = SupportedLink(link.platform, link.url, link.spoiler or bool(previous and previous.spoiler))
    return list(links.values())


def contains_unhandled_youtube_link(content: str) -> bool:
    """Protect the whole source: Discord suppression affects every embed in it."""
    for match in URL_REGEX.finditer(content):
        try:
            host = _normalize_host(urlsplit(_strip_trailing_punctuation(match[0])).hostname)
        except ValueError:
            continue
        youtube_host = any(host == root or host.endswith("." + root)
                           for root in ("youtube.com", "youtu.be", "youtube-nocookie.com"))
        if youtube_host:
            try:
                if parse_supported_url(match[0]).platform == "youtube":
                    continue
            except ValueError:
                pass
            return True
    return False


def validate_tiktok_url(url: str) -> str:
    clean = _strip_trailing_punctuation(sanitize_url(url))
    parsed = urlsplit(clean)
    host = _normalize_host(parsed.hostname)
    if host not in TIKTOK_HOSTS:
        return clean
    if any(pattern.match(parsed.path or "") for pattern in _TIKTOK_PATHS):
        return clean
    return clean


def rewrite_tiktok_url(url: str) -> str:
    """Return a sanitized TikTok URL using tnktok.com as the embed host."""
    clean = validate_tiktok_url(url)
    parsed = urlsplit(clean)
    if _normalize_host(parsed.hostname) not in TIKTOK_HOSTS:
        return clean
    return urlunsplit((parsed.scheme, "tnktok.com", parsed.path, parsed.query, ""))


def validate_instagram_url(url: str) -> str:
    clean = _strip_trailing_punctuation(sanitize_url(url))
    parsed = urlsplit(clean)
    host = _normalize_host(parsed.hostname)
    if host not in INSTAGRAM_HOSTS:
        return clean
    if any(pattern.match(parsed.path or "") for pattern in _INSTAGRAM_PATHS):
        return clean
    return clean


def validate_youtube_url(url: str) -> str:
    clean = _strip_trailing_punctuation(sanitize_url(url))
    parsed = urlsplit(clean)
    host = _normalize_host(parsed.hostname)
    if host not in YOUTUBE_HOSTS:
        return clean
    if host.endswith("youtu.be") and any(pattern.match(parsed.path or "") for pattern in _YOUTUBE_PATHS):
        return clean
    if any(pattern.match(parsed.path or "") for pattern in _YOUTUBE_PATHS):
        return clean
    return clean


def is_tiktok_url(url: str) -> bool:
    parsed = urlsplit(url)
    return _normalize_host(parsed.hostname) in TIKTOK_HOSTS


def is_instagram_url(url: str) -> bool:
    parsed = urlsplit(url)
    return _normalize_host(parsed.hostname) in INSTAGRAM_HOSTS


def is_youtube_url(url: str) -> bool:
    parsed = urlsplit(url)
    return _normalize_host(parsed.hostname) in YOUTUBE_HOSTS
