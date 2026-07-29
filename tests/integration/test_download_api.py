from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import douyin_downloader.media as media_module
from douyin_downloader.domain import AppError, ParsedVideo, ResolvedShare
from douyin_downloader.logging_config import configure_logging
from douyin_downloader.media import open_upstream
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices

VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="钟哥",
    description='a/b:c*?"<d>|. ',
    duration_ms=15279,
    cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
    media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
)


class FakeResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        return ResolvedShare(share_text, "https://www.douyin.com/video/1", VIDEO.aweme_id)


class FakeParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        return VIDEO


class StaticParser:
    def __init__(self, video: ParsedVideo) -> None:
        self._video = video

    async def parse(self, aweme_id: str) -> ParsedVideo:
        return self._video


def make_app(
    media_client: httpx.AsyncClient,
    *,
    store: ParseStore | None = None,
) -> tuple[object, SessionManager]:
    sessions = SessionManager()
    app = create_app(
        services=AppServices(
            parse_service=ParseService(FakeResolver(), FakeParser(), store or ParseStore()),
            media_client=media_client,
        ),
        session_manager=sessions,
        testing=True,
    )
    return app, sessions


def media_transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "v95-web-sz.douyinvod.com":
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4-data")
    if request.url.host == "p3.douyinpic.com":
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpeg-data")
    return httpx.Response(404, headers={"content-type": "text/plain"})


@pytest.mark.asyncio
async def test_download_streams_attachment_with_safe_filename() -> None:
    async with AsyncClient(transport=httpx.MockTransport(media_transport)) as media_client:
        app, sessions = make_app(media_client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            parse_response = await client.post(
                "/api/parse",
                headers={"origin": "http://testserver"},
                json={"share_text": "https://v.douyin.com/example/"},
            )
            token = parse_response.json()["parse_token"]
            response = await client.get(f"/api/download/{token}")

    assert response.status_code == 200
    assert response.content == b"mp4-data"
    assert response.headers["content-type"].startswith("video/mp4")
    assert "attachment" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "a/b" not in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_cover_is_protected_and_streams_trusted_image() -> None:
    async with AsyncClient(transport=httpx.MockTransport(media_transport)) as media_client:
        app, sessions = make_app(media_client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            unauthenticated = await client.get("/api/cover/missing")
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            parse_response = await client.post(
                "/api/parse",
                headers={"origin": "http://testserver"},
                json={"share_text": "https://v.douyin.com/example/"},
            )
            token = parse_response.json()["parse_token"]
            response = await client.get(f"/api/cover/{token}")

    assert unauthenticated.status_code == 403
    assert response.status_code == 200
    assert response.content == b"jpeg-data"
    assert response.headers["cache-control"] == "private, max-age=300"


@pytest.mark.asyncio
async def test_download_requires_session_and_expired_token_returns_410() -> None:
    now = [0.0]
    store = ParseStore(ttl_seconds=1, clock=lambda: now[0])
    async with AsyncClient(transport=httpx.MockTransport(media_transport)) as media_client:
        app, sessions = make_app(media_client, store=store)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            unauthenticated = await client.get("/api/download/missing")
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            parse_response = await client.post(
                "/api/parse",
                headers={"origin": "http://testserver"},
                json={"share_text": "https://v.douyin.com/example/"},
            )
            token = parse_response.json()["parse_token"]
            now[0] = 2.0
            expired = await client.get(f"/api/download/{token}")

    assert unauthenticated.status_code == 403
    assert expired.status_code == 410


@pytest.mark.asyncio
async def test_download_tries_second_candidate_after_first_fails() -> None:
    video = replace(
        VIDEO,
        media_urls=(
            "https://v95-web-sz.douyinvod.com/expired.mp4",
            "https://v95-web-sz.douyinvod.com/working.mp4",
        ),
    )

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/expired.mp4":
            return httpx.Response(403, headers={"content-type": "text/plain"})
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"second")

    async with AsyncClient(transport=httpx.MockTransport(transport)) as media_client:
        sessions = SessionManager()
        app = create_app(
            services=AppServices(
                parse_service=ParseService(FakeResolver(), StaticParser(video), ParseStore()),
                media_client=media_client,
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            token = (
                await client.post(
                    "/api/parse",
                    headers={"origin": "http://testserver"},
                    json={"share_text": "https://v.douyin.com/example/"},
                )
            ).json()["parse_token"]
            response = await client.get(f"/api/download/{token}")

    assert response.status_code == 200
    assert response.content == b"second"


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"first"
        yield b"second"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stopped_stream_closes_upstream_response() -> None:
    stream = TrackingStream()

    def transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, stream=stream)

    async with AsyncClient(transport=httpx.MockTransport(transport)) as client:
        upstream = await open_upstream(
            client, "https://v95-web-sz.douyinvod.com/video.mp4", "video"
        )
        iterator = upstream.iter_bytes()
        assert await anext(iterator) == b"firstsecond"
        await iterator.aclose()

    assert stream.closed is True


@pytest.mark.asyncio
async def test_completed_stream_logs_only_operation_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []

    class FakeLogger:
        def info(self, _: str, *, extra: dict[str, object]) -> None:
            logged.append(extra)

    monkeypatch.setattr(media_module, "_LOGGER", FakeLogger())
    stream = TrackingStream()

    def transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, stream=stream)

    async with AsyncClient(transport=httpx.MockTransport(transport)) as client:
        upstream = await open_upstream(
            client, "https://v95-web-sz.douyinvod.com/video.mp4", "video"
        )
        assert b"".join([chunk async for chunk in upstream.iter_bytes()]) == b"firstsecond"

    assert logged == [
        {
            "operation": "download",
            "error_code": "-",
            "elapsed_ms": pytest.approx(0, abs=1000),
            "bytes_streamed": 11,
        }
    ]


@pytest.mark.asyncio
async def test_real_routes_write_safe_parse_cover_and_download_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    logger = configure_logging()

    async with AsyncClient(transport=httpx.MockTransport(media_transport)) as media_client:
        app, sessions = make_app(media_client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            parse_response = await client.post(
                "/api/parse",
                headers={"origin": "http://testserver"},
                json={"share_text": "sensitive share text"},
            )
            token = parse_response.json()["parse_token"]
            assert (await client.get(f"/api/cover/{token}")).content == b"jpeg-data"
            assert (await client.get(f"/api/download/{token}")).content == b"mp4-data"

    for handler in logger.handlers:
        handler.flush()
    log_path = tmp_path / "LocalAppData" / "DouyinLocalDownloader" / "logs" / "app.log"
    text = log_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert len(lines) == 3
    assert "operation=parse error_code=-" in lines[0]
    assert "bytes_streamed=0" in lines[0]
    assert "operation=cover error_code=-" in lines[1]
    assert "bytes_streamed=9" in lines[1]
    assert "operation=download error_code=-" in lines[2]
    assert "bytes_streamed=8" in lines[2]
    assert "sensitive share text" not in text
    assert token not in text
    assert "douyinvod.com" not in text


@pytest.mark.asyncio
async def test_failed_media_open_logs_anonymous_error_with_zero_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []

    class FakeLogger:
        def info(self, _: str, *, extra: dict[str, object]) -> None:
            logged.append(extra)

    monkeypatch.setattr(media_module, "_LOGGER", FakeLogger())

    def transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"content-type": "text/plain"})

    async with AsyncClient(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(AppError, match="下载失败"):
            await open_upstream(
                client,
                "https://p3.douyinpic.com/private-cover.jpeg?secret=must-not-log",
                "cover",
            )

    assert len(logged) == 1
    assert logged[0]["operation"] == "cover"
    assert logged[0]["error_code"] == "DOWNLOAD_FAILED"
    assert logged[0]["bytes_streamed"] == 0
    assert "secret" not in repr(logged)
