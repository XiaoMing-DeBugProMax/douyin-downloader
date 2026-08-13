import threading
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.archive import (
    ArchiveArtifactSnapshot,
    ArchiveItemSnapshot,
    ArchiveOperationSnapshot,
    SingleArchiveRequest,
    TaskCenterOperationSnapshot,
    TaskCenterSourceSnapshot,
    TaskCenterWorkSnapshot,
    TaskErrorSnapshot,
    TaskProgressSnapshot,
    TaskSnapshot,
    WorkArchiveSnapshot,
)
from douyin_downloader.domain import AppError, ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.settings import ArchiveProfile, SettingsModule
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
        self.library_items: tuple[WorkArchiveSnapshot, ...] = ()
        self.supplements: list[tuple[str, bool, bool]] = []
        self.repairs: list[str] = []
        self.forced: list[tuple[str, bool]] = []
        self.relocations: list[tuple[str, Path]] = []
        self.deletions: list[tuple[str, bool]] = []

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

    def get_work(self, aweme_id: str) -> WorkArchiveSnapshot | None:
        return next((item for item in self.library_items if item.aweme_id == aweme_id), None)

    def list_works(self) -> tuple[WorkArchiveSnapshot, ...]:
        return self.library_items

    async def supplement(
        self,
        aweme_id: str,
        *,
        include_audio: bool,
        include_description: bool,
    ) -> ArchiveOperationSnapshot:
        self.supplements.append((aweme_id, include_audio, include_description))
        return self._action_result(aweme_id)

    async def repair(self, aweme_id: str) -> ArchiveOperationSnapshot:
        self.repairs.append(aweme_id)
        return self._action_result(aweme_id)

    async def force_rearchive(
        self,
        aweme_id: str,
        *,
        confirm_overwrite: bool,
    ) -> ArchiveOperationSnapshot:
        self.forced.append((aweme_id, confirm_overwrite))
        return self._action_result(aweme_id)

    def relocate(self, aweme_id: str, archive_root: Path) -> WorkArchiveSnapshot:
        self.relocations.append((aweme_id, archive_root))
        item = self.get_work(aweme_id)
        assert item is not None
        return WorkArchiveSnapshot(
            aweme_id=item.aweme_id,
            author=item.author,
            published_at=item.published_at,
            profile=item.profile,
            root=archive_root,
            relative_directory=item.relative_directory,
            status="archived",
            artifacts=item.artifacts,
        )

    def delete(self, aweme_id: str, *, confirm_recycle: bool) -> None:
        if not confirm_recycle:
            raise AppError(
                "ARCHIVE_DELETE_CONFIRMATION_REQUIRED",
                "confirmation required",
                409,
            )
        self.deletions.append((aweme_id, confirm_recycle))

    def _action_result(self, aweme_id: str) -> ArchiveOperationSnapshot:
        task = TaskSnapshot("library-action", "finished", "idle", "success")
        item = ArchiveItemSnapshot(aweme_id, "archived", Path("author/year/work"))
        return ArchiveOperationSnapshot(
            task,
            task,
            task,
            item,
            SingleArchiveRequest(aweme_id, Path("C:/library")).settings_snapshot(),
        )

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
async def test_library_routes_list_detail_and_dispatch_explicit_actions(
    tmp_path: Path,
) -> None:
    managed_archive = RecordingManagedArchive()
    item = WorkArchiveSnapshot(
        aweme_id=VIDEO.aweme_id,
        author="测试作者",
        published_at=1_720_000_000,
        profile=ArchiveProfile(),
        root=tmp_path / "library",
        relative_directory=Path("author/2024/work-7429378937383308594"),
        status="archived",
        artifacts=(
            ArchiveArtifactSnapshot(
                "video",
                Path("7429378937383308594.mp4"),
                1024,
                "video/mp4",
                "a" * 64,
                "valid",
            ),
        ),
    )
    managed_archive.library_items = (item,)
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(UnusedResolver(), UnusedParser(), ParseStore()),
                media_client=media_client,
                managed_archive=managed_archive,
                archive_library=managed_archive,
                directory_chooser=StaticDirectoryChooser(tmp_path / "relocated"),
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
            listing = await client.get("/api/library")
            detail = await client.get(f"/api/library/{VIDEO.aweme_id}")
            supplement = await client.post(
                f"/api/library/{VIDEO.aweme_id}/supplement",
                headers=headers,
                json={"include_audio": True, "include_description": False},
            )
            repair = await client.post(
                f"/api/library/{VIDEO.aweme_id}/repair",
                headers=headers,
            )
            force = await client.post(
                f"/api/library/{VIDEO.aweme_id}/force",
                headers=headers,
                json={"confirm_overwrite": True},
            )
            missing_confirmation = await client.post(
                f"/api/library/{VIDEO.aweme_id}/force",
                headers=headers,
                json={},
            )
            cross_origin = await client.post(
                f"/api/library/{VIDEO.aweme_id}/repair",
                headers={"origin": "https://example.com"},
            )
            (tmp_path / "relocated").mkdir()
            relocate = await client.post(
                f"/api/library/{VIDEO.aweme_id}/relocate",
                headers=headers,
            )
            delete_without_confirmation = await client.request(
                "DELETE",
                f"/api/library/{VIDEO.aweme_id}",
                headers=headers,
                json={"confirm_recycle": False},
            )
            deleted = await client.request(
                "DELETE",
                f"/api/library/{VIDEO.aweme_id}",
                headers=headers,
                json={"confirm_recycle": True},
            )

    assert listing.status_code == 200
    assert listing.json()["items"] == [detail.json()]
    assert detail.json() == {
        "aweme_id": VIDEO.aweme_id,
        "author": "测试作者",
        "published_at": 1_720_000_000,
        "profile": {"include_audio": False, "include_description": False},
        "root": str(tmp_path / "library"),
        "relative_directory": "author\\2024\\work-7429378937383308594",
        "status": "archived",
        "artifacts": [
            {
                "kind": "video",
                "relative_path": "7429378937383308594.mp4",
                "size_bytes": 1024,
                "mime_type": "video/mp4",
                "sha256": "a" * 64,
                "integrity": "valid",
            }
        ],
    }
    assert supplement.status_code == repair.status_code == force.status_code == 200
    assert missing_confirmation.status_code == 400
    assert cross_origin.status_code == 403
    assert relocate.status_code == 200
    assert relocate.json()["root"] == str(tmp_path / "relocated")
    assert delete_without_confirmation.status_code == 409
    assert deleted.status_code == 204
    assert managed_archive.supplements == [(VIDEO.aweme_id, True, False)]
    assert managed_archive.repairs == [VIDEO.aweme_id]
    assert managed_archive.forced == [(VIDEO.aweme_id, True)]
    assert managed_archive.relocations == [(VIDEO.aweme_id, tmp_path / "relocated")]
    assert managed_archive.deletions == [(VIDEO.aweme_id, True)]


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
