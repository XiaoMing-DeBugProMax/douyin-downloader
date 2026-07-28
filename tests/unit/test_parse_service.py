import pytest

from douyin_downloader.domain import ParsedVideo, ResolvedShare, TransientUpstreamError
from douyin_downloader.parse_service import ParseService
from douyin_downloader.store import ParseStore


class FlakyResolver:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, share_text: str) -> ResolvedShare:
        self.calls += 1
        if self.calls == 1:
            raise TransientUpstreamError("temporary")
        return ResolvedShare(share_text, "https://www.douyin.com/video/1", "1")


class Parser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        return ParsedVideo(aweme_id, "author", "description", 1, (), ())


@pytest.mark.asyncio
async def test_parse_retries_one_transient_upstream_failure() -> None:
    resolver = FlakyResolver()
    service = ParseService(resolver, Parser(), ParseStore())

    result = await service.parse("https://v.douyin.com/example/")

    assert result.video.aweme_id == "1"
    assert resolver.calls == 2
