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
HTTP_URL_MARKER = re.compile(r"https?://", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SCHEME_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
VIDEO_PATTERN = re.compile(r"/(?:share/)?video/(\d+)")
TRAILING_PUNCTUATION = ".,;:!?)]}，。！；：、】【】》〉」』”"

_MISSING_LINK_MESSAGE = "没有识别到抖音链接，请粘贴完整分享文案。"
_MALFORMED_URL_MESSAGE = "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"
_UNSUPPORTED_URL_MESSAGE = "目前只支持抖音公开视频。"
_HTTPS_ONLY_MESSAGE = "仅支持 HTTPS 抖音公开视频链接。"


def _invalid_input(message: str) -> AppError:
    return AppError("INVALID_INPUT", message, 400)


def _unsupported_url(message: str = _UNSUPPORTED_URL_MESSAGE) -> AppError:
    return AppError("UNSUPPORTED_URL", message, 400)


def _has_unsafe_url_characters(url: str) -> bool:
    return any(
        ord(character) <= 0x1F
        or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in url
    )


def _url_parts(url: str) -> tuple[str, str, int | None, bool]:
    if _has_unsafe_url_characters(url):
        raise _invalid_input(_MALFORMED_URL_MESSAGE)
    try:
        httpx.URL(url)
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
        has_credentials = parsed.username is not None or parsed.password is not None
    except (httpx.InvalidURL, UnicodeError, ValueError) as error:
        raise _invalid_input(_MALFORMED_URL_MESSAGE) from error
    if not parsed.netloc or hostname is None:
        raise _invalid_input(_MALFORMED_URL_MESSAGE)
    return parsed.scheme.lower(), hostname.lower(), port, has_credentials


def _validated_url(url: str) -> str:
    scheme, hostname, port, has_credentials = _url_parts(url)
    if hostname not in ENTRY_HOSTS:
        raise _unsupported_url()
    if scheme != "https":
        if scheme == "http":
            raise _unsupported_url(_HTTPS_ONLY_MESSAGE)
        raise _unsupported_url()
    if has_credentials or port not in {None, 443}:
        raise _unsupported_url()
    return url


def _redirect_url(current: str, location: str) -> str:
    if SCHEME_PREFIX.match(location) or location.startswith("//"):
        return _validated_url(location)
    try:
        return _validated_url(urljoin(current, location))
    except (UnicodeError, ValueError) as error:
        raise _invalid_input(_MALFORMED_URL_MESSAGE) from error


def extract_share_url(text: str) -> str:
    if not 1 <= len(text) <= 2000:
        raise _invalid_input(_MISSING_LINK_MESSAGE)
    match = URL_PATTERN.search(text)
    if match is None:
        message = (
            _MALFORMED_URL_MESSAGE
            if HTTP_URL_MARKER.search(text)
            else _MISSING_LINK_MESSAGE
        )
        raise _invalid_input(message)
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
            except httpx.InvalidURL as error:
                raise _invalid_input(_MALFORMED_URL_MESSAGE) from error
            except httpx.RemoteProtocolError as error:
                if (
                    str(error).startswith("Invalid URL in location header:")
                    and isinstance(error.__context__, httpx.InvalidURL)
                ):
                    raise _invalid_input(_MALFORMED_URL_MESSAGE) from error
                raise TransientUpstreamError(type(error).__name__) from error
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
            current = _redirect_url(current, location)
        match = VIDEO_PATTERN.search(urlsplit(current).path)
        if match:
            return ResolvedShare(source, current, match.group(1))
        raise AppError("VIDEO_NOT_FOUND", "没有找到公开视频，请检查作品是否存在。", 404)
