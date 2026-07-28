import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.web.app import create_app


@pytest.mark.asyncio
async def test_health_contract() -> None:
    app = create_app()
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
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "鎶栭煶瑙嗛涓嬭浇" in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text
