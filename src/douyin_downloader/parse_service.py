import asyncio
from typing import Protocol

from douyin_downloader.domain import (
    AppError,
    ParsedVideo,
    ParseResult,
    ResolvedShare,
    TransientUpstreamError,
)
from douyin_downloader.store import ParseStore


class Resolver(Protocol):
    async def resolve(self, share_text: str) -> ResolvedShare: ...


class VideoParser(Protocol):
    async def parse(self, aweme_id: str) -> ParsedVideo: ...


class ParseService:
    def __init__(self, resolver: Resolver, parser: VideoParser, store: ParseStore) -> None:
        self._resolver = resolver
        self._parser = parser
        self.store = store

    async def parse(self, share_text: str) -> ParseResult:
        for attempt in range(2):
            try:
                async with asyncio.timeout(20):
                    resolved = await self._resolver.resolve(share_text)
                    video = await self._parser.parse(resolved.aweme_id)
                return ParseResult(self.store.put(video), video)
            except (TimeoutError, TransientUpstreamError) as error:
                if attempt == 1:
                    if isinstance(error, TimeoutError):
                        code = "UPSTREAM_TIMEOUT"
                    else:
                        code = "UPSTREAM_BLOCKED"
                    status = 504 if code == "UPSTREAM_TIMEOUT" else 502
                    raise AppError(code, "解析服务暂时不可用，请稍后重试。", status) from error
                await asyncio.sleep(0.25)
        raise AssertionError("retry loop must return or raise")
