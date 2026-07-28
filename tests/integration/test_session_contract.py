import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.session import COOKIE_NAME, SessionManager
from douyin_downloader.web.app import create_app


@pytest.mark.asyncio
async def test_launch_sets_http_only_cookie_and_redirects_clean_url() -> None:
    sessions = SessionManager()
    token = sessions.issue_launch_token()
    app = create_app(session_manager=sessions, testing=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get("/", params={"launch_token": token})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert token not in response.headers["location"]


@pytest.mark.asyncio
async def test_invalid_launch_token_cannot_establish_a_session() -> None:
    app = create_app(session_manager=SessionManager(), testing=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get("/", params={"launch_token": "invalid"})

    assert response.status_code == 403
    assert response.json()["detail"] == "INVALID_LAUNCH_TOKEN"
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")
