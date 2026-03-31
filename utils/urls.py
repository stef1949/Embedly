from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

TWITTER_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com", "x.com", "www.x.com", "mobile.x.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "vm.tiktok.com"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"}

URL_REGEX = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


@dataclass(frozen=True)
class RewriteResult:
    rewritten_urls: list[str]
    spoiler_urls: list[str]


def sanitize_url(url: str) -> str:
    return re.sub(r"[^\w\.\/:\-\?\&\=\%\@#]", "", url)


def _normalize_host(hostname: str | None) -> str:
    return (hostname or "").lower()


def _is_spoiler(content: str, start: int, end: int) -> bool:
    has_prefix = start >= 2 and content[start - 2:start] == "||"
    has_suffix_outside = end + 2 <= len(content) and content[end:end + 2] == "||"
    has_suffix_inside = end >= 2 and content[end - 2:end] == "||"
    return has_prefix and (has_suffix_outside or has_suffix_inside)


def rewrite_twitter_urls(content: str) -> RewriteResult:
    rewritten: list[str] = []
    spoiler: list[str] = []
    for match in URL_REGEX.finditer(content):
        raw_url = match.group(0)
        parsed = urlsplit(raw_url)
        host = _normalize_host(parsed.hostname)
        if host == "vxtwitter.com" or host == "www.vxtwitter.com":
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


def validate_tiktok_url(url: str) -> str:
    clean = sanitize_url(url)
    parsed = urlsplit(clean)
    if _normalize_host(parsed.hostname) not in TIKTOK_HOSTS:
        return clean
    return clean


def validate_instagram_url(url: str) -> str:
    clean = sanitize_url(url)
    parsed = urlsplit(clean)
    if _normalize_host(parsed.hostname) not in INSTAGRAM_HOSTS:
        return clean
    return clean


def is_tiktok_url(url: str) -> bool:
    parsed = urlsplit(url)
    return _normalize_host(parsed.hostname) in TIKTOK_HOSTS


def is_instagram_url(url: str) -> bool:
    parsed = urlsplit(url)
    return _normalize_host(parsed.hostname) in INSTAGRAM_HOSTS
