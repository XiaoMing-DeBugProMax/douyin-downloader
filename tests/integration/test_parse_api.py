import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.domain import ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices


class FakeResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        return ResolvedShare(
            share_text,
            "https://www.douyin.com/video/7429378937383308594",
            "7429378937383308594",
        )


class FakeParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        return ParsedVideo(
            aweme_id=aweme_id,
            author="钟哥!!",
            description="#王者荣耀 #王者荣耀热门",
            duration_ms=15279,
            cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
            media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
        )


@pytest.fixture
async def app_and_sessions() -> tuple[object, SessionManager]:
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(FakeResolver(), FakeParser(), ParseStore()),
                media_client=media_client,
            ),
            session_manager=sessions,
            testing=True,
        )
        yield app, sessions


@pytest.mark.asyncio
async def test_parse_returns_public_projection_not_media_urls(
    app_and_sessions: tuple[object, SessionManager],
) -> None:
    app, sessions = app_and_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        client.cookies.set("douyin_local_session", sessions.cookie_token)
        response = await client.post(
            "/api/parse",
            headers={"origin": "http://testserver"},
            json={"share_text": "https://v.douyin.com/example/"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["video"]["author"] == "钟哥!!"
    assert payload["video"]["cover_url"].startswith("/api/cover/")
    assert payload["video"]["suggested_filename"].endswith(".mp4")
    assert not any(char in payload["video"]["suggested_filename"] for char in '<>:"/\\|?*')
    assert "media_urls" not in response.text
    assert "douyinvod.com" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("share_text", ["", "x" * 2001])
async def test_parse_validation_returns_approved_400_contract(
    share_text: str,
    app_and_sessions: tuple[object, SessionManager],
) -> None:
    app, sessions = app_and_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        client.cookies.set("douyin_local_session", sessions.cookie_token)
        response = await client.post(
            "/api/parse",
            headers={"origin": "http://testserver"},
            json={"share_text": share_text},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_parse_requires_local_session_and_same_origin(
    app_and_sessions: tuple[object, SessionManager],
) -> None:
    app, sessions = app_and_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        no_cookie = await client.post(
            "/api/parse",
            headers={"origin": "http://testserver"},
            json={"share_text": "https://v.douyin.com/example/"},
        )
        client.cookies.set("douyin_local_session", sessions.cookie_token)
        cross_origin = await client.post(
            "/api/parse",
            headers={"origin": "http://attacker.test"},
            json={"share_text": "https://v.douyin.com/example/"},
        )

    assert no_cookie.status_code == 403
    assert cross_origin.status_code == 403


@pytest.mark.asyncio
async def test_default_app_returns_controlled_error_without_parser_adapter() -> None:
    sessions = SessionManager()
    app = create_app(session_manager=sessions, expected_port=43123)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1:43123",
    ) as client:
        client.cookies.set("douyin_local_session", sessions.cookie_token)
        response = await client.post(
            "/api/parse",
            headers={"origin": "http://127.0.0.1:43123"},
            json={"share_text": "https://v.douyin.com/example/"},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_BLOCKED"
