from datetime import date
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.database_recovery import (
    DatabaseBackup,
    DatabaseRecoveryStatus,
)
from douyin_downloader.domain import ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices


class UnusedResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        raise AssertionError(share_text)


class UnusedParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        raise AssertionError(aweme_id)


class RecordingRecovery:
    def __init__(self, tmp_path: Path) -> None:
        self.backup = DatabaseBackup(
            "archive.daily-2026-08-13.bak",
            tmp_path / "backups" / "archive.daily-2026-08-13.bak",
            date(2026, 8, 13),
        )
        self.restored: list[str] = []
        self.rebuilt: list[Path] = []

    def status(self) -> DatabaseRecoveryStatus:
        return DatabaseRecoveryStatus(
            "recovery_required",
            (self.backup,),
            self.backup.path.parent / "archive.corrupt.db",
        )

    def restore(self, backup_name: str) -> DatabaseRecoveryStatus:
        self.restored.append(backup_name)
        return DatabaseRecoveryStatus("healthy", (self.backup,))

    def rebuild_from_metadata(self, root: Path) -> DatabaseRecoveryStatus:
        self.rebuilt.append(root)
        return DatabaseRecoveryStatus(
            "healthy",
            (self.backup,),
            history_recovery="incomplete",
            rebuilt_archives=2,
        )


class StaticDirectoryChooser:
    def __init__(self, path: Path) -> None:
        self.path = path

    def choose_directory(self) -> Path:
        return self.path


@pytest.mark.asyncio
async def test_recovery_routes_remain_available_when_archive_services_are_down(
    tmp_path: Path,
) -> None:
    recovery = RecordingRecovery(tmp_path)
    root = tmp_path / "library"
    root.mkdir()
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(UnusedResolver(), UnusedParser(), ParseStore()),
                media_client=media_client,
                database_recovery=recovery,
                directory_chooser=StaticDirectoryChooser(root),
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            status = await client.get("/api/recovery")
            restored = await client.post(
                "/api/recovery/restore",
                headers={"origin": "http://testserver"},
                json={"backup_name": recovery.backup.name},
            )
            rebuilt = await client.post(
                "/api/recovery/rebuild",
                headers={"origin": "http://testserver"},
            )
            cross_origin = await client.post(
                "/api/recovery/rebuild",
                headers={"origin": "https://example.com"},
            )

    assert status.status_code == 200
    assert status.json() == {
        "state": "recovery_required",
        "backups": [
            {
                "name": recovery.backup.name,
                "local_date": "2026-08-13",
                "kind": "daily",
            }
        ],
        "quarantined": True,
        "history_recovery": "complete",
        "rebuilt_archives": 0,
        "restart_required": False,
    }
    assert restored.json()["restart_required"] is True
    assert rebuilt.json()["history_recovery"] == "incomplete"
    assert rebuilt.json()["rebuilt_archives"] == 2
    assert cross_origin.status_code == 403
    assert recovery.restored == [recovery.backup.name]
    assert recovery.rebuilt == [root]
