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
)
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


class DirectoryChooser(Protocol):
    def choose_directory(self) -> Path | None: ...


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
    settings: SettingsModuleInterface | None = None
    directory_chooser: DirectoryChooser | None = None


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
    can_open_folder: bool


class ArchiveWorkResponse(BaseModel):
    aweme_id: str
    status: str
    can_open_folder: bool


class ArchiveProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_audio: bool
    include_description: bool


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
        return ArchiveResponse(
            operation_id=result.operation.task_id,
            aweme_id=result.archive_item.aweme_id,
            status=result.archive_item.status,
            can_open_folder=True,
        )

    @router.get(
        "/api/settings",
        response_model=SettingsResponse,
        dependencies=[Depends(require_local_session)],
    )
    async def get_settings(request: Request) -> SettingsResponse:
        current = await asyncio.to_thread(_settings_module(request).current)
        return _settings_response(current)

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

    return router
