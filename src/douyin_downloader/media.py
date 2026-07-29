import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

from douyin_downloader.domain import AppError, ParsedVideo
from douyin_downloader.logging_config import log_operation

_LOGGER = logging.getLogger("douyin_downloader")
VIDEO_SUFFIXES = (".douyinvod.com", ".bytevcloud.com", ".bytecdn.cn")
COVER_SUFFIXES = (".douyinpic.com", ".byteimg.com", ".byteimg.cn")
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _download_error() -> AppError:
    return AppError("DOWNLOAD_FAILED", "下载失败，请重新解析后重试。", 502)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    hostname = host.lower()
    return any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in suffixes)


def validate_media_url(url: str, kind: Literal["cover", "video"]) -> str:
    parsed = urlsplit(url)
    suffixes = VIDEO_SUFFIXES if kind == "video" else COVER_SUFFIXES
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or not _host_matches(parsed.hostname, suffixes)
    ):
        raise _download_error()
    try:
        port = parsed.port
    except ValueError as error:
        raise _download_error() from error
    if parsed.username or parsed.password or port not in {None, 443}:
        raise _download_error()
    return url


def safe_video_filename(video: ParsedVideo) -> str:
    base = f"{video.author} - {video.description or video.aweme_id}"
    base = INVALID_WINDOWS.sub("", base).strip(" .")[:120].rstrip(" .")
    return f"{base or video.aweme_id}.mp4"


@dataclass(slots=True)
class UpstreamStream:
    response: httpx.Response
    operation: Literal["cover", "download"]
    started_at: float
    chunk_size: int = 256 * 1024

    @property
    def content_type(self) -> str:
        return self.response.headers["content-type"].split(";", maxsplit=1)[0]

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        bytes_streamed = 0
        error_code = "-"
        try:
            async for chunk in self.response.aiter_bytes(self.chunk_size):
                if chunk:
                    bytes_streamed += len(chunk)
                    yield chunk
        except BaseException:
            error_code = "STREAM_INTERRUPTED"
            raise
        finally:
            try:
                await self.response.aclose()
            finally:
                log_operation(
                    _LOGGER,
                    operation=self.operation,
                    error_code=error_code,
                    elapsed_ms=int((time.monotonic() - self.started_at) * 1000),
                    bytes_streamed=bytes_streamed,
                )


async def open_upstream(
    client: httpx.AsyncClient,
    url: str,
    kind: Literal["cover", "video"],
) -> UpstreamStream:
    started_at = time.monotonic()
    operation: Literal["cover", "download"] = "download" if kind == "video" else "cover"
    try:
        request = client.build_request(
            "GET",
            validate_media_url(url, kind),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"},
        )
        response = await client.send(request, stream=True, follow_redirects=False)
    except AppError:
        log_operation(
            _LOGGER,
            operation=operation,
            error_code="DOWNLOAD_FAILED",
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            bytes_streamed=0,
        )
        raise
    except httpx.HTTPError as error:
        log_operation(
            _LOGGER,
            operation=operation,
            error_code="DOWNLOAD_FAILED",
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            bytes_streamed=0,
        )
        raise _download_error() from error
    expected_content_type = "video/mp4" if kind == "video" else "image/"
    content_type = response.headers.get("content-type", "").lower()
    if not response.is_success or not content_type.startswith(expected_content_type):
        await response.aclose()
        log_operation(
            _LOGGER,
            operation=operation,
            error_code="DOWNLOAD_FAILED",
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            bytes_streamed=0,
        )
        raise _download_error()
    return UpstreamStream(response, operation, started_at)


async def open_first_available(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    kind: Literal["cover", "video"],
) -> UpstreamStream:
    last_error: AppError | None = None
    for url in urls:
        try:
            return await open_upstream(client, url, kind)
        except AppError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise _download_error()
