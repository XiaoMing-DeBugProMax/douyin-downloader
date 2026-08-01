import threading
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.archive import (
    ArchiveItemSnapshot,
    ArchiveOperationSnapshot,
    SingleArchiveRequest,
    TaskSnapshot,
)
from douyin_downloader.domain import ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.settings import SettingsModule
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices

VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="测试作者",
    description="归档接口测试",
    duration_ms=15_000,
    cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
    media_urls=("https://v95-web.douyinvod.com/video.mp4",),
)


class UnusedResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        raise AssertionError(share_text)


class UnusedParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        raise AssertionError(aweme_id)


class RecordingManagedArchive:
    def __init__(self) -> None:
        self.requests: list[SingleArchiveRequest] = []
        self.items: dict[str, ArchiveItemSnapshot] = {}
        self.opened: list[str] = []
        self.status_thread_ids: list[int] = []
        self.open_thread_ids: list[int] = []

    async def archive_single(
        self,
        request: SingleArchiveRequest,
    ) -> ArchiveOperationSnapshot:
        self.requests.append(request)
        task = TaskSnapshot("task-id", "finished", "idle", "success")
        item = ArchiveItemSnapshot(
            request.aweme_id,
            "archived",
            Path("author-safe/2026/work-7429378937383308594"),
        )
        self.items[request.aweme_id] = item
        return ArchiveOperationSnapshot(
            operation=task,
            source_task=task,
            work_task=task,
            archive_item=item,
            settings=request.settings_snapshot(),
        )

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None:
        self.status_thread_ids.append(threading.get_ident())
        return self.items.get(aweme_id)

    def open_work_folder(self, aweme_id: str) -> None:
        self.open_thread_ids.append(threading.get_ident())
        self.opened.append(aweme_id)


class StaticDirectoryChooser:
    def __init__(self, selected: Path) -> None:
        self._selected = selected

    def choose_directory(self) -> Path:
        return self._selected


@pytest.mark.asyncio
async def test_archive_route_uses_parse_token_without_exposing_paths_or_media_urls(
    tmp_path: Path,
) -> None:
    store = ParseStore()
    parse_token = store.put(VIDEO)
    managed_archive = RecordingManagedArchive()
    settings = SettingsModule(tmp_path / "archive.db")
    event_loop_thread_id = threading.get_ident()
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(UnusedResolver(), UnusedParser(), store),
                media_client=media_client,
                managed_archive=managed_archive,
                settings=settings,
                directory_chooser=StaticDirectoryChooser(tmp_path),
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            response = await client.post(
                "/api/archive/single",
                headers={"origin": "http://testserver"},
                json={"parse_token": parse_token},
            )
            status_response = await client.get(
                f"/api/archive/work/{VIDEO.aweme_id}",
            )
            open_response = await client.post(
                f"/api/archive/work/{VIDEO.aweme_id}/open",
                headers={"origin": "http://testserver"},
            )

    assert response.status_code == 200
    assert response.json() == {
        "operation_id": "task-id",
        "aweme_id": VIDEO.aweme_id,
        "status": "archived",
        "audio_outcome": "not_requested",
        "description_outcome": "not_requested",
        "can_open_folder": True,
    }
    assert managed_archive.requests == [
        SingleArchiveRequest.from_settings(VIDEO.aweme_id, settings.capture())
    ]
    assert str(tmp_path) not in response.text
    assert "douyinvod.com" not in response.text
    assert status_response.json() == {
        "aweme_id": VIDEO.aweme_id,
        "status": "archived",
        "audio_outcome": "not_requested",
        "description_outcome": "not_requested",
        "can_open_folder": True,
    }
    assert open_response.status_code == 204
    assert managed_archive.opened == [VIDEO.aweme_id]
    assert managed_archive.status_thread_ids != [event_loop_thread_id]
    assert managed_archive.open_thread_ids != [event_loop_thread_id]


@pytest.mark.asyncio
async def test_location_unavailable_archive_cannot_open_folder(tmp_path: Path) -> None:
    managed_archive = RecordingManagedArchive()
    managed_archive.items[VIDEO.aweme_id] = ArchiveItemSnapshot(
        VIDEO.aweme_id,
        "location_unavailable",
        tmp_path / "missing",
    )
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(UnusedResolver(), UnusedParser(), ParseStore()),
                media_client=media_client,
                managed_archive=managed_archive,
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            response = await client.get(f"/api/archive/work/{VIDEO.aweme_id}")

    assert response.status_code == 200
    assert response.json() == {
        "aweme_id": VIDEO.aweme_id,
        "status": "location_unavailable",
        "audio_outcome": "not_requested",
        "description_outcome": "not_requested",
        "can_open_folder": False,
    }
