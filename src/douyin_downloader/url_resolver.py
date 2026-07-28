import re
from urllib.parse import urljoin, urlsplit

import httpx

from douyin_downloader.domain import AppError, ResolvedShare, TransientUpstreamError

ENTRY_HOSTS = frozenset(
    {
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
    }
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
VIDEO_PATTERN = re.compile(r"/(?:share/)?video/(\d+)")
TRAILING_PUNCTUATION = ".,;:!?)]}，。！；：、】【】》〉」』”"


def _unsupported_url() -> AppError:
    return AppError("UNSUPPORTED_URL", "目前只支持抖音公开视频。", 400)


def _validated_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ENTRY_HOSTS:
        raise _unsupported_url()
    if parsed.username or parsed.password:
        raise _unsupported_url()
    try:
        port = parsed.port
    except ValueError as error:
        raise _unsupported_url() from error
    default_port = 80 if parsed.scheme == "http" else 443
    if port not in {None, default_port}:
        raise _unsupported_url()
    return url


def extract_share_url(text: str) -> str:
    if not 1 <= len(text) <= 2000:
        raise AppError("INVALID_INPUT", "没有识别到抖音链接，请粘贴完整分享文案。", 400)
    match = URL_PATTERN.search(text)
    if match is None:
        raise AppError("INVALID_INPUT", "没有识别到抖音链接，请粘贴完整分享文案。", 400)
    return _validated_url(match.group(0).rstrip(TRAILING_PUNCTUATION))


class ShareResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, share_text: str) -> ResolvedShare:
        source = extract_share_url(share_text)
        current = source
        for _ in range(5):
            match = VIDEO_PATTERN.search(urlsplit(current).path)
            if match:
                return ResolvedShare(source, current, match.group(1))
            try:
                response = await self._client.get(
                    current,
                    follow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"},
                )
            except httpx.HTTPError as error:
                raise TransientUpstreamError(type(error).__name__) from error
            if response.status_code >= 500:
                raise TransientUpstreamError(f"HTTP {response.status_code}")
            if response.status_code == 429:
                raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用，请稍后重试。", 502)
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if location is None:
                break
            current = _validated_url(urljoin(current, location))
        match = VIDEO_PATTERN.search(urlsplit(current).path)
        if match:
            return ResolvedShare(source, current, match.group(1))
        raise AppError("VIDEO_NOT_FOUND", "没有找到公开视频，请检查作品是否存在。", 404)
