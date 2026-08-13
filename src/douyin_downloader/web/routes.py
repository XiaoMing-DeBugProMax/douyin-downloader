import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response, StreamingResponse

from douyin_downloader.archive import (
    ArchiveItemSnapshot,
    ArchiveOperationSnapshot,
    SingleArchiveRequest,
    TaskCenterOperationSnapshot,
    WorkArchiveSnapshot,
)
from douyin_downloader.database_recovery import DatabaseRecoveryStatus
from douyin_downloader.domain import AppError
from douyin_downloader.media import UpstreamStream, open_first_available, safe_video_filename
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager, require_local_session, require_same_origin
from douyin_downloader.settings import (
    ArchiveProfile,
    CurrentSettings,
    NamingTemplate,
    OperationSettingsSnapshot,
    SettingsUpdate,
)


class ManagedArchiveModule(Protocol):
    async def archive_single(
        self,
        request: SingleArchiveRequest,
    ) -> ArchiveOperationSnapshot: ...

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None: ...

    def open_work_folder(self, aweme_id: str) -> None: ...

    def list_task_operations(self) -> tuple[TaskCenterOperationSnapshot, ...]: ...

    def clear_task_operation(self, operation_id: str) -> None: ...

    async def pause_task(self, task_id: str) -> TaskCenterOperationSnapshot: ...

    async def resume_task(self, task_id: str) -> TaskCenterOperationSnapshot: ...

    async def retry_task(self, task_id: str) -> TaskCenterOperationSnapshot: ...

    async def cancel_task(
        self,
        task_id: str,
        *,
        retain_parts: bool,
    ) -> TaskCenterOperationSnapshot: ...


class ArchiveLibraryModule(Protocol):
    def get_work(self, aweme_id: str) -> WorkArchiveSnapshot | None: ...

    def list_works(self) -> tuple[WorkArchiveSnapshot, ...]: ...

    async def supplement(
        self,
        aweme_id: str,
        *,
        include_audio: bool,
        include_description: bool,
    ) -> ArchiveOperationSnapshot: ...

    async def repair(self, aweme_id: str) -> ArchiveOperationSnapshot: ...

    async def force_rearchive(
        self,
        aweme_id: str,
        *,
        confirm_overwrite: bool,
    ) -> ArchiveOperationSnapshot: ...

    def relocate(self, aweme_id: str, archive_root: Path) -> WorkArchiveSnapshot: ...

    def delete(self, aweme_id: str, *, confirm_recycle: bool) -> None: ...


class DirectoryChooser(Protocol):
    def choose_directory(self) -> Path | None: ...


class DatabaseRecoveryModule(Protocol):
    def status(self) -> DatabaseRecoveryStatus: ...

    def restore(self, backup_name: str) -> DatabaseRecoveryStatus: ...

    def rebuild_from_metadata(self, root: Path) -> DatabaseRecoveryStatus: ...


class SettingsModuleInterface(Protocol):
    def current(self) -> CurrentSettings: ...

    def update(self, changes: SettingsUpdate) -> CurrentSettings: ...

    def set_archive_root(self, archive_root: Path) -> CurrentSettings: ...

    def capture(self) -> OperationSettingsSnapshot: ...


@dataclass(slots=True)
class AppServices:
    parse_service: ParseService
    media_client: httpx.AsyncClient
    managed_archive: ManagedArchiveModule | None = None
    archive_library: ArchiveLibraryModule | None = None
    settings: SettingsModuleInterface | None = None
    directory_chooser: DirectoryChooser | None = None
    database_recovery: DatabaseRecoveryModule | None = None


class ParseRequest(BaseModel):
    share_text: str = Field(min_length=1, max_length=2000)


class VideoResponse(BaseModel):
    aweme_id: str
    author: str
    description: str
    duration_ms: int
    cover_url: str
    suggested_filename: str


class ParseResponse(BaseModel):
    parse_token: str
    video: VideoResponse


class ArchiveRequest(BaseModel):
    parse_token: str = Field(min_length=1, max_length=200)


class ArchiveResponse(BaseModel):
    operation_id: str
    aweme_id: str
    status: str
    audio_outcome: str
    description_outcome: str
    can_open_folder: bool


class ArchiveProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_audio: bool
    include_description: bool


class ArchiveWorkResponse(BaseModel):
    aweme_id: str
    status: str
    audio_outcome: str
    description_outcome: str
    can_open_folder: bool


class LibraryArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    relative_path: str
    size_bytes: int
    mime_type: str
    sha256: str
    integrity: str


class LibraryWorkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    aweme_id: str
    author: str | None
    published_at: int | None
    profile: ArchiveProfileModel
    root: str
    relative_directory: str
    status: str
    artifacts: list[LibraryArtifactResponse]


class LibraryWorksResponse(BaseModel):
    items: list[LibraryWorkResponse]


class SupplementArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_audio: bool = False
    include_description: bool = False


class ForceRearchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_overwrite: bool


class DeleteArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_recycle: bool


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    naming_template: str
    profile: ArchiveProfileModel
    download_concurrency: int
    retry_limit: int


class SettingsResponse(BaseModel):
    archive_root: str | None
    naming_template: str
    profile: ArchiveProfileModel
    download_concurrency: int
    retry_limit: int


class RestoreDatabaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_name: str = Field(min_length=1, max_length=200)


class RecoveryBackupResponse(BaseModel):
    name: str
    local_date: str
    kind: str


class RecoveryStatusResponse(BaseModel):
    state: str
    backups: list[RecoveryBackupResponse]
    quarantined: bool
    history_recovery: str
    rebuilt_archives: int
    restart_required: bool


class TaskErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    suggestion: str


class TaskProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    completed_items: int
    total_items: int
    completed_bytes: int
    total_bytes: int | None
    percentage: float | None
    speed_bytes_per_second: float | None
    eta_seconds: int | None


class TaskNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    lifecycle: str
    phase: str
    result: str
    progress: TaskProgressResponse
    error: TaskErrorResponse | None


class WorkTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task: TaskNodeResponse
    aweme_id: str


class SourceTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task: TaskNodeResponse
    work_tasks: list[WorkTaskResponse]


class TaskOperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task: TaskNodeResponse
    source_tasks: list[SourceTaskResponse]


class TaskOperationsResponse(BaseModel):
    operations: list[TaskOperationResponse]


class CancelTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retain_parts: bool


def content_disposition_filename(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii")
    if not ascii_fallback.removesuffix(".mp4").strip(" .-"):
        ascii_fallback = "video.mp4"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _services(request: Request) -> AppServices:
    services: AppServices | None = request.app.state.services
    if services is None:
        raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用，请稍后重试。", 502)
    return services


def _managed_archive(request: Request) -> ManagedArchiveModule:
    archive = _services(request).managed_archive
    if archive is None:
        raise AppError("ARCHIVE_UNAVAILABLE", "本地归档暂时不可用，快速下载仍可使用。", 503)
    return archive


def _archive_library(request: Request) -> ArchiveLibraryModule:
    library = _services(request).archive_library
    if library is None:
        raise AppError("ARCHIVE_UNAVAILABLE", "本地档案库暂时不可用。", 503)
    return library


def _settings_module(request: Request) -> SettingsModuleInterface:
    settings = _services(request).settings
    if settings is None:
        raise AppError("SETTINGS_UNAVAILABLE", "设置暂时不可用。", 503)
    return settings


def _settings_response(settings: CurrentSettings) -> SettingsResponse:
    return SettingsResponse(
        archive_root=str(settings.archive_root) if settings.archive_root is not None else None,
        naming_template=settings.naming_template,
        profile=ArchiveProfileModel(
            include_audio=settings.profile.include_audio,
            include_description=settings.profile.include_description,
        ),
        download_concurrency=settings.download_concurrency,
        retry_limit=settings.retry_limit,
    )


def _database_recovery(request: Request) -> DatabaseRecoveryModule:
    recovery = _services(request).database_recovery
    if recovery is None:
        raise AppError("DATABASE_RECOVERY_UNAVAILABLE", "数据库恢复暂时不可用。", 503)
    return recovery


def _recovery_response(
    status: DatabaseRecoveryStatus,
    *,
    restart_required: bool = False,
) -> RecoveryStatusResponse:
    return RecoveryStatusResponse(
        state=status.state,
        backups=[
            RecoveryBackupResponse(
                name=backup.name,
                local_date=backup.local_date.isoformat(),
                kind=backup.kind,
            )
            for backup in status.backups
        ],
        quarantined=status.quarantined_path is not None,
        history_recovery=status.history_recovery,
        rebuilt_archives=status.rebuilt_archives,
        restart_required=restart_required,
    )


def _archive_response(result: ArchiveOperationSnapshot) -> ArchiveResponse:
    return ArchiveResponse(
        operation_id=result.operation.task_id,
        aweme_id=result.archive_item.aweme_id,
        status=result.archive_item.status,
        audio_outcome=result.archive_item.audio_outcome,
        description_outcome=result.archive_item.description_outcome,
        can_open_folder=True,
    )


def _library_work_response(item: WorkArchiveSnapshot) -> LibraryWorkResponse:
    return LibraryWorkResponse(
        aweme_id=item.aweme_id,
        author=item.author,
        published_at=item.published_at,
        profile=ArchiveProfileModel(
            include_audio=item.profile.include_audio,
            include_description=item.profile.include_description,
        ),
        root=str(item.root),
        relative_directory=str(item.relative_directory),
        status=item.status,
        artifacts=[
            LibraryArtifactResponse(
                kind=artifact.kind,
                relative_path=str(artifact.relative_path),
                size_bytes=artifact.size_bytes,
                mime_type=artifact.mime_type,
                sha256=artifact.sha256,
                integrity=artifact.integrity,
            )
            for artifact in item.artifacts
        ],
    )


def streaming_cover_response(upstream: UpstreamStream) -> StreamingResponse:
    return StreamingResponse(
        upstream.iter_bytes(),
        media_type=upstream.content_type,
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/launch-token")
    async def issue_launch_token(request: Request) -> dict[str, str]:
        supplied = request.headers.get("x-management-token")
        sessions: SessionManager = request.app.state.session_manager
        if supplied is None or not secrets.compare_digest(supplied, sessions.management_token):
            raise HTTPException(status_code=403)
        return {"launch_token": sessions.issue_launch_token()}

    @router.post(
        "/api/parse",
        response_model=ParseResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def parse_video(payload: ParseRequest, request: Request) -> ParseResponse:
        services = _services(request)
        result = await services.parse_service.parse(payload.share_text)
        return ParseResponse(
            parse_token=result.parse_token,
            video=VideoResponse(
                aweme_id=result.video.aweme_id,
                author=result.video.author,
                description=result.video.description,
                duration_ms=result.video.duration_ms,
                cover_url=f"/api/cover/{result.parse_token}",
                suggested_filename=safe_video_filename(result.video),
            ),
        )

    @router.get(
        "/api/cover/{parse_token}",
        dependencies=[Depends(require_local_session)],
    )
    async def cover(parse_token: str, request: Request) -> StreamingResponse:
        services = _services(request)
        video = services.parse_service.store.get(parse_token)
        upstream = await open_first_available(services.media_client, video.cover_urls, "cover")
        return streaming_cover_response(upstream)

    @router.get(
        "/api/download/{parse_token}",
        dependencies=[Depends(require_local_session)],
    )
    async def download(parse_token: str, request: Request) -> StreamingResponse:
        services = _services(request)
        video = services.parse_service.store.get(parse_token)
        upstream = await open_first_available(services.media_client, video.media_urls, "video")
        return StreamingResponse(
            upstream.iter_bytes(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": content_disposition_filename(safe_video_filename(video)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post(
        "/api/archive/single",
        response_model=ArchiveResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def archive_single(
        payload: ArchiveRequest,
        request: Request,
    ) -> ArchiveResponse:
        services = _services(request)
        video = services.parse_service.store.get(payload.parse_token)
        archive = _managed_archive(request)
        settings_module = _settings_module(request)
        try:
            settings = await asyncio.to_thread(settings_module.capture)
        except AppError as error:
            if error.code != "ARCHIVE_ROOT_REQUIRED":
                raise
            if services.directory_chooser is None:
                raise AppError(
                    "ARCHIVE_UNAVAILABLE",
                    "本地归档暂时不可用，快速下载仍可使用。",
                    503,
                ) from error
            archive_root = await asyncio.to_thread(
                services.directory_chooser.choose_directory
            )
            if archive_root is None:
                raise AppError(
                    "ARCHIVE_SELECTION_CANCELLED",
                    "已取消选择归档目录。",
                    409,
                ) from error
            await asyncio.to_thread(settings_module.set_archive_root, archive_root)
            settings = await asyncio.to_thread(settings_module.capture)
        result = await archive.archive_single(
            SingleArchiveRequest.from_settings(video.aweme_id, settings)
        )
        return _archive_response(result)

    @router.get(
        "/api/library",
        response_model=LibraryWorksResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def list_library(request: Request) -> LibraryWorksResponse:
        items = await asyncio.to_thread(_archive_library(request).list_works)
        return LibraryWorksResponse(
            items=[_library_work_response(item) for item in items]
        )

    @router.get(
        "/api/library/{aweme_id}",
        response_model=LibraryWorkResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def library_detail(aweme_id: str, request: Request) -> LibraryWorkResponse:
        if not aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        item = await asyncio.to_thread(
            _archive_library(request).get_work,
            aweme_id,
        )
        if item is None:
            raise AppError("ARCHIVE_NOT_FOUND", "没有找到该作品的本地档案。", 404)
        return _library_work_response(item)

    @router.post(
        "/api/library/{aweme_id}/supplement",
        response_model=ArchiveResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def supplement_library_work(
        aweme_id: str,
        payload: SupplementArchiveRequest,
        request: Request,
    ) -> ArchiveResponse:
        result = await _archive_library(request).supplement(
            aweme_id,
            include_audio=payload.include_audio,
            include_description=payload.include_description,
        )
        return _archive_response(result)

    @router.post(
        "/api/library/{aweme_id}/repair",
        response_model=ArchiveResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def repair_library_work(aweme_id: str, request: Request) -> ArchiveResponse:
        result = await _archive_library(request).repair(aweme_id)
        return _archive_response(result)

    @router.post(
        "/api/library/{aweme_id}/force",
        response_model=ArchiveResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def force_library_work(
        aweme_id: str,
        payload: ForceRearchiveRequest,
        request: Request,
    ) -> ArchiveResponse:
        result = await _archive_library(request).force_rearchive(
            aweme_id,
            confirm_overwrite=payload.confirm_overwrite,
        )
        return _archive_response(result)

    @router.post(
        "/api/library/{aweme_id}/relocate",
        response_model=LibraryWorkResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def relocate_library_work(
        aweme_id: str,
        request: Request,
    ) -> LibraryWorkResponse:
        if not aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        services: AppServices = request.app.state.services
        if services.directory_chooser is None:
            raise AppError("ARCHIVE_UNAVAILABLE", "本地档案暂时不可用。", 503)
        archive_root = await asyncio.to_thread(
            services.directory_chooser.choose_directory
        )
        if archive_root is None:
            raise AppError(
                "ARCHIVE_SELECTION_CANCELLED",
                "已取消选择档案目录。",
                409,
            )
        item = await asyncio.to_thread(
            _archive_library(request).relocate,
            aweme_id,
            archive_root,
        )
        return _library_work_response(item)

    @router.delete(
        "/api/library/{aweme_id}",
        status_code=204,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def delete_library_work(
        aweme_id: str,
        payload: DeleteArchiveRequest,
        request: Request,
    ) -> Response:
        await asyncio.to_thread(
            _archive_library(request).delete,
            aweme_id,
            confirm_recycle=payload.confirm_recycle,
        )
        return Response(status_code=204)

    @router.get(
        "/api/settings",
        response_model=SettingsResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def get_settings(request: Request) -> SettingsResponse:
        current = await asyncio.to_thread(_settings_module(request).current)
        return _settings_response(current)

    @router.get(
        "/api/recovery",
        response_model=RecoveryStatusResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def get_recovery_status(request: Request) -> RecoveryStatusResponse:
        status = await asyncio.to_thread(_database_recovery(request).status)
        return _recovery_response(status)

    @router.post(
        "/api/recovery/restore",
        response_model=RecoveryStatusResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def restore_database(
        payload: RestoreDatabaseRequest,
        request: Request,
    ) -> RecoveryStatusResponse:
        status = await asyncio.to_thread(
            _database_recovery(request).restore,
            payload.backup_name,
        )
        return _recovery_response(status, restart_required=True)

    @router.post(
        "/api/recovery/rebuild",
        response_model=RecoveryStatusResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def rebuild_database(request: Request) -> RecoveryStatusResponse:
        services = _services(request)
        if services.directory_chooser is None:
            raise AppError("DATABASE_RECOVERY_UNAVAILABLE", "数据库恢复暂时不可用。", 503)
        selected = await asyncio.to_thread(services.directory_chooser.choose_directory)
        if selected is None:
            raise AppError("ARCHIVE_SELECTION_CANCELLED", "已取消选择归档目录。", 409)
        status = await asyncio.to_thread(
            _database_recovery(request).rebuild_from_metadata,
            selected,
        )
        return _recovery_response(status, restart_required=True)

    @router.put(
        "/api/settings",
        response_model=SettingsResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def update_settings(
        payload: SettingsUpdateRequest,
        request: Request,
    ) -> SettingsResponse:
        updated = await asyncio.to_thread(
            _settings_module(request).update,
            SettingsUpdate(
                naming_template=NamingTemplate(payload.naming_template),
                profile=ArchiveProfile(
                    include_audio=payload.profile.include_audio,
                    include_description=payload.profile.include_description,
                ),
                download_concurrency=payload.download_concurrency,
                retry_limit=payload.retry_limit,
            ),
        )
        return _settings_response(updated)

    @router.post(
        "/api/settings/archive-root/select",
        response_model=SettingsResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def select_settings_archive_root(request: Request) -> SettingsResponse:
        services = _services(request)
        if services.directory_chooser is None:
            raise AppError("SETTINGS_UNAVAILABLE", "设置暂时不可用。", 503)
        selected = await asyncio.to_thread(services.directory_chooser.choose_directory)
        if selected is None:
            raise AppError(
                "ARCHIVE_SELECTION_CANCELLED",
                "已取消选择归档目录。",
                409,
            )
        updated = await asyncio.to_thread(
            _settings_module(request).set_archive_root,
            selected,
        )
        return _settings_response(updated)

    @router.get(
        "/api/archive/work/{aweme_id}",
        response_model=ArchiveWorkResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def archive_work_status(
        aweme_id: str,
        request: Request,
    ) -> ArchiveWorkResponse:
        if not aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        item = await asyncio.to_thread(
            _managed_archive(request).get_work_archive,
            aweme_id,
        )
        return ArchiveWorkResponse(
            aweme_id=aweme_id,
            status=item.status if item is not None else "not_archived",
            audio_outcome=(
                item.audio_outcome if item is not None else "not_requested"
            ),
            description_outcome=(
                item.description_outcome if item is not None else "not_requested"
            ),
            can_open_folder=(
                item is not None and item.status != "location_unavailable"
            ),
        )

    @router.post(
        "/api/archive/work/{aweme_id}/open",
        status_code=204,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def open_archive_work_folder(
        aweme_id: str,
        request: Request,
    ) -> Response:
        if not aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        await asyncio.to_thread(
            _managed_archive(request).open_work_folder,
            aweme_id,
        )
        return Response(status_code=204)

    @router.get(
        "/api/tasks",
        response_model=TaskOperationsResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def list_task_operations(request: Request) -> TaskOperationsResponse:
        operations = await asyncio.to_thread(
            _managed_archive(request).list_task_operations
        )
        return TaskOperationsResponse(
            operations=[
                TaskOperationResponse.model_validate(operation)
                for operation in operations
            ]
        )

    @router.delete(
        "/api/tasks/{operation_id}",
        status_code=204,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def clear_task_operation(operation_id: str, request: Request) -> Response:
        if not operation_id or len(operation_id) > 200:
            raise AppError("INVALID_INPUT", "任务标识无效。", 400)
        await asyncio.to_thread(
            _managed_archive(request).clear_task_operation,
            operation_id,
        )
        return Response(status_code=204)

    @router.post(
        "/api/tasks/{task_id}/pause",
        response_model=TaskOperationResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def pause_task(task_id: str, request: Request) -> TaskOperationResponse:
        _validate_task_id(task_id)
        operation = await _managed_archive(request).pause_task(task_id)
        return TaskOperationResponse.model_validate(operation)

    @router.post(
        "/api/tasks/{task_id}/resume",
        response_model=TaskOperationResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def resume_task(task_id: str, request: Request) -> TaskOperationResponse:
        _validate_task_id(task_id)
        operation = await _managed_archive(request).resume_task(task_id)
        return TaskOperationResponse.model_validate(operation)

    @router.post(
        "/api/tasks/{task_id}/retry",
        response_model=TaskOperationResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def retry_task(task_id: str, request: Request) -> TaskOperationResponse:
        _validate_task_id(task_id)
        operation = await _managed_archive(request).retry_task(task_id)
        return TaskOperationResponse.model_validate(operation)

    @router.post(
        "/api/tasks/{task_id}/cancel",
        response_model=TaskOperationResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def cancel_task(
        task_id: str,
        payload: CancelTaskRequest,
        request: Request,
    ) -> TaskOperationResponse:
        _validate_task_id(task_id)
        operation = await _managed_archive(request).cancel_task(
            task_id,
            retain_parts=payload.retain_parts,
        )
        return TaskOperationResponse.model_validate(operation)

    return router


def _validate_task_id(task_id: str) -> None:
    if not task_id or len(task_id) > 200:
        raise AppError("INVALID_INPUT", "任务标识无效。", 400)
