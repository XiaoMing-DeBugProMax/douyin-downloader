import pytest

import douyin_downloader.parse_service as parse_service_module
from douyin_downloader.domain import AppError, ParsedVideo, ResolvedShare, TransientUpstreamError
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
async def test_parse_retries_one_transient_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []

    class FakeLogger:
        def info(self, _: str, *, extra: dict[str, object]) -> None:
            logged.append(extra)

    monkeypatch.setattr(parse_service_module, "_LOGGER", FakeLogger())
    resolver = FlakyResolver()
    service = ParseService(resolver, Parser(), ParseStore())

    result = await service.parse("https://v.douyin.com/example/")

    assert result.video.aweme_id == "1"
    assert resolver.calls == 2
    assert logged == [
        {
            "operation": "parse",
            "error_code": "-",
            "elapsed_ms": pytest.approx(250, abs=100),
            "bytes_streamed": 0,
        }
    ]


@pytest.mark.asyncio
async def test_parse_logs_anonymous_error_code_without_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []

    class AlwaysFailResolver:
        async def resolve(self, _: str) -> ResolvedShare:
            raise TransientUpstreamError("contains sensitive upstream detail")

    class FakeLogger:
        def info(self, _: str, *, extra: dict[str, object]) -> None:
            logged.append(extra)

    monkeypatch.setattr(parse_service_module, "_LOGGER", FakeLogger())
    service = ParseService(AlwaysFailResolver(), Parser(), ParseStore())

    with pytest.raises(AppError, match="解析服务"):
        await service.parse("sensitive share text")

    assert len(logged) == 1
    assert logged[0]["operation"] == "parse"
    assert logged[0]["error_code"] == "UPSTREAM_BLOCKED"
    assert logged[0]["bytes_streamed"] == 0
    assert "sensitive" not in repr(logged)
