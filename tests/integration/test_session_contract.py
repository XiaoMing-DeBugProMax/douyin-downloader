import secrets

import pytest
from httpx import ASGITransport, AsyncClient

import douyin_downloader.web.routes as route_module
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


@pytest.mark.asyncio
async def test_internal_launch_token_rejects_missing_or_wrong_management_token() -> None:
    management_token = secrets.token_urlsafe(32)
    sessions = SessionManager(management_token=management_token)
    app = create_app(session_manager=sessions, testing=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        missing = await client.post("/api/internal/launch-token")
        wrong = await client.post(
            "/api/internal/launch-token",
            headers={"x-management-token": secrets.token_urlsafe(32)},
        )

    assert missing.status_code == 403
    assert wrong.status_code == 403


@pytest.mark.asyncio
async def test_internal_launch_token_uses_constant_time_check_and_redirects_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    management_token = secrets.token_urlsafe(32)
    sessions = SessionManager(management_token=management_token)
    app = create_app(session_manager=sessions, testing=True)
    comparisons: list[tuple[str, str]] = []
    real_compare_digest = route_module.secrets.compare_digest

    def recording_compare_digest(supplied: str, expected: str) -> bool:
        comparisons.append((supplied, expected))
        return real_compare_digest(supplied, expected)

    monkeypatch.setattr(route_module.secrets, "compare_digest", recording_compare_digest)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        issued = await client.post(
            "/api/internal/launch-token",
            headers={
                "x-management-token": management_token,
                "origin": "https://untrusted.example",
            },
        )
        launch_token = issued.json()["launch_token"]
        management_comparisons = comparisons.copy()
        launched = await client.get("/", params={"launch_token": launch_token})

    assert issued.status_code == 200
    assert management_comparisons == [(management_token, management_token)]
    assert management_token not in issued.text
    assert management_token not in str(issued.request.url)
    assert "access-control-allow-origin" not in issued.headers
    assert launched.status_code == 303
    assert launched.headers["location"] == "/"
    assert launch_token not in launched.headers["location"]
