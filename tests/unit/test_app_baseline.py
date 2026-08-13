import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.archive import ManagedArchive
from douyin_downloader.database_recovery import DatabaseRecoveryStatus
from douyin_downloader.domain import AppError
from douyin_downloader.f2_adapter import F2VideoParser
from douyin_downloader.launcher import main
from douyin_downloader.settings import SettingsModule
from douyin_downloader.url_resolver import ShareResolver
from douyin_downloader.web import app as app_module
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
        assert isinstance(app.state.services.managed_archive, ManagedArchive)
        assert isinstance(app.state.services.settings, SettingsModule)

    assert shared_client.is_closed


@pytest.mark.asyncio
async def test_settings_database_failure_does_not_remove_quick_download_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "archive.db").write_bytes(b"not a sqlite database")

    class TemporaryRuntimeStore:
        app_dir = tmp_path

    monkeypatch.setattr(app_module, "RuntimeStore", TemporaryRuntimeStore)
    app = create_app(testing=True)

    async with app.router.lifespan_context(app):
        assert app.state.services.settings is None
        assert app.state.services.managed_archive is None
        assert app.state.services.parse_service is not None
        assert app.state.services.database_recovery.status().state == "recovery_required"
    assert not (tmp_path / "archive.db").exists()
    assert len(tuple(tmp_path.glob("archive.corrupt-*.db"))) == 1  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_database_backup_failure_does_not_remove_quick_download_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TemporaryRuntimeStore:
        app_dir = tmp_path

    class FailingRecovery:
        def __init__(self, database_path: Path) -> None:
            self.database_path = database_path

        def prepare_startup(self) -> DatabaseRecoveryStatus:
            raise AppError("DATABASE_BACKUP_FAILED", "数据库备份校验失败。", 500)

    monkeypatch.setattr(app_module, "RuntimeStore", TemporaryRuntimeStore)
    monkeypatch.setattr(app_module, "DatabaseRecovery", FailingRecovery)
    app = create_app(testing=True)

    async with app.router.lifespan_context(app):
        assert app.state.services.settings is None
        assert app.state.services.managed_archive is None
        assert app.state.services.parse_service is not None


@pytest.mark.asyncio
async def test_production_composition_backs_up_untouched_database_before_issue5_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "archive.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE archive_operations (
                operation_id TEXT PRIMARY KEY,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL,
                root_path TEXT NOT NULL
            )
            """
        )

    class TemporaryRuntimeStore:
        app_dir = tmp_path

    monkeypatch.setattr(app_module, "RuntimeStore", TemporaryRuntimeStore)
    app = create_app(testing=True)

    async with app.router.lifespan_context(app):
        assert app.state.services.settings is not None
        assert app.state.services.managed_archive is not None

    backup = tmp_path / "archive.pre-settings-snapshot.bak"
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        legacy_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(archive_operations)")
        }
    assert "current_settings" not in tables
    assert "naming_template" not in legacy_columns


@pytest.mark.asyncio
async def test_production_app_rejects_test_only_host() -> None:
    app = create_app(expected_port=43123)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1", "http://127.0.0.1:43124"],
)
async def test_production_app_rejects_missing_or_wrong_loopback_port(base_url: str) -> None:
    app = create_app(expected_port=43123)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=base_url,
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
async def test_production_app_accepts_expected_loopback_authority(host: str) -> None:
    app = create_app(expected_port=43123)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=f"http://{host}:43123",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
