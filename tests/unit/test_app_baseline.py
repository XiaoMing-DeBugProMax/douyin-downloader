import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.f2_adapter import F2VideoParser
from douyin_downloader.launcher import main
from douyin_downloader.url_resolver import ShareResolver
from douyin_downloader.web.app import create_app


@pytest.mark.asyncio
async def test_health_contract() -> None:
    app = create_app(testing=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["app"] == "douyin-local-downloader"
    assert response.json()["status"] == "ok"
    assert response.json()["instance_id"]


@pytest.mark.asyncio
async def test_home_serves_local_static_page() -> None:
    app = create_app(testing=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "抖音视频下载" in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text


def test_launcher_entry_point_is_callable() -> None:
    assert callable(main)


@pytest.mark.asyncio
async def test_lifespan_composes_services_with_one_shared_client() -> None:
    app = create_app(testing=True)

    async with app.router.lifespan_context(app):
        shared_client = app.state.http_client
        parse_service = app.state.services.parse_service

        assert not shared_client.is_closed
        assert isinstance(parse_service._resolver, ShareResolver)
        assert parse_service._resolver._client is shared_client
        assert isinstance(parse_service._parser, F2VideoParser)

    assert shared_client.is_closed


@pytest.mark.asyncio
async def test_production_app_rejects_test_only_host() -> None:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 400
