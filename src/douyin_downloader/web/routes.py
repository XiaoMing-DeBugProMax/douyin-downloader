import secrets
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from douyin_downloader.domain import AppError
from douyin_downloader.media import UpstreamStream, open_first_available, safe_video_filename
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager, require_local_session, require_same_origin


@dataclass(slots=True)
class AppServices:
    parse_service: ParseService
    media_client: httpx.AsyncClient


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

    return router
