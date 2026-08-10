import threading
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.archive import (
    ArchiveItemSnapshot,
    ArchiveOperationSnapshot,
    SingleArchiveRequest,
    TaskCenterOperationSnapshot,
    TaskCenterSourceSnapshot,
    TaskCenterWorkSnapshot,
    TaskErrorSnapshot,
    TaskProgressSnapshot,
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
        self.task_operations: tuple[TaskCenterOperationSnapshot, ...] = ()
        self.cleared_operations: list[str] = []
        self.paused_tasks: list[str] = []
        self.resumed_tasks: list[str] = []
        self.retried_tasks: list[str] = []
        self.cancelled_tasks: list[tuple[str, bool]] = []

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

    def list_task_operations(self) -> tuple[TaskCenterOperationSnapshot, ...]:
        return self.task_operations

    def clear_task_operation(self, operation_id: str) -> None:
        self.cleared_operations.append(operation_id)

    async def pause_task(self, task_id: str) -> TaskCenterOperationSnapshot:
        self.paused_tasks.append(task_id)
        return self.task_operations[0]

    async def resume_task(self, task_id: str) -> TaskCenterOperationSnapshot:
        self.resumed_tasks.append(task_id)
        return self.task_operations[0]

    async def retry_task(self, task_id: str) -> TaskCenterOperationSnapshot:
        self.retried_tasks.append(task_id)
        return self.task_operations[0]

    async def cancel_task(
        self,
        task_id: str,
        *,
        retain_parts: bool,
    ) -> TaskCenterOperationSnapshot:
        self.cancelled_tasks.append((task_id, retain_parts))
        return self.task_operations[0]


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


@pytest.mark.asyncio
async def test_task_routes_project_safe_three_level_history_and_clear_it(
    tmp_path: Path,
) -> None:
    managed_archive = RecordingManagedArchive()
    progress = TaskProgressSnapshot(
        completed_items=1,
        total_items=1,
        completed_bytes=512,
        total_bytes=1024,
        percentage=50.0,
        speed_bytes_per_second=256.0,
        eta_seconds=2,
    )
    error = TaskErrorSnapshot(
        "UPSTREAM_BLOCKED",
        "解析服务暂时不可用。",
        "请稍后重试此归档操作。",
    )
    work = TaskCenterWorkSnapshot(
        TaskSnapshot("work-1", "finished", "idle", "failed", error, progress),
        VIDEO.aweme_id,
    )
    source = TaskCenterSourceSnapshot(
        TaskSnapshot("source-1", "finished", "idle", "failed", error, progress),
        (work,),
    )
    managed_archive.task_operations = (
        TaskCenterOperationSnapshot(
            TaskSnapshot("operation-1", "finished", "idle", "failed", error, progress),
            (source,),
        ),
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
            response = await client.get("/api/tasks")
            clear_response = await client.delete(
                "/api/tasks/operation-1",
                headers={"origin": "http://testserver"},
            )

    assert response.status_code == 200
    assert response.json() == {
        "operations": [
            {
                "task": {
                    "task_id": "operation-1",
                    "lifecycle": "finished",
                    "phase": "idle",
                    "result": "failed",
                    "progress": {
                        "completed_items": 1,
                        "total_items": 1,
                        "completed_bytes": 512,
                        "total_bytes": 1024,
                        "percentage": 50.0,
                        "speed_bytes_per_second": 256.0,
                        "eta_seconds": 2,
                    },
                    "error": {
                        "code": "UPSTREAM_BLOCKED",
                        "message": "解析服务暂时不可用。",
                        "suggestion": "请稍后重试此归档操作。",
                    },
                },
                "source_tasks": [
                    {
                        "task": {
                            "task_id": "source-1",
                            "lifecycle": "finished",
                            "phase": "idle",
                            "result": "failed",
                            "progress": {
                                "completed_items": 1,
                                "total_items": 1,
                                "completed_bytes": 512,
                                "total_bytes": 1024,
                                "percentage": 50.0,
                                "speed_bytes_per_second": 256.0,
                                "eta_seconds": 2,
                            },
                            "error": {
                                "code": "UPSTREAM_BLOCKED",
                                "message": "解析服务暂时不可用。",
                                "suggestion": "请稍后重试此归档操作。",
                            },
                        },
                        "work_tasks": [
                            {
                                "task": {
                                    "task_id": "work-1",
                                    "lifecycle": "finished",
                                    "phase": "idle",
                                    "result": "failed",
                                    "progress": {
                                        "completed_items": 1,
                                        "total_items": 1,
                                        "completed_bytes": 512,
                                        "total_bytes": 1024,
                                        "percentage": 50.0,
                                        "speed_bytes_per_second": 256.0,
                                        "eta_seconds": 2,
                                    },
                                    "error": {
                                        "code": "UPSTREAM_BLOCKED",
                                        "message": "解析服务暂时不可用。",
                                        "suggestion": "请稍后重试此归档操作。",
                                    },
                                },
                                "aweme_id": VIDEO.aweme_id,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert "douyinvod.com" not in response.text
    assert str(tmp_path) not in response.text
    assert clear_response.status_code == 204
    assert managed_archive.cleared_operations == ["operation-1"]


@pytest.mark.asyncio
async def test_task_control_routes_require_origin_and_explicit_cancel_choice(
    tmp_path: Path,
) -> None:
    managed_archive = RecordingManagedArchive()
    progress = TaskProgressSnapshot(completed_items=0, total_items=1)
    work = TaskCenterWorkSnapshot(
        TaskSnapshot("work-1", "running", "downloading", "none", None, progress),
        VIDEO.aweme_id,
    )
    source = TaskCenterSourceSnapshot(
        TaskSnapshot("source-1", "running", "downloading", "none", None, progress),
        (work,),
    )
    managed_archive.task_operations = (
        TaskCenterOperationSnapshot(
            TaskSnapshot(
                "operation-1",
                "running",
                "downloading",
                "none",
                None,
                progress,
            ),
            (source,),
        ),
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
            headers = {"origin": "http://testserver"}
            pause = await client.post("/api/tasks/work-1/pause", headers=headers)
            resume = await client.post("/api/tasks/work-1/resume", headers=headers)
            retry = await client.post("/api/tasks/work-1/retry", headers=headers)
            cancel = await client.post(
                "/api/tasks/work-1/cancel",
                headers=headers,
                json={"retain_parts": True},
            )
            missing_choice = await client.post(
                "/api/tasks/work-1/cancel",
                headers=headers,
                json={},
            )
            cross_origin = await client.post(
                "/api/tasks/work-1/pause",
                headers={"origin": "https://example.com"},
            )

    assert pause.status_code == 200
    assert pause.json()["task"]["task_id"] == "operation-1"
    assert resume.status_code == 200
    assert retry.status_code == 200
    assert cancel.status_code == 200
    assert missing_choice.status_code == 400
    assert cross_origin.status_code == 403
    assert managed_archive.paused_tasks == ["work-1"]
    assert managed_archive.resumed_tasks == ["work-1"]
    assert managed_archive.retried_tasks == ["work-1"]
    assert managed_archive.cancelled_tasks == [("work-1", True)]
    assert "douyinvod.com" not in cancel.text
    assert str(tmp_path) not in cancel.text
